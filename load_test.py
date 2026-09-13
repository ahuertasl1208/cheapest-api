#!/usr/bin/env python3
"""
Pruebas de carga contra el backend monolitico de Cheapest (NestJS).

Escenarios > 450 threads, donde JMeter deja de ser confiable.

Instalacion:
    pip3 install aiohttp matplotlib

Uso:
    python3 load_test.py --endpoint GET  --users 1500 --ramp-up 75  --duration 60 --out-prefix alta_c1
    python3 load_test.py --endpoint POST --users 3000 --ramp-up 100 --duration 60 --out-prefix muyalta_c1

Modelo temporal:
    Los primeros --ramp-up segundos la concurrencia sube linealmente de 0 a --users.
    Despues se SOSTIENE en --users durante --duration segundos.
    Duracion total del proceso = ramp-up + duration.
    Las metricas se reportan dos veces: sobre toda la corrida y solo sobre la
    ventana sostenida (steady state), que es la comparable con los ASRs.
"""

import argparse
import asyncio
import csv
import json
import os
import random
import resource
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

try:
    import aiohttp
except ImportError:
    sys.exit("Falta aiohttp. Instale con: pip3 install aiohttp")

# --------------------------------------------------------------------------
# Configuracion fija del laboratorio (IDs sembrados por seed.sql)
# --------------------------------------------------------------------------
BASE_URL = "http://localhost:3000"
TIENDA_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
MONEDA_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
PRODUCTO_FALLBACK = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ZONA = "Zona Norte"

GET_PATH = "/logistics/tenderos/productos-disponibles"
POST_PATH = "/logistics/pedidos"

REQUEST_TIMEOUT = 30      # segundos
ITEMS_POR_PEDIDO = 25     # > 20 segun el enunciado


# --------------------------------------------------------------------------
# Construccion de requests
# --------------------------------------------------------------------------
def build_get_url():
    # urlencode codifica el espacio de "Zona Norte"; sin esto el backend
    # responde 400 y se mediria un error de cliente, no de saturacion.
    return f"{BASE_URL}{GET_PATH}?" + urlencode({"tiendaId": TIENDA_ID, "zona": ZONA})


def load_productos(path="productos.txt"):
    if os.path.exists(path):
        with open(path) as fh:
            ids = [line.strip() for line in fh if line.strip()]
        if ids:
            print(f"[info] {len(ids)} productoId cargados desde {path}")
            return ids
    print(f"[warn] {path} no encontrado o vacio. Usando UUID de fallback.")
    print("[warn] Si el POST devuelve 400/500, extraiga UUIDs reales con:")
    print('[warn]   docker exec -it cheapest-postgres psql -U postgres -d cheapest '
          '-t -A -c "select id from productos limit 30;" > productos.txt')
    return [PRODUCTO_FALLBACK]


def build_post_body(productos):
    """Cada llamada genera un identificador unico: si se repitiera, la BD
    devolveria error de clave duplicada y el Error % mediria colisiones de
    datos en lugar de saturacion del sistema."""
    items = []
    total = 0.0
    for _ in range(ITEMS_POR_PEDIDO):
        precio = round(random.uniform(1000, 20000), 2)
        cantidad = random.randint(1, 10)
        descuento = round(random.uniform(0, 500), 2)
        total += precio * cantidad - descuento
        items.append({
            "productoId": random.choice(productos),
            "cantidad": cantidad,
            "precioUnitario": precio,
            "descuento": descuento,
            "monedaId": MONEDA_ID,
        })
    return {
        "identificador": f"PED-{uuid.uuid4()}"[:100],
        "tiendaId": TIENDA_ID,
        "fechaHoraCreacion": datetime.now(timezone.utc).isoformat(),
        "montoTotal": round(total, 2),
        "monedaId": MONEDA_ID,
        "estado": "creado",
        "items": items,
    }


# --------------------------------------------------------------------------
# Motor de carga
# --------------------------------------------------------------------------
async def send_one(session, method, url, body, results, steady_at):
    started = time.perf_counter()
    ts = datetime.now(timezone.utc).isoformat()
    status, error = 0, ""
    try:
        if method == "GET":
            async with session.get(url) as resp:
                await resp.read()
                status = resp.status
        else:
            async with session.post(url, json=body) as resp:
                await resp.read()
                status = resp.status
        if status >= 400:
            error = f"http_{status}"
    except asyncio.TimeoutError:
        error = "timeout"
    except aiohttp.ClientConnectorError:
        error = "connection_error"
    except aiohttp.ClientError as exc:
        error = f"client_error:{type(exc).__name__}"
    except Exception as exc:  # noqa: BLE001
        error = f"other:{type(exc).__name__}"

    latency_ms = (time.perf_counter() - started) * 1000
    results.append({
        "timestamp_iso": ts,
        "status_code": status,
        "latency_ms": round(latency_ms, 2),
        "error": error,
        "steady": time.perf_counter() >= steady_at,
    })


async def worker(idx, session, args, productos, results, t0, steady_at, deadline):
    # Arranque escalonado: reparte los --users a lo largo del ramp-up.
    delay = (args.ramp_up * idx / args.users) if args.users else 0
    await asyncio.sleep(max(0.0, (t0 + delay) - time.perf_counter()))

    url = build_get_url() if args.endpoint == "GET" else f"{BASE_URL}{POST_PATH}"
    while time.perf_counter() < deadline:
        body = build_post_body(productos) if args.endpoint == "POST" else None
        await send_one(session, args.endpoint, url, body, results, steady_at)


async def run(args, productos):
    results = []
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    connector = aiohttp.TCPConnector(limit=args.conn_limit, limit_per_host=args.conn_limit)

    t0 = time.perf_counter()
    steady_at = t0 + args.ramp_up
    deadline = steady_at + args.duration

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        tasks = [
            asyncio.create_task(
                worker(i, session, args, productos, results, t0, steady_at, deadline)
            )
            for i in range(args.users)
        ]
        # Progreso simple para no quedarse mirando una terminal muda.
        async def ticker():
            while time.perf_counter() < deadline:
                await asyncio.sleep(10)
                elapsed = time.perf_counter() - t0
                fase = "ramp-up" if elapsed < args.ramp_up else "sostenido"
                print(f"  [{elapsed:6.1f}s] {fase:9s} requests={len(results)}")
        tick = asyncio.create_task(ticker())
        await asyncio.gather(*tasks, return_exceptions=True)
        tick.cancel()

    return results, (time.perf_counter() - t0)


# --------------------------------------------------------------------------
# Metricas y salida
# --------------------------------------------------------------------------
def percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(pct / 100 * len(ordered) + 0.5)) - 1))
    return ordered[k]


def summarize(rows, window_seconds, label):
    if not rows:
        print(f"\n--- {label}: sin datos ---")
        return None
    lat = [r["latency_ms"] for r in rows]
    errores = [r for r in rows if r["error"]]
    total = len(rows)

    tipos = {}
    for r in errores:
        clave = r["error"].split(":")[0]
        tipos[clave] = tipos.get(clave, 0) + 1

    stats = {
        "total": total,
        "throughput": total / window_seconds if window_seconds else 0,
        "avg": statistics.mean(lat),
        "p95": percentile(lat, 95),
        "p99": percentile(lat, 99),
        "error_pct": len(errores) / total * 100,
        "tipos": tipos,
    }

    print(f"\n--- {label} ---")
    print(f"  Requests totales : {stats['total']}")
    print(f"  Throughput       : {stats['throughput']:.2f} req/s")
    print(f"  Latencia promedio: {stats['avg']:.2f} ms")
    print(f"  Latencia p95     : {stats['p95']:.2f} ms")
    print(f"  Latencia p99     : {stats['p99']:.2f} ms")
    print(f"  Error %          : {stats['error_pct']:.2f} %")
    if tipos:
        print("  Desglose de errores:")
        for k, v in sorted(tipos.items(), key=lambda x: -x[1]):
            print(f"    - {k:20s} {v}")
    return stats


def write_csv(rows, path):
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["timestamp_iso", "status_code", "latency_ms", "error"])
        for r in rows:
            writer.writerow([r["timestamp_iso"], r["status_code"], r["latency_ms"], r["error"]])
    print(f"[ok] CSV escrito: {path}")


def make_plots(rows, prefix, endpoint):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("[warn] matplotlib no instalado; se omiten graficos.")
        return

    base = datetime.fromisoformat(rows[0]["timestamp_iso"])
    segundos = [(datetime.fromisoformat(r["timestamp_iso"]) - base).total_seconds() for r in rows]
    lat = [r["latency_ms"] for r in rows]

    fig, ax = plt.subplots(figsize=(11, 4))
    ax.scatter(segundos, lat, s=3, alpha=0.3)
    ax.set_xlabel("Segundos desde el inicio")
    ax.set_ylabel("Latencia (ms)")
    ax.set_title(f"Latencia por request — {endpoint} — {prefix}")
    fig.tight_layout()
    fig.savefig(f"{prefix}_{endpoint.lower()}_latencia.png", dpi=110)

    buckets = {}
    for s in segundos:
        buckets[int(s)] = buckets.get(int(s), 0) + 1
    xs = sorted(buckets)
    fig2, ax2 = plt.subplots(figsize=(11, 4))
    ax2.plot(xs, [buckets[x] for x in xs])
    ax2.set_xlabel("Segundos desde el inicio")
    ax2.set_ylabel("Requests por segundo")
    ax2.set_title(f"Throughput — {endpoint} — {prefix}")
    fig2.tight_layout()
    fig2.savefig(f"{prefix}_{endpoint.lower()}_throughput.png", dpi=110)
    print(f"[ok] Graficos: {prefix}_{endpoint.lower()}_latencia.png / _throughput.png")


# --------------------------------------------------------------------------
def check_ulimit(users):
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    necesario = users + 256
    if soft < necesario:
        print(f"[warn] ulimit -n actual = {soft}, se recomiendan >= {necesario}.")
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (min(necesario, hard), hard))
            print(f"[ok] Limite elevado a {min(necesario, hard)} para este proceso.")
        except (ValueError, OSError):
            print("[warn] No se pudo elevar. Ejecute antes:  ulimit -n 25000")
            print("[warn] Si el SO no lo permite, repórtelo como restriccion del entorno.")


def main():
    p = argparse.ArgumentParser(description="Pruebas de carga - Lab 2 Cheapest")
    p.add_argument("--endpoint", choices=["GET", "POST"], required=True)
    p.add_argument("--users", type=int, required=True, help="Concurrencia objetivo")
    p.add_argument("--ramp-up", type=float, default=0, dest="ramp_up")
    p.add_argument("--duration", type=float, default=60, help="Segundos de carga sostenida")
    p.add_argument("--out-prefix", default="run", dest="out_prefix")
    p.add_argument("--conn-limit", type=int, default=0, dest="conn_limit",
                   help="Limite de conexiones del pool (0 = igual a --users)")
    args = p.parse_args()

    if args.conn_limit == 0:
        args.conn_limit = args.users

    check_ulimit(args.users)
    productos = load_productos() if args.endpoint == "POST" else []

    print(f"\n=== {args.endpoint} | users={args.users} | ramp-up={args.ramp_up}s "
          f"| duracion sostenida={args.duration}s ===")

    rows, elapsed = asyncio.run(run(args, productos))

    steady = [r for r in rows if r["steady"]]
    summarize(rows, elapsed, f"CORRIDA COMPLETA ({elapsed:.1f}s, incluye ramp-up)")
    summarize(steady, args.duration, f"VENTANA SOSTENIDA ({args.duration:.0f}s) <-- comparar con ASR")

    csv_path = f"{args.out_prefix}_{args.endpoint.lower()}.csv"
    write_csv(rows, csv_path)
    if rows:
        make_plots(rows, args.out_prefix, args.endpoint)


if __name__ == "__main__":
    main()

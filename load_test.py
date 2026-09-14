#!/usr/bin/env python3
"""
Pruebas de carga contra el monolito de Cheapest desplegado en AWS.

Se usa para los escenarios de mas de 450 threads, donde JMeter deja de ser
un generador de carga confiable y se vuelve el cuello de botella del cliente.

A diferencia del Lab 2, el destino ya no es localhost sino el DNS del
Application Load Balancer. Toda medicion incluye el RTT de red hasta
us-east-1, por lo que conviene registrar la latencia base con --baseline
antes de la matriz.

Instalacion:
    pip3 install aiohttp

Uso:
    python3 load_test.py --baseline
    python3 load_test.py --endpoint GET  --users 1500  --ramp-up 75  --duration 60 --out-prefix get_alta_c1
    python3 load_test.py --endpoint POST --users 18000 --ramp-up 200 --duration 60 --out-prefix post_fuerte_c1

Modelo temporal:
    Durante los primeros --ramp-up segundos la concurrencia sube linealmente
    de 0 a --users. Despues se SOSTIENE en --users durante --duration segundos.
    Duracion total = ramp-up + duration.

    Las metricas se reportan dos veces: sobre toda la corrida y solo sobre la
    ventana sostenida (steady state). La ventana sostenida es la comparable
    con los ASRs, porque durante el ramp-up la concurrencia todavia no es la
    nominal y los percentiles salen optimistas.
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

try:
    import aiohttp
except ImportError:
    sys.exit("Falta aiohttp. Instale con:  pip3 install aiohttp")

# ----------------------------------------------------------------------------
# Configuracion del sistema bajo prueba
# ----------------------------------------------------------------------------

ALB_DNS = "Cheapest-alb-119288833.us-east-1.elb.amazonaws.com"

RUTA_GET = "/logistics/tenderos/productos-disponibles"
RUTA_POST = "/logistics/pedidos"
RUTA_HEALTH = "/health"

# Parametros fijos del GET. La zona se pasa por params para que aiohttp la
# codifique: un espacio sin codificar devuelve 400 en todas las peticiones.
TIENDA_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ZONA = "Zona Norte"

MONEDA_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
PRODUCTO_FALLBACK = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

ITEMS_POR_PEDIDO = 25   # el laboratorio exige un pedido grande (> 20 items)
TIMEOUT_S = 30


# ----------------------------------------------------------------------------
# Construccion del body del POST
# ----------------------------------------------------------------------------

def cargar_productos(ruta):
    """Lee UUIDs reales de productos, uno por linea.

    Sin UUIDs que existan en la base, cada insert falla por llave foranea y
    el Error % mediria datos invalidos en vez de saturacion del sistema.
    """
    if ruta and os.path.exists(ruta):
        with open(ruta) as f:
            ids = [l.strip() for l in f if l.strip()]
        if ids:
            return ids
        print(f"AVISO: {ruta} esta vacio, se usa el UUID de respaldo.")
    else:
        print(f"AVISO: no se encontro {ruta}, se usa el UUID de respaldo.")
        print("       Genere el archivo con:")
        print("       sudo docker exec cheapest-db psql -U postgres -d cheapest "
              "-t -A -c \"select id from productos limit 50;\" > productos.txt")
    return [PRODUCTO_FALLBACK]


def construir_body(productos):
    """Genera un pedido que cumple el contrato del DTO.

    'identificador' es unico por peticion. Si se repitiera, la base lo
    rechazaria por clave duplicada y el Error % reflejaria colisiones de
    datos en lugar de la capacidad real del sistema.
    """
    items = []
    for _ in range(ITEMS_POR_PEDIDO):
        items.append({
            "productoId": random.choice(productos),
            "cantidad": random.randint(1, 10),
            "precioUnitario": round(random.uniform(1000, 50000), 2),
            "descuento": 0,
            "monedaId": MONEDA_ID,
        })
    monto = round(sum(i["precioUnitario"] * i["cantidad"] for i in items), 2)
    return {
        "identificador": f"PED-{uuid.uuid4()}",
        "tiendaId": TIENDA_ID,
        "fechaHoraCreacion": datetime.now(timezone.utc).isoformat(),
        "montoTotal": monto,
        "monedaId": MONEDA_ID,
        "estado": "creado",
        "items": items,
    }


# ----------------------------------------------------------------------------
# Motor de carga
# ----------------------------------------------------------------------------

class Registro:
    __slots__ = ("t_inicio", "status", "latencia_ms", "error")

    def __init__(self, t_inicio, status, latencia_ms, error):
        self.t_inicio = t_inicio
        self.status = status
        self.latencia_ms = latencia_ms
        self.error = error


async def una_peticion(sesion, base, endpoint, productos, registros, t0):
    inicio = time.perf_counter()
    marca = time.time()
    status, error = 0, ""
    try:
        if endpoint == "GET":
            url = base + RUTA_GET
            params = {"tiendaId": TIENDA_ID, "zona": ZONA}
            async with sesion.get(url, params=params) as r:
                await r.read()
                status = r.status
        else:
            url = base + RUTA_POST
            cuerpo = construir_body(productos)
            cabeceras = {"Content-Type": "application/json",
                         "Accept": "application/json"}
            async with sesion.post(url, json=cuerpo, headers=cabeceras) as r:
                await r.read()
                status = r.status
        if status >= 400:
            error = f"http_{status}"
    except asyncio.TimeoutError:
        error = "timeout"
    except aiohttp.ClientConnectorError:
        error = "connection_error"
    except aiohttp.ClientError as e:
        error = f"client_error:{type(e).__name__}"
    except Exception as e:
        error = f"otro:{type(e).__name__}"

    latencia = (time.perf_counter() - inicio) * 1000
    registros.append(Registro(marca, status, latencia, error))


async def trabajador(idx, sesion, base, endpoint, productos, registros,
                     t0, retraso, fin):
    """Un usuario simulado: espera su turno del ramp-up y luego envia
    peticiones en serie hasta que termina la corrida."""
    await asyncio.sleep(retraso)
    while time.perf_counter() < fin:
        await una_peticion(sesion, base, endpoint, productos, registros, t0)


async def ejecutar(args, productos):
    base = args.base_url.rstrip("/")
    registros = []

    conector = aiohttp.TCPConnector(
        limit=args.conn_limit,
        limit_per_host=args.conn_limit,
        ttl_dns_cache=300,
    )
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_S)

    async with aiohttp.ClientSession(connector=conector, timeout=timeout) as sesion:
        t0 = time.perf_counter()
        fin = t0 + args.ramp_up + args.duration
        paso = args.ramp_up / args.users if args.users else 0

        tareas = [
            asyncio.create_task(
                trabajador(i, sesion, base, args.endpoint, productos,
                           registros, t0, i * paso, fin)
            )
            for i in range(args.users)
        ]

        inicio_sostenido = t0 + args.ramp_up
        await asyncio.gather(*tareas, return_exceptions=True)

    return registros, t0, inicio_sostenido


# ----------------------------------------------------------------------------
# Metricas
# ----------------------------------------------------------------------------

def percentil(valores, p):
    if not valores:
        return 0.0
    orden = sorted(valores)
    k = (len(orden) - 1) * (p / 100)
    bajo, alto = int(k), min(int(k) + 1, len(orden) - 1)
    return orden[bajo] + (orden[alto] - orden[bajo]) * (k - int(k))


def resumir(registros, segundos, etiqueta):
    if not registros:
        print(f"\n[{etiqueta}] sin peticiones registradas.")
        return None

    latencias = [r.latencia_ms for r in registros]
    fallidos = [r for r in registros if r.error]
    total = len(registros)

    m = {
        "etiqueta": etiqueta,
        "samples": total,
        "throughput": total / segundos if segundos > 0 else 0,
        "promedio": statistics.mean(latencias),
        "min": min(latencias),
        "max": max(latencias),
        "desv": statistics.pstdev(latencias) if total > 1 else 0.0,
        "p95": percentil(latencias, 95),
        "p99": percentil(latencias, 99),
        "error_pct": len(fallidos) / total * 100,
    }

    print(f"\n--- {etiqueta} ---")
    print(f"  # Samples        : {m['samples']}")
    print(f"  Throughput       : {m['throughput']:.2f} req/s")
    print(f"  Latencia promedio: {m['promedio']:.1f} ms")
    print(f"  Min / Max        : {m['min']:.1f} / {m['max']:.1f} ms")
    print(f"  Desv. estandar   : {m['desv']:.1f} ms")
    print(f"  p95              : {m['p95']:.1f} ms")
    print(f"  p99              : {m['p99']:.1f} ms")
    print(f"  Error %          : {m['error_pct']:.2f} %")

    if fallidos:
        tipos = {}
        for r in fallidos:
            tipos[r.error] = tipos.get(r.error, 0) + 1
        print("  Desglose de errores:")
        for k, v in sorted(tipos.items(), key=lambda x: -x[1]):
            print(f"    {k}: {v} ({v / total * 100:.2f} %)")

    return m


def veredicto(m):
    """Compara contra las medidas de respuesta de los ASRs del laboratorio."""
    if not m:
        return
    asr1 = m["p99"] < 1000
    asr2 = m["error_pct"] <= 2
    print("\n--- Cumplimiento de ASRs (ventana sostenida) ---")
    print(f"  ASR 1  p99 < 1000 ms : {'CUMPLE' if asr1 else 'NO CUMPLE'} "
          f"({m['p99']:.1f} ms)")
    print(f"  ASR 2  Error % <= 2  : {'CUMPLE' if asr2 else 'NO CUMPLE'} "
          f"({m['error_pct']:.2f} %)")
    if asr1 and asr2:
        print("  -> Todavia dentro de los ASRs. Suba al siguiente escenario.")
    else:
        print("  -> Punto de inflexion alcanzado en este nivel de carga.")


def fila_tabla(args, m):
    """Imprime la fila lista para pegar en la tabla del informe."""
    print("\n--- Fila para la tabla de resultados ---")
    print("| # threads/users | Ramp-up (s) | p99 (ms) | p95 (ms) | "
          "Throughput (req/s) | Error % |")
    print(f"| {args.users} | {args.ramp_up} | {m['p99']:.0f} | {m['p95']:.0f} | "
          f"{m['throughput']:.2f} | {m['error_pct']:.2f} |")


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------

def exportar(registros, prefijo, endpoint):
    nombre = f"{prefijo}_{endpoint.lower()}.csv"
    with open(nombre, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_iso", "metodo", "endpoint",
                    "status_code", "latency_ms", "error"])
        ruta = RUTA_GET if endpoint == "GET" else RUTA_POST
        for r in registros:
            w.writerow([
                datetime.fromtimestamp(r.t_inicio, timezone.utc).isoformat(),
                endpoint, ruta, r.status, f"{r.latencia_ms:.2f}", r.error,
            ])
    print(f"\nCSV escrito: {nombre}")
    return nombre


def revisar_descriptores(usuarios):
    blando, duro = resource.getrlimit(resource.RLIMIT_NOFILE)
    necesarios = int(usuarios * 1.2) + 256
    if blando < necesarios:
        print(f"AVISO: limite de descriptores ({blando}) por debajo de los "
              f"~{necesarios} que requieren {usuarios} usuarios.")
        print(f"       Suba el limite antes de correr:  ulimit -n {necesarios}")
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (min(necesarios, duro), duro))
            print(f"       Ajustado en caliente a {min(necesarios, duro)}.")
        except (ValueError, OSError):
            print("       No se pudo ajustar automaticamente.")


async def medir_baseline(base, n=10):
    """Latencia base contra /health. Es el piso de red que va incluido en
    todas las mediciones de la matriz y hay que reportarlo en el informe."""
    url = base.rstrip("/") + RUTA_HEALTH
    tiempos = []
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_S)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        for _ in range(n):
            t = time.perf_counter()
            try:
                async with s.get(url) as r:
                    await r.read()
                    if r.status == 200:
                        tiempos.append((time.perf_counter() - t) * 1000)
            except Exception as e:
                print(f"  fallo: {type(e).__name__}")
            await asyncio.sleep(0.3)
    if tiempos:
        print(f"\nLatencia base a {url}")
        print(f"  muestras : {len(tiempos)}")
        print(f"  promedio : {statistics.mean(tiempos):.1f} ms")
        print(f"  min / max: {min(tiempos):.1f} / {max(tiempos):.1f} ms")
        print("\nEste valor es el piso de red de todas las mediciones.")
    else:
        print("No hubo respuestas exitosas. Revise el ALB y los targets.")


# ----------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="Generador de carga para el monolito de Cheapest en AWS.")
    p.add_argument("--endpoint", choices=["GET", "POST"])
    p.add_argument("--users", type=int, default=1500,
                   help="Concurrencia objetivo")
    p.add_argument("--ramp-up", type=float, default=75,
                   help="Segundos para escalar de 0 a --users")
    p.add_argument("--duration", type=float, default=60,
                   help="Segundos sosteniendo la concurrencia objetivo")
    p.add_argument("--out-prefix", default="corrida",
                   help="Prefijo del CSV de salida")
    p.add_argument("--base-url", default=f"http://{ALB_DNS}",
                   help="URL del ALB")
    p.add_argument("--productos", default="productos.txt",
                   help="Archivo con UUIDs reales de productos (para POST)")
    p.add_argument("--conn-limit", type=int, default=0,
                   help="Limite de conexiones del pool (0 = sin limite)")
    p.add_argument("--baseline", action="store_true",
                   help="Solo mide la latencia base contra /health y termina")
    args = p.parse_args()

    if args.baseline:
        asyncio.run(medir_baseline(args.base_url))
        return

    if not args.endpoint:
        sys.exit("Debe indicar --endpoint GET o --endpoint POST "
                 "(o usar --baseline).")

    productos = cargar_productos(args.productos) if args.endpoint == "POST" else []
    revisar_descriptores(args.users)

    print(f"\nDestino    : {args.base_url}")
    print(f"Endpoint   : {args.endpoint}")
    print(f"Usuarios   : {args.users}")
    print(f"Ramp-up    : {args.ramp_up} s")
    print(f"Sostenido  : {args.duration} s")
    print(f"Total      : {args.ramp_up + args.duration} s")
    print("\nEjecutando...")

    registros, t0, inicio_sostenido = asyncio.run(ejecutar(args, productos))

    absoluto_t0 = time.time() - (time.perf_counter() - t0)
    corte = absoluto_t0 + args.ramp_up
    sostenidos = [r for r in registros if r.t_inicio >= corte]

    resumir(registros, args.ramp_up + args.duration, "Corrida completa")
    m = resumir(sostenidos, args.duration, "Ventana sostenida (comparable con ASRs)")

    veredicto(m)
    if m:
        fila_tabla(args, m)
    exportar(registros, args.out_prefix, args.endpoint)


if __name__ == "__main__":
    main()

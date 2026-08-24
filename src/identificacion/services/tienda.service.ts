import { Injectable, NotFoundException } from '@nestjs/common';
import {
  CreateTiendaDto,
  QueryTiendaDto,
  TiendaResponseDto,
  UpdateTiendaDto,
} from '../dtos';
import { TiendaRepository } from '../repositories';
import { Tienda } from '../repositories/entities';

@Injectable()
export class TiendaService {
  constructor(
    private readonly tiendaRepository: TiendaRepository,
  ) {}

  async create(dto: CreateTiendaDto): Promise<TiendaResponseDto> {
    const tienda = await this.tiendaRepository.create(dto);
    return this.mapToResponse(tienda);
  }

  async findAll(query: QueryTiendaDto): Promise<TiendaResponseDto[]> {
    const tiendas = await this.tiendaRepository.findAll(query);
    return tiendas.map((tienda) => this.mapToResponse(tienda));
  }

  async findById(id: string): Promise<TiendaResponseDto> {
    const tienda = await this.tiendaRepository.findById(id);
    if (!tienda) {
      throw new NotFoundException(`Tienda con id ${id} no encontrada`);
    }
    return this.mapToResponse(tienda);
  }

  async update(
    id: string,
    dto: UpdateTiendaDto,
  ): Promise<TiendaResponseDto> {
    const tienda = await this.tiendaRepository.findById(id);
    if (!tienda) {
      throw new NotFoundException(`Tienda con id ${id} no encontrada`);
    }

    const updatedTienda = await this.tiendaRepository.update(id, dto);
    return this.mapToResponse(updatedTienda!);
  }

  async delete(id: string): Promise<void> {
    const tienda = await this.tiendaRepository.findById(id);
    if (!tienda) {
      throw new NotFoundException(`Tienda con id ${id} no encontrada`);
    }

    await this.tiendaRepository.delete(id);
  }

  async exists(id: string): Promise<boolean> {
  const tienda = await this.tiendaRepository.findById(id);
  return tienda !== null;
}

  private mapToResponse(tienda: Tienda): TiendaResponseDto {
  return {
    id: tienda.id,
    codigoInterno: tienda.codigoInterno,
    nombreComercial: tienda.nombreComercial,
    rut: tienda.rut,
    direccion: tienda.direccion,
    telefono: tienda.telefono,
    estadoCaptacion: tienda.estadoCaptacion,
    createdAt: tienda.createdAt,
    updatedAt: tienda.updatedAt,
  };
}

}

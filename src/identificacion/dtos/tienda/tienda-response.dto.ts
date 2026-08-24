import { EstadoCaptacion } from 'src/identificacion/repositories/entities';

export class TiendaResponseDto {
  id: string;
  codigoInterno: string;
  nombreComercial: string;
  rut: string;
  direccion: string;
  telefono: string;
  estadoCaptacion: EstadoCaptacion;
  createdAt: Date;
  updatedAt: Date;
}
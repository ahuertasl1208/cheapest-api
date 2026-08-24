import {IsOptional, IsString, IsUUID, MaxLength} from 'class-validator';
import { EstadoCaptacion } from 'src/identificacion/repositories/entities';

export class CreateTiendaDto {
  
  @IsString()
  @MaxLength(100)
  codigoInterno: string;

   @IsString()
   @MaxLength(255)
  nombreComercial: string;

   @IsString()
   @MaxLength(255)
  rut: string;

   @IsString()
   @MaxLength(255)
  direccion: string;

   @IsString()
   @MaxLength(50)
  telefono: string;

  @IsOptional()
    @IsUUID()
    responsableId?: string;


}
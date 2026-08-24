import {
  Body,
  Controller,
  Delete,
  Get,
  Param,
  Patch,
  Post,
  Query,
  ValidationPipe,
} from '@nestjs/common';
import {
  TiendaResponseDto,
  CreateTiendaDto,
  QueryTiendaDto,
  UpdateTiendaDto,
} from '../dtos';
import { TiendaService } from '../services';

@Controller('identification/tiendas')
export class TiendaController {
  constructor(private readonly tiendaService: TiendaService) {}

  @Post()
  async create(
    @Body(new ValidationPipe({ transform: true }))
    dto: CreateTiendaDto,
  ): Promise<TiendaResponseDto> {
    return this.tiendaService.create(dto);
  }

  @Get()
  async findAll(
    @Query(new ValidationPipe({ transform: true }))
    query: QueryTiendaDto,
  ): Promise<TiendaResponseDto[]> {
    return this.tiendaService.findAll(query);
  }

  @Get(':id')
  async findById(@Param('id') id: string): Promise<TiendaResponseDto> {
    return this.tiendaService.findById(id);
  }

  @Patch(':id')
  async update(
    @Param('id') id: string,
    @Body(new ValidationPipe({ transform: true }))
    dto: UpdateTiendaDto,
  ): Promise<TiendaResponseDto> {
    return this.tiendaService.update(id, dto);
  }

  @Delete(':id')
  async delete(@Param('id') id: string): Promise<void> {
    return this.tiendaService.delete(id);
  }
}
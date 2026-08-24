import {
  Column,
  CreateDateColumn,
  Entity,
  PrimaryGeneratedColumn,
  UpdateDateColumn,
} from 'typeorm';


export enum EstadoCaptacion {
    PROSPECTO_CREADO = 'prospectoCreado',
    VISITA_1_REALIZADA = 'visita1Realizada',
    DOCUMENTOS_RECIBIDOS = 'documentosRecibidos',
    VISITA_2_REALIZADA = 'visita2Realizada',
    RUT_VALIDADO = 'rutValidado',
    HABILITADO_BASICO = 'habilitadoBasico',
    HABILITADO_AVANZADO = 'habilitadoAvanzado'
}

@Entity('tiendas')
export class Tienda {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column('varchar', { length: 100 })
  codigoInterno: string;

  @Column('varchar', { length: 255 })
  nombreComercial: string;

  @Column('uuid', { nullable: true })
  responsableId: string;

  @Column('varchar', { length: 255 })
  direccion: string;

  @Column('varchar', { length: 50 })
  telefono: string;

  @Column('varchar', { length: 255 })
  rut: string;


  @Column({
    type: 'enum',
    enum: EstadoCaptacion,
    default: EstadoCaptacion.PROSPECTO_CREADO,
})
    estadoCaptacion: EstadoCaptacion;

    @CreateDateColumn()
createdAt: Date;

@UpdateDateColumn()
updatedAt: Date;






   


}

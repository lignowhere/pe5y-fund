import { PrismaClient } from '@prisma/client';
import { CreateWarehouseDTO, UpdateWarehouseDTO } from './warehouse.types';

export class WarehouseRepository {
  constructor(private prisma: PrismaClient) {}

  async create(data: CreateWarehouseDTO) {
    return this.prisma.warehouse.create({ data });
  }

  async findById(id: string) {
    return this.prisma.warehouse.findUnique({ where: { id } });
  }

  async findByLocationCode(locationCode: string) {
    return this.prisma.warehouse.findUnique({ where: { locationCode } });
  }

  async findAll() {
    return this.prisma.warehouse.findMany({
      where: { isActive: true },
      orderBy: { createdAt: 'desc' },
    });
  }

  async update(id: string, data: UpdateWarehouseDTO) {
    return this.prisma.warehouse.update({
      where: { id },
      data,
    });
  }

  async softDelete(id: string) {
    return this.prisma.warehouse.update({
      where: { id },
      data: { isActive: false },
    });
  }

  async hasActiveInventory(warehouseId: string): Promise<boolean> {
    const count = await this.prisma.inventory.count({
      where: {
        warehouseId,
        quantityAvailable: { gt: 0 },
      },
    });
    return count > 0;
  }
}

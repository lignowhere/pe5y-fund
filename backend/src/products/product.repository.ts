import { PrismaClient } from '@prisma/client';
import { CreateProductDTO, UpdateProductDTO, ProductFilters } from './product.types';

export class ProductRepository {
  constructor(private prisma: PrismaClient) {}

  async create(data: CreateProductDTO) {
    return this.prisma.product.create({
      data: {
        ...data,
        costPriceUsd: data.costPriceUsd.toString(),
        sellingPriceUsd: data.sellingPriceUsd.toString(),
        weightKg: data.weightKg ? data.weightKg.toString() : null,
      },
    });
  }

  async findById(id: string) {
    return this.prisma.product.findUnique({ where: { id } });
  }

  async findBySKU(sku: string) {
    return this.prisma.product.findUnique({ where: { sku } });
  }

  async findByBarcode(barcode: string) {
    return this.prisma.product.findUnique({ where: { barcode } });
  }

  async findAll(page: number = 1, limit: number = 50) {
    const [products, total] = await Promise.all([
      this.prisma.product.findMany({
        where: { isActive: true },
        skip: (page - 1) * limit,
        take: limit,
        orderBy: { createdAt: 'desc' },
      }),
      this.prisma.product.count({ where: { isActive: true } }),
    ]);

    return { products, total, page, limit };
  }

  async search(filters: ProductFilters) {
    const { q, category, page = 1, limit = 50 } = filters;
    const where: any = { isActive: true };

    if (q) {
      where.OR = [
        { sku: { contains: q, mode: 'insensitive' } },
        { name: { contains: q, mode: 'insensitive' } },
        { barcode: { contains: q, mode: 'insensitive' } },
      ];
    }
    if (category) where.category = category;

    const [products, total] = await Promise.all([
      this.prisma.product.findMany({
        where,
        skip: (page - 1) * limit,
        take: limit,
        orderBy: { createdAt: 'desc' },
      }),
      this.prisma.product.count({ where }),
    ]);

    return { products, total, page, limit };
  }

  async update(id: string, data: UpdateProductDTO) {
    return this.prisma.product.update({
      where: { id },
      data: {
        ...data,
        ...(data.costPriceUsd && { costPriceUsd: data.costPriceUsd.toString() }),
        ...(data.sellingPriceUsd && { sellingPriceUsd: data.sellingPriceUsd.toString() }),
        ...(data.weightKg && { weightKg: data.weightKg.toString() }),
      },
    });
  }

  async softDelete(id: string) {
    return this.prisma.product.update({
      where: { id },
      data: { isActive: false },
    });
  }
}

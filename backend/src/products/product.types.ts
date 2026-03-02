import { Prisma } from '@prisma/client';

export type CreateProductDTO = {
  sku: string;
  name: string;
  description?: string;
  category?: string;
  costPriceUsd: number;
  sellingPriceUsd: number;
  weightKg?: number;
  dimensions?: {
    length: number;
    width: number;
    height: number;
    unit: 'cm' | 'in';
  };
  barcode?: string;
  imageUrl?: string;
};

export type UpdateProductDTO = Partial<CreateProductDTO>;

export type ProductFilters = {
  q?: string;
  category?: string;
  page?: number;
  limit?: number;
};

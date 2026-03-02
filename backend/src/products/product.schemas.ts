import { z } from 'zod';

export const createProductSchema = z.object({
  body: z.object({
    sku: z.string().min(3).max(100),
    name: z.string().min(1).max(255),
    description: z.string().optional(),
    category: z.string().max(100).optional(),
    costPriceUsd: z.number().positive(),
    sellingPriceUsd: z.number().positive(),
    weightKg: z.number().positive().optional(),
    dimensions: z.object({
      length: z.number().positive(),
      width: z.number().positive(),
      height: z.number().positive(),
      unit: z.enum(['cm', 'in']),
    }).optional(),
    barcode: z.string().max(100).optional(),
    imageUrl: z.string().url().optional(),
  }),
});

export const updateProductSchema = z.object({
  params: z.object({ id: z.string().uuid() }),
  body: createProductSchema.shape.body.partial(),
});

export const getProductSchema = z.object({
  params: z.object({ id: z.string().uuid() }),
});

export const searchProductSchema = z.object({
  query: z.object({
    q: z.string().optional(),
    category: z.string().optional(),
    page: z.string().regex(/^\d+$/).transform(Number).optional(),
    limit: z.string().regex(/^\d+$/).transform(Number).optional(),
  }),
});

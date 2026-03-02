import { z } from 'zod';

export const createWarehouseSchema = z.object({
  body: z.object({
    name: z.string().min(1).max(255),
    locationCode: z.string().min(1).max(50),
    address: z.string().optional(),
    country: z.string().max(100).optional(),
  }),
});

export const updateWarehouseSchema = z.object({
  params: z.object({ id: z.string().uuid() }),
  body: createWarehouseSchema.shape.body.partial(),
});

export const getWarehouseSchema = z.object({
  params: z.object({ id: z.string().uuid() }),
});

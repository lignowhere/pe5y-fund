import { Router } from 'express';
import { PrismaClient } from '@prisma/client';
import { WarehouseController } from './warehouse.controller';
import { WarehouseService } from './warehouse.service';
import { WarehouseRepository } from './warehouse.repository';
import { validate } from '../middleware/validate';
import {
  createWarehouseSchema,
  updateWarehouseSchema,
  getWarehouseSchema
} from './warehouse.schemas';

const router = Router();
const prisma = new PrismaClient();
const repository = new WarehouseRepository(prisma);
const service = new WarehouseService(repository);
const controller = new WarehouseController(service);

router.post('/', validate(createWarehouseSchema), controller.createWarehouse);
router.get('/:id', validate(getWarehouseSchema), controller.getWarehouse);
router.get('/', controller.getAllWarehouses);
router.put('/:id', validate(updateWarehouseSchema), controller.updateWarehouse);
router.delete('/:id', validate(getWarehouseSchema), controller.deleteWarehouse);

export default router;

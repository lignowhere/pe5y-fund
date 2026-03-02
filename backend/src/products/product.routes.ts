import { Router } from 'express';
import { PrismaClient } from '@prisma/client';
import { ProductController } from './product.controller';
import { ProductService } from './product.service';
import { ProductRepository } from './product.repository';
import { validate } from '../middleware/validate';
import {
  createProductSchema,
  updateProductSchema,
  getProductSchema,
  searchProductSchema
} from './product.schemas';

const router = Router();
const prisma = new PrismaClient();
const repository = new ProductRepository(prisma);
const service = new ProductService(repository);
const controller = new ProductController(service);

router.post('/', validate(createProductSchema), controller.createProduct);
router.get('/search', validate(searchProductSchema), controller.searchProducts);
router.get('/:id', validate(getProductSchema), controller.getProduct);
router.get('/', controller.getAllProducts);
router.put('/:id', validate(updateProductSchema), controller.updateProduct);
router.delete('/:id', validate(getProductSchema), controller.deleteProduct);

export default router;

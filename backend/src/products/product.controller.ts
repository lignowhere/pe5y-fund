import { Request, Response } from 'express';
import { ProductService } from './product.service';
import { asyncHandler } from '../middleware/async-handler';

export class ProductController {
  constructor(private service: ProductService) {}

  createProduct = asyncHandler(async (req: Request, res: Response) => {
    const product = await this.service.createProduct(req.body);
    res.status(201).json({ success: true, data: product });
  });

  getProduct = asyncHandler(async (req: Request, res: Response) => {
    const product = await this.service.getProduct(req.params.id!);
    res.json({ success: true, data: product });
  });

  getAllProducts = asyncHandler(async (req: Request, res: Response) => {
    const page = req.query.page ? parseInt(req.query.page as string) : undefined;
    const limit = req.query.limit ? parseInt(req.query.limit as string) : undefined;
    const result = await this.service.getAllProducts(page, limit);
    res.json({ success: true, data: result });
  });

  searchProducts = asyncHandler(async (req: Request, res: Response) => {
    const result = await this.service.searchProducts(req.query);
    res.json({ success: true, data: result });
  });

  updateProduct = asyncHandler(async (req: Request, res: Response) => {
    const product = await this.service.updateProduct(req.params.id!, req.body);
    res.json({ success: true, data: product });
  });

  deleteProduct = asyncHandler(async (req: Request, res: Response) => {
    const product = await this.service.deleteProduct(req.params.id!);
    res.json({ success: true, data: product });
  });
}

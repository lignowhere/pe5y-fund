import { Request, Response } from 'express';
import { WarehouseService } from './warehouse.service';
import { asyncHandler } from '../middleware/async-handler';

export class WarehouseController {
  constructor(private service: WarehouseService) {}

  createWarehouse = asyncHandler(async (req: Request, res: Response) => {
    const warehouse = await this.service.createWarehouse(req.body);
    res.status(201).json({ success: true, data: warehouse });
  });

  getWarehouse = asyncHandler(async (req: Request, res: Response) => {
    const warehouse = await this.service.getWarehouse(req.params.id!);
    res.json({ success: true, data: warehouse });
  });

  getAllWarehouses = asyncHandler(async (req: Request, res: Response) => {
    const warehouses = await this.service.getAllWarehouses();
    res.json({ success: true, data: warehouses });
  });

  updateWarehouse = asyncHandler(async (req: Request, res: Response) => {
    const warehouse = await this.service.updateWarehouse(req.params.id!, req.body);
    res.json({ success: true, data: warehouse });
  });

  deleteWarehouse = asyncHandler(async (req: Request, res: Response) => {
    const warehouse = await this.service.deleteWarehouse(req.params.id!);
    res.json({ success: true, data: warehouse });
  });
}

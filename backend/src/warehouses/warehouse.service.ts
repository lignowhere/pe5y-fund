import { AppError } from '../utils/app-error';
import { WarehouseRepository } from './warehouse.repository';
import { CreateWarehouseDTO, UpdateWarehouseDTO } from './warehouse.types';

export class WarehouseService {
  constructor(private repository: WarehouseRepository) {}

  async createWarehouse(data: CreateWarehouseDTO) {
    // Check for duplicate location code
    const existing = await this.repository.findByLocationCode(data.locationCode);
    if (existing) {
      throw new AppError(409, 'Warehouse with this location code already exists', 'DUPLICATE_LOCATION_CODE');
    }

    return this.repository.create(data);
  }

  async getWarehouse(id: string) {
    const warehouse = await this.repository.findById(id);
    if (!warehouse) {
      throw new AppError(404, 'Warehouse not found', 'NOT_FOUND');
    }
    return warehouse;
  }

  async getAllWarehouses() {
    return this.repository.findAll();
  }

  async updateWarehouse(id: string, data: UpdateWarehouseDTO) {
    // Verify warehouse exists
    await this.getWarehouse(id);

    // Check for duplicate location code if updating location code
    if (data.locationCode) {
      const existing = await this.repository.findByLocationCode(data.locationCode);
      if (existing && existing.id !== id) {
        throw new AppError(409, 'Warehouse with this location code already exists', 'DUPLICATE_LOCATION_CODE');
      }
    }

    return this.repository.update(id, data);
  }

  async deleteWarehouse(id: string) {
    // Verify warehouse exists
    await this.getWarehouse(id);

    // Check for active inventory
    const hasInventory = await this.repository.hasActiveInventory(id);
    if (hasInventory) {
      throw new AppError(409, 'Cannot delete warehouse with active inventory', 'WAREHOUSE_HAS_INVENTORY');
    }

    return this.repository.softDelete(id);
  }
}

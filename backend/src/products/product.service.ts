import { AppError } from '../utils/app-error';
import { ProductRepository } from './product.repository';
import { CreateProductDTO, UpdateProductDTO, ProductFilters } from './product.types';

export class ProductService {
  constructor(private repository: ProductRepository) {}

  async createProduct(data: CreateProductDTO) {
    // Check for duplicate SKU
    const existingSKU = await this.repository.findBySKU(data.sku);
    if (existingSKU) {
      throw new AppError(409, 'Product with this SKU already exists', 'DUPLICATE_SKU');
    }

    // Check for duplicate barcode
    if (data.barcode) {
      const existingBarcode = await this.repository.findByBarcode(data.barcode);
      if (existingBarcode) {
        throw new AppError(409, 'Product with this barcode already exists', 'DUPLICATE_BARCODE');
      }
    }

    return this.repository.create(data);
  }

  async getProduct(id: string) {
    const product = await this.repository.findById(id);
    if (!product) {
      throw new AppError(404, 'Product not found', 'NOT_FOUND');
    }
    return product;
  }

  async getAllProducts(page?: number, limit?: number) {
    return this.repository.findAll(page, limit);
  }

  async searchProducts(filters: ProductFilters) {
    return this.repository.search(filters);
  }

  async updateProduct(id: string, data: UpdateProductDTO) {
    // Verify product exists
    await this.getProduct(id);

    // Check for duplicate SKU if updating SKU
    if (data.sku) {
      const existing = await this.repository.findBySKU(data.sku);
      if (existing && existing.id !== id) {
        throw new AppError(409, 'Product with this SKU already exists', 'DUPLICATE_SKU');
      }
    }

    // Check for duplicate barcode if updating barcode
    if (data.barcode) {
      const existing = await this.repository.findByBarcode(data.barcode);
      if (existing && existing.id !== id) {
        throw new AppError(409, 'Product with this barcode already exists', 'DUPLICATE_BARCODE');
      }
    }

    return this.repository.update(id, data);
  }

  async deleteProduct(id: string) {
    // Verify product exists
    await this.getProduct(id);
    return this.repository.softDelete(id);
  }
}

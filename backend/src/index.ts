import express, { Application, Request, Response } from 'express';
import cors from 'cors';
import dotenv from 'dotenv';
import prisma from './config/prisma';
import productRoutes from './products/product.routes';
import warehouseRoutes from './warehouses/warehouse.routes';
import { errorHandler } from './middleware/error-handler';

dotenv.config();

const app: Application = express();
const PORT = process.env.PORT || 3001;

// Middleware
app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// Health check endpoint
app.get('/health', (req: Request, res: Response) => {
  res.json({
    status: 'ok',
    timestamp: new Date().toISOString(),
    database: 'connected',
  });
});

// API root
app.get('/api', (req: Request, res: Response) => {
  res.json({
    message: 'Multi-Channel Inventory Management API',
    version: '1.0.0',
    endpoints: {
      health: '/health',
      products: '/api/products',
      warehouses: '/api/warehouses',
      inventory: '/api/inventory',
      movements: '/api/movements',
      transfers: '/api/transfers',
    },
  });
});

// API Routes
app.use('/api/products', productRoutes);
app.use('/api/warehouses', warehouseRoutes);

// Test database connection endpoint
app.get('/api/test-db', async (req: Request, res: Response) => {
  try {
    const productCount = await prisma.product.count();
    const warehouseCount = await prisma.warehouse.count();
    const inventoryCount = await prisma.inventory.count();

    res.json({
      status: 'Database connected successfully',
      data: {
        products: productCount,
        warehouses: warehouseCount,
        inventoryRecords: inventoryCount,
      },
    });
  } catch (error) {
    res.status(500).json({
      status: 'Database connection failed',
      error: error instanceof Error ? error.message : 'Unknown error',
    });
  }
});

// Error handling middleware (must be last)
app.use(errorHandler);

// Start server
app.listen(PORT, () => {
  console.log(`🚀 Server running on http://localhost:${PORT}`);
  console.log(`📊 Health check: http://localhost:${PORT}/health`);
  console.log(`🔍 API root: http://localhost:${PORT}/api`);
  console.log(`🗄️  Database test: http://localhost:${PORT}/api/test-db`);
});

// Graceful shutdown
process.on('SIGTERM', async () => {
  console.log('SIGTERM received, closing server...');
  await prisma.$disconnect();
  process.exit(0);
});

process.on('SIGINT', async () => {
  console.log('SIGINT received, closing server...');
  await prisma.$disconnect();
  process.exit(0);
});

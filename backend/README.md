# Multi-Channel Inventory Management System - Backend

Production-ready backend API for managing products, warehouses, inventory levels, stock movements, and inter-warehouse transfers.

## Tech Stack

- **Runtime:** Node.js 18+ with TypeScript
- **Framework:** Express.js
- **Database:** PostgreSQL 16
- **ORM:** Prisma
- **Validation:** Zod
- **Authentication:** JWT + bcryptjs

## Database Schema

### Tables (8)
- `users` - Admin/manager/user accounts with JWT authentication
- `warehouses` - Multi-location warehouse management
- `products` - SKU-based product catalog
- `inventory` - Product quantities per warehouse
- `inventory_movements` - Immutable audit trail for all stock changes
- `stock_transfers` - Inter-warehouse transfer workflow
- `batches_lots` - Batch/lot tracking (future enhancement)
- `_prisma_migrations` - Schema version control

### Key Features
- **Multi-location:** Track inventory across China, Vietnam, US 3PL centers
- **Audit Trail:** Every quantity change creates an immutable movement record
- **Transfer Workflow:** PENDING → IN_TRANSIT → COMPLETED state machine
- **Low Stock Alerts:** Configurable reorder points per product/warehouse
- **Performance:** 14+ indexes on high-traffic queries

## Getting Started

### Prerequisites
```bash
# Install Node.js 18+
node --version  # Should be v18 or higher

# Install PostgreSQL 16
brew install postgresql@16  # macOS
brew services start postgresql@16

# Create database
createdb inventory_db
```

### Installation

```bash
# Install dependencies
npm install

# Configure environment variables
cp .env.example .env
# Edit .env with your database credentials

# Run migrations
npm run db:migrate

# Seed database (3 warehouses + 20 products + 60 inventory records)
npm run db:seed
```

### Development

```bash
# Start development server with hot reload
npm run dev

# Server runs on http://localhost:3001
```

### Available Scripts

```bash
npm run dev          # Start dev server with hot reload
npm run build        # Compile TypeScript to dist/
npm run start        # Run production build
npm run db:migrate   # Create and apply database migration
npm run db:generate  # Generate Prisma Client
npm run db:seed      # Seed database with sample data
npm run db:studio    # Open Prisma Studio GUI
npm run db:reset     # Reset database (WARNING: deletes all data)
```

## API Endpoints

### Health & Info
- `GET /health` - Health check
- `GET /api` - API information
- `GET /api/test-db` - Test database connection

### Products (Phase 2)
- `POST /api/products` - Create product
- `GET /api/products` - List products (pagination, search, filters)
- `GET /api/products/:id` - Get product details
- `PUT /api/products/:id` - Update product
- `DELETE /api/products/:id` - Soft delete product
- `GET /api/products/search?q=:query` - Search by SKU/name/barcode

### Warehouses (Phase 2)
- `POST /api/warehouses` - Create warehouse
- `GET /api/warehouses` - List warehouses
- `GET /api/warehouses/:id` - Get warehouse details
- `PUT /api/warehouses/:id` - Update warehouse
- `DELETE /api/warehouses/:id` - Deactivate warehouse

### Inventory (Phase 3)
- `GET /api/inventory` - Get all inventory (filters: warehouse_id, product_id, low_stock)
- `GET /api/inventory/product/:productId` - Get inventory across all warehouses for a product
- `GET /api/inventory/warehouse/:warehouseId` - Get all products in a warehouse
- `POST /api/inventory/adjust` - Adjust inventory quantity (with reason)
- `POST /api/inventory/transfer` - Initiate stock transfer
- `GET /api/inventory/low-stock` - Get products below reorder point
- `PUT /api/inventory/reorder-point` - Update reorder point

### Movements (Phase 4)
- `GET /api/movements` - Get all movements (filters: product, warehouse, date range, type)
- `GET /api/movements/product/:productId` - Get movement history for a product
- `POST /api/movements` - Record new movement (internal use)

### Transfers (Phase 4)
- `POST /api/transfers` - Create stock transfer
- `GET /api/transfers` - List all transfers (filter by status, warehouse)
- `GET /api/transfers/:id` - Get transfer details
- `PUT /api/transfers/:id/status` - Update transfer status

### Statistics (Phase 4)
- `GET /api/stats/dashboard` - Total SKUs, inventory value, low stock count

## Environment Variables

```env
# Database
DATABASE_URL="postgresql://user:password@localhost:5432/inventory_db?schema=public"

# Server
NODE_ENV=development
PORT=3001

# Connection Pool
POOL_MIN=2
POOL_MAX=10
POOL_IDLE_TIMEOUT_MS=30000

# JWT Authentication
JWT_SECRET=your_jwt_secret_key_change_in_production
```

## Database Migrations

```bash
# Create a new migration after schema changes
npm run db:migrate -- --name migration_name

# Apply pending migrations
npm run db:migrate

# Reset database (dev only)
npm run db:reset
```

## Seeded Data

After running `npm run db:seed`, you'll have:

- **1 Admin User:**
  - Email: `admin@inventory.com`
  - Password: `admin123`
  - Role: ADMIN

- **3 Warehouses:**
  - China Guangzhou (CN-GZ-01)
  - Vietnam Ho Chi Minh (VN-HCM-01)
  - US California 3PL (US-CA-LA-01)

- **20 Products:**
  - Categories: Electronics, Furniture, Clothing, Food & Beverage, Books
  - SKUs: SKU-0001 through SKU-0020
  - All with inventory across all warehouses

- **60 Inventory Records:**
  - Each product in each warehouse
  - Quantities: 50-200 units
  - Reorder point: 20 units

## Project Structure

```
backend/
├── prisma/
│   ├── migrations/          # Database migrations
│   ├── schema.prisma        # Database schema
│   └── seed.ts              # Seed script
├── src/
│   ├── config/
│   │   ├── database.ts      # PostgreSQL pool config
│   │   └── prisma.ts        # Prisma Client instance
│   └── index.ts             # Express server entry point
├── .env                     # Environment variables (git-ignored)
├── .env.example             # Environment template
├── package.json
├── tsconfig.json
└── README.md
```

## Next Steps

**Phase 2:** Implement Products & Warehouses API endpoints
- See `plans/251207-1655-inventory-management-system/phase-02-backend-core.md`

## License

ISC

import { PrismaClient } from '@prisma/client';
import bcrypt from 'bcryptjs';

const prisma = new PrismaClient();

async function main() {
  console.log('🌱 Starting database seed...');

  // Seed Admin User
  const hashedPassword = await bcrypt.hash('admin123', 10);
  const adminUser = await prisma.user.upsert({
    where: { email: 'admin@inventory.com' },
    update: {},
    create: {
      email: 'admin@inventory.com',
      password: hashedPassword,
      name: 'Admin User',
      role: 'ADMIN',
    },
  });
  console.log('✓ Created admin user:', adminUser.email);

  // Seed Warehouses
  const warehouses = await Promise.all([
    prisma.warehouse.upsert({
      where: { locationCode: 'CN-GZ-01' },
      update: {},
      create: {
        name: 'China Guangzhou Warehouse',
        locationCode: 'CN-GZ-01',
        address: 'No. 123 Industrial Park, Guangzhou, Guangdong 510000',
        country: 'China',
      },
    }),
    prisma.warehouse.upsert({
      where: { locationCode: 'VN-HCM-01' },
      update: {},
      create: {
        name: 'Vietnam Ho Chi Minh Warehouse',
        locationCode: 'VN-HCM-01',
        address: '456 Logistics Zone, District 7, Ho Chi Minh City 700000',
        country: 'Vietnam',
      },
    }),
    prisma.warehouse.upsert({
      where: { locationCode: 'US-CA-LA-01' },
      update: {},
      create: {
        name: 'US California 3PL Center',
        locationCode: 'US-CA-LA-01',
        address: '789 Distribution Blvd, Los Angeles, CA 90001',
        country: 'USA',
      },
    }),
  ]);
  console.log(`✓ Created ${warehouses.length} warehouses`);

  // Seed Products
  const categories = ['Electronics', 'Furniture', 'Clothing', 'Food & Beverage', 'Books'];
  const productData = [];

  for (let i = 1; i <= 20; i++) {
    const category = categories[(i - 1) % categories.length];
    productData.push({
      sku: `SKU-${String(i).padStart(4, '0')}`,
      name: `Product ${i} - ${category}`,
      description: `High-quality ${category.toLowerCase()} product with excellent features and durability.`,
      category,
      costPriceUsd: 10.0 + i * 5,
      sellingPriceUsd: 20.0 + i * 10,
      weightKg: 0.5 + i * 0.2,
      dimensions: {
        length: 10 + i,
        width: 8 + i * 0.5,
        height: 5 + i * 0.3,
        unit: 'cm',
      },
      barcode: `BAR${String(i).padStart(10, '0')}`,
      imageUrl: `https://placeholder.com/product-${i}.jpg`,
    });
  }

  const products = [];
  for (const data of productData) {
    const product = await prisma.product.upsert({
      where: { sku: data.sku },
      update: {},
      create: data,
    });
    products.push(product);
  }
  console.log(`✓ Created ${products.length} products`);

  // Seed Initial Inventory
  let inventoryCount = 0;
  let movementCount = 0;

  for (const product of products) {
    for (const warehouse of warehouses) {
      const quantityAvailable = Math.floor(Math.random() * 150) + 50; // 50-200

      const inventory = await prisma.inventory.upsert({
        where: {
          productId_warehouseId: {
            productId: product.id,
            warehouseId: warehouse.id,
          },
        },
        update: {},
        create: {
          productId: product.id,
          warehouseId: warehouse.id,
          quantityAvailable,
          reorderPoint: 20,
          lastRestockedAt: new Date(),
        },
      });
      inventoryCount++;

      // Create initial stock movement
      await prisma.inventoryMovement.create({
        data: {
          productId: product.id,
          warehouseId: warehouse.id,
          movementType: 'initial_stock',
          quantity: quantityAvailable,
          quantityBefore: 0,
          quantityAfter: quantityAvailable,
          createdBy: 'seed-script',
          notes: 'Initial stock seeded',
        },
      });
      movementCount++;
    }
  }

  console.log(`✓ Created ${inventoryCount} inventory records`);
  console.log(`✓ Created ${movementCount} inventory movements`);

  // Seed some sample stock transfers
  const sampleTransfer = await prisma.stockTransfer.create({
    data: {
      productId: products[0].id,
      fromWarehouseId: warehouses[0].id,
      toWarehouseId: warehouses[1].id,
      quantity: 25,
      status: 'pending',
      initiatedAt: new Date(),
      createdBy: adminUser.email,
      notes: 'Sample pending transfer',
    },
  });
  console.log('✓ Created sample stock transfer');

  console.log('\n🎉 Database seeding completed successfully!');
  console.log('\n📊 Summary:');
  console.log(`   - Users: 1 admin`);
  console.log(`   - Warehouses: ${warehouses.length}`);
  console.log(`   - Products: ${products.length}`);
  console.log(`   - Inventory records: ${inventoryCount}`);
  console.log(`   - Movements: ${movementCount}`);
  console.log(`   - Stock transfers: 1`);
}

main()
  .catch((e) => {
    console.error('❌ Seeding error:', e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });

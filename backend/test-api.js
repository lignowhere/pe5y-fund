/**
 * Multi-Channel Inventory Management System - Phase 2 API Testing
 *
 * Tests all 11 endpoints (6 Products + 5 Warehouses)
 * Run with: node test-api.js
 *
 * Make sure the backend server is running on port 3001
 */

const http = require('http');

const BASE_URL = 'localhost';
const PORT = 3001;

// Test counters
let totalTests = 0;
let passedTests = 0;
let failedTests = 0;

// Store created IDs for cleanup
const createdProducts = [];
const createdWarehouses = [];

// ANSI color codes
const colors = {
  reset: '\x1b[0m',
  green: '\x1b[32m',
  red: '\x1b[31m',
  yellow: '\x1b[33m',
  cyan: '\x1b[36m',
};

// Helper function to make HTTP requests
function makeRequest(method, path, data = null) {
  return new Promise((resolve, reject) => {
    const options = {
      hostname: BASE_URL,
      port: PORT,
      path: path,
      method: method,
      headers: {
        'Content-Type': 'application/json',
      },
    };

    if (data) {
      options.headers['Content-Length'] = Buffer.byteLength(JSON.stringify(data));
    }

    const req = http.request(options, (res) => {
      let body = '';

      res.on('data', (chunk) => {
        body += chunk;
      });

      res.on('end', () => {
        try {
          const jsonBody = body ? JSON.parse(body) : {};
          resolve({
            status: res.statusCode,
            body: jsonBody,
            rawBody: body,
          });
        } catch (e) {
          resolve({
            status: res.statusCode,
            body: null,
            rawBody: body,
          });
        }
      });
    });

    req.on('error', (error) => {
      reject(error);
    });

    if (data) {
      req.write(JSON.stringify(data));
    }

    req.end();
  });
}

// Test assertion helper
function assert(condition, message) {
  totalTests++;
  if (condition) {
    passedTests++;
    console.log(`${colors.green}✓ PASS${colors.reset}: ${message}`);
    return true;
  } else {
    failedTests++;
    console.log(`${colors.red}✗ FAIL${colors.reset}: ${message}`);
    return false;
  }
}

// Test runner
async function runTest(description, testFn) {
  console.log(`\n${colors.yellow}=== ${description} ===${colors.reset}`);
  try {
    await testFn();
  } catch (error) {
    console.error(`${colors.red}Error in test: ${error.message}${colors.reset}`);
    failedTests++;
  }
}

// Print section header
function printSection(title) {
  console.log(`\n${'='.repeat(50)}`);
  console.log(`  ${title}`);
  console.log('='.repeat(50));
}

// Main test suite
async function runTests() {
  console.log('==========================================');
  console.log('  Phase 2 API Testing - Backend');
  console.log('==========================================');
  console.log(`Backend: http://${BASE_URL}:${PORT}`);
  console.log('');

  // ==========================================
  // HEALTH CHECK
  // ==========================================

  await runTest('Health Check', async () => {
    const response = await makeRequest('GET', '/health');
    console.log('Response:', response.body);
    assert(response.status === 200, 'Status code should be 200');
    assert(response.body.status === 'ok', 'Health status should be ok');
  });

  // ==========================================
  // DATABASE CONNECTION TEST
  // ==========================================

  await runTest('Database Connection Test', async () => {
    const response = await makeRequest('GET', '/api/test-db');
    console.log('Response:', response.body);
    assert(response.status === 200, 'Status code should be 200');
    assert(
      response.body.status === 'Database connected successfully',
      'Database should be connected'
    );
  });

  printSection('PRODUCTS API TESTS (6 endpoints)');

  // ==========================================
  // 1. CREATE PRODUCT - VALID DATA
  // ==========================================

  let productId1 = null;

  await runTest('Create Product - Valid Data', async () => {
    const productData = {
      name: 'Test Product Alpha',
      sku: 'TEST-ALPHA-' + Date.now(),
      description: 'Test product for API testing',
      category: 'Electronics',
      unit_price: 99.99,
      cost_price: 50.0,
      barcode: '1234567890123',
      min_stock_level: 10,
      max_stock_level: 100,
      reorder_point: 20,
    };

    const response = await makeRequest('POST', '/api/products', productData);
    console.log('Response:', response.body);

    assert(response.status === 201, 'Status code should be 201');
    assert(response.body.success === true, 'Response should indicate success');
    assert(response.body.data.name === productData.name, 'Product name should match');
    assert(response.body.data.sku === productData.sku, 'Product SKU should match');

    if (response.body.data && response.body.data.id) {
      productId1 = response.body.data.id;
      createdProducts.push(productId1);
    }
  });

  // ==========================================
  // 2. CREATE PRODUCT - DUPLICATE SKU (409)
  // ==========================================

  await runTest('Create Product - Duplicate SKU (Should Fail)', async () => {
    const productData = {
      name: 'Test Product Beta',
      sku: 'TEST-ALPHA-' + Date.now(),
      description: 'First product with this SKU',
      category: 'Electronics',
      unit_price: 79.99,
      cost_price: 40.0,
    };

    // First create
    await makeRequest('POST', '/api/products', productData);

    // Attempt duplicate
    const response = await makeRequest('POST', '/api/products', productData);
    console.log('Response:', response.body);

    assert(response.status === 409, 'Status code should be 409 for duplicate SKU');
    assert(response.body.success === false, 'Response should indicate failure');
  });

  // ==========================================
  // 3. CREATE PRODUCT - INVALID DATA (400)
  // ==========================================

  await runTest('Create Product - Invalid Data (Should Fail)', async () => {
    const invalidData = {
      name: '',
      sku: 'INV',
      unit_price: -10,
    };

    const response = await makeRequest('POST', '/api/products', invalidData);
    console.log('Response:', response.body);

    assert(response.status === 400, 'Status code should be 400 for invalid data');
    assert(response.body.success === false, 'Response should indicate failure');
  });

  // ==========================================
  // 4. GET ALL PRODUCTS - DEFAULT PAGINATION
  // ==========================================

  await runTest('Get All Products - Default Pagination', async () => {
    const response = await makeRequest('GET', '/api/products');
    console.log('Response meta:', response.body.data?.meta || 'No meta');

    assert(response.status === 200, 'Status code should be 200');
    assert(response.body.success === true, 'Response should indicate success');
    assert(Array.isArray(response.body.data.products), 'Should return products array');
    assert(response.body.data.meta !== undefined, 'Should include pagination meta');
  });

  // ==========================================
  // 5. GET ALL PRODUCTS - CUSTOM PAGINATION
  // ==========================================

  await runTest('Get All Products - Custom Pagination', async () => {
    const response = await makeRequest('GET', '/api/products?page=1&limit=5');
    console.log('Response meta:', response.body.data?.meta);

    assert(response.status === 200, 'Status code should be 200');
    assert(response.body.data.meta.limit === 5, 'Limit should be 5');
    assert(response.body.data.meta.page === 1, 'Page should be 1');
  });

  // ==========================================
  // 6. GET SINGLE PRODUCT BY ID
  // ==========================================

  if (productId1) {
    await runTest('Get Single Product by ID', async () => {
      const response = await makeRequest('GET', `/api/products/${productId1}`);
      console.log('Response:', response.body);

      assert(response.status === 200, 'Status code should be 200');
      assert(response.body.success === true, 'Response should indicate success');
      assert(response.body.data.id === productId1, 'Product ID should match');
    });
  }

  // ==========================================
  // 7. GET SINGLE PRODUCT - NOT FOUND (404)
  // ==========================================

  await runTest('Get Single Product - Non-existent ID (Should Fail)', async () => {
    const fakeId = '99999999-0000-0000-0000-000000000000';
    const response = await makeRequest('GET', `/api/products/${fakeId}`);
    console.log('Response:', response.body);

    assert(response.status === 404, 'Status code should be 404 for non-existent ID');
    assert(response.body.success === false, 'Response should indicate failure');
  });

  // ==========================================
  // 8. UPDATE PRODUCT - PARTIAL UPDATE
  // ==========================================

  if (productId1) {
    await runTest('Update Product - Partial Update', async () => {
      const updateData = {
        name: 'Test Product Alpha - Updated',
        unit_price: 109.99,
        description: 'Updated description',
      };

      const response = await makeRequest('PUT', `/api/products/${productId1}`, updateData);
      console.log('Response:', response.body);

      assert(response.status === 200, 'Status code should be 200');
      assert(response.body.success === true, 'Response should indicate success');
      assert(response.body.data.name === updateData.name, 'Name should be updated');
      assert(
        Math.abs(response.body.data.unit_price - updateData.unit_price) < 0.01,
        'Unit price should be updated'
      );
    });
  }

  // ==========================================
  // 9. SEARCH PRODUCTS - BY NAME
  // ==========================================

  await runTest('Search Products - By Name', async () => {
    const response = await makeRequest('GET', '/api/products/search?query=Test');
    console.log('Response count:', response.body.data?.products?.length || 0);

    assert(response.status === 200, 'Status code should be 200');
    assert(response.body.success === true, 'Response should indicate success');
    assert(Array.isArray(response.body.data.products), 'Should return products array');
  });

  // ==========================================
  // 10. SEARCH PRODUCTS - BY CATEGORY
  // ==========================================

  await runTest('Search Products - By Category', async () => {
    const response = await makeRequest('GET', '/api/products/search?category=Electronics');
    console.log('Response count:', response.body.data?.products?.length || 0);

    assert(response.status === 200, 'Status code should be 200');
    assert(response.body.success === true, 'Response should indicate success');
  });

  // ==========================================
  // 11. SEARCH PRODUCTS - BY BARCODE
  // ==========================================

  await runTest('Search Products - By Barcode', async () => {
    const response = await makeRequest('GET', '/api/products/search?barcode=1234567890123');
    console.log('Response count:', response.body.data?.products?.length || 0);

    assert(response.status === 200, 'Status code should be 200');
    assert(response.body.success === true, 'Response should indicate success');
  });

  // ==========================================
  // 12. DELETE PRODUCT - SOFT DELETE
  // ==========================================

  await runTest('Delete Product - Soft Delete', async () => {
    const productData = {
      name: 'Product To Delete',
      sku: 'DELETE-TEST-' + Date.now(),
      description: 'This product will be deleted',
      category: 'Test',
      unit_price: 9.99,
      cost_price: 5.0,
    };

    const createResponse = await makeRequest('POST', '/api/products', productData);
    const deleteProductId = createResponse.body.data.id;

    const deleteResponse = await makeRequest('DELETE', `/api/products/${deleteProductId}`);
    console.log('Delete response:', deleteResponse.body);

    assert(deleteResponse.status === 200, 'Status code should be 200');
    assert(deleteResponse.body.success === true, 'Delete should succeed');

    // Verify product is soft-deleted
    const getResponse = await makeRequest('GET', `/api/products/${deleteProductId}`);
    assert(
      getResponse.status === 404,
      'Deleted product should not be found in GET request'
    );
  });

  printSection('WAREHOUSES API TESTS (5 endpoints)');

  // ==========================================
  // 1. CREATE WAREHOUSE - VALID DATA
  // ==========================================

  let warehouseId1 = null;

  await runTest('Create Warehouse - Valid Data', async () => {
    const warehouseData = {
      name: 'Test Warehouse Alpha',
      location_code: 'TW-ALPHA-' + Date.now(),
      address: '123 Test Street, Test City, TC 12345',
      type: 'distribution',
      contact_person: 'John Doe',
      contact_phone: '+1-555-0100',
      contact_email: 'john.doe@testwarehouse.com',
      is_active: true,
    };

    const response = await makeRequest('POST', '/api/warehouses', warehouseData);
    console.log('Response:', response.body);

    assert(response.status === 201, 'Status code should be 201');
    assert(response.body.success === true, 'Response should indicate success');
    assert(response.body.data.name === warehouseData.name, 'Warehouse name should match');

    if (response.body.data && response.body.data.id) {
      warehouseId1 = response.body.data.id;
      createdWarehouses.push(warehouseId1);
    }
  });

  // ==========================================
  // 2. CREATE WAREHOUSE - DUPLICATE LOCATION CODE
  // ==========================================

  await runTest('Create Warehouse - Duplicate Location Code (Should Fail)', async () => {
    const locationCode = 'TW-DUP-' + Date.now();

    const warehouseData = {
      name: 'Test Warehouse Beta',
      location_code: locationCode,
      address: '456 Another Street',
      type: 'storage',
    };

    // First create
    await makeRequest('POST', '/api/warehouses', warehouseData);

    // Attempt duplicate
    const response = await makeRequest('POST', '/api/warehouses', warehouseData);
    console.log('Response:', response.body);

    assert(
      response.status === 409,
      'Status code should be 409 for duplicate location code'
    );
    assert(response.body.success === false, 'Response should indicate failure');
  });

  // ==========================================
  // 3. CREATE WAREHOUSE - INVALID DATA
  // ==========================================

  await runTest('Create Warehouse - Invalid Data (Should Fail)', async () => {
    const invalidData = {
      name: '',
      location_code: 'INV',
      type: 'invalid_type',
    };

    const response = await makeRequest('POST', '/api/warehouses', invalidData);
    console.log('Response:', response.body);

    assert(response.status === 400, 'Status code should be 400 for invalid data');
    assert(response.body.success === false, 'Response should indicate failure');
  });

  // ==========================================
  // 4. GET ALL WAREHOUSES
  // ==========================================

  await runTest('Get All Warehouses', async () => {
    const response = await makeRequest('GET', '/api/warehouses');
    console.log('Response count:', response.body.data?.length || 0);

    assert(response.status === 200, 'Status code should be 200');
    assert(response.body.success === true, 'Response should indicate success');
    assert(Array.isArray(response.body.data), 'Should return warehouses array');
  });

  // ==========================================
  // 5. GET SINGLE WAREHOUSE BY ID
  // ==========================================

  if (warehouseId1) {
    await runTest('Get Single Warehouse by ID', async () => {
      const response = await makeRequest('GET', `/api/warehouses/${warehouseId1}`);
      console.log('Response:', response.body);

      assert(response.status === 200, 'Status code should be 200');
      assert(response.body.success === true, 'Response should indicate success');
      assert(response.body.data.id === warehouseId1, 'Warehouse ID should match');
    });
  }

  // ==========================================
  // 6. GET SINGLE WAREHOUSE - NOT FOUND
  // ==========================================

  await runTest('Get Single Warehouse - Non-existent ID (Should Fail)', async () => {
    const fakeId = '99999999-0000-0000-0000-000000000000';
    const response = await makeRequest('GET', `/api/warehouses/${fakeId}`);
    console.log('Response:', response.body);

    assert(response.status === 404, 'Status code should be 404 for non-existent ID');
    assert(response.body.success === false, 'Response should indicate failure');
  });

  // ==========================================
  // 7. UPDATE WAREHOUSE - PARTIAL UPDATE
  // ==========================================

  if (warehouseId1) {
    await runTest('Update Warehouse - Partial Update', async () => {
      const updateData = {
        name: 'Test Warehouse Alpha - Updated',
        contact_person: 'Jane Smith',
        contact_phone: '+1-555-0200',
      };

      const response = await makeRequest(
        'PUT',
        `/api/warehouses/${warehouseId1}`,
        updateData
      );
      console.log('Response:', response.body);

      assert(response.status === 200, 'Status code should be 200');
      assert(response.body.success === true, 'Response should indicate success');
      assert(response.body.data.name === updateData.name, 'Name should be updated');
      assert(
        response.body.data.contact_person === updateData.contact_person,
        'Contact person should be updated'
      );
    });
  }

  // ==========================================
  // 8. DELETE WAREHOUSE - SOFT DELETE
  // ==========================================

  await runTest('Delete Warehouse - Soft Delete', async () => {
    const warehouseData = {
      name: 'Warehouse To Delete',
      location_code: 'DELETE-WH-' + Date.now(),
      address: '999 Delete Street',
      type: 'storage',
    };

    const createResponse = await makeRequest('POST', '/api/warehouses', warehouseData);
    const deleteWarehouseId = createResponse.body.data.id;

    const deleteResponse = await makeRequest('DELETE', `/api/warehouses/${deleteWarehouseId}`);
    console.log('Delete response:', deleteResponse.body);

    assert(deleteResponse.status === 200, 'Status code should be 200');
    assert(deleteResponse.body.success === true, 'Delete should succeed');

    // Verify warehouse is soft-deleted
    const getResponse = await makeRequest('GET', `/api/warehouses/${deleteWarehouseId}`);
    assert(
      getResponse.status === 404,
      'Deleted warehouse should not be found in GET request'
    );
  });

  printSection('EDGE CASE TESTS');

  // ==========================================
  // PAGINATION EDGE CASES
  // ==========================================

  await runTest('Pagination - Page 0', async () => {
    const response = await makeRequest('GET', '/api/products?page=0&limit=10');
    assert(response.status === 200, 'Should handle page 0 gracefully');
  });

  await runTest('Pagination - Limit 0', async () => {
    const response = await makeRequest('GET', '/api/products?page=1&limit=0');
    assert(response.status === 200, 'Should handle limit 0 gracefully');
  });

  await runTest('Pagination - High Page Number', async () => {
    const response = await makeRequest('GET', '/api/products?page=999&limit=10');
    assert(response.status === 200, 'Should handle high page numbers');
    assert(
      response.body.data.products.length === 0,
      'Should return empty results for out-of-range page'
    );
  });

  // ==========================================
  // SEARCH EDGE CASES
  // ==========================================

  await runTest('Search - No Results Found', async () => {
    const response = await makeRequest(
      'GET',
      '/api/products/search?query=NONEXISTENT_PRODUCT_XYZ_12345'
    );
    assert(response.status === 200, 'Status code should be 200');
    assert(
      response.body.data.products.length === 0,
      'Should return empty array for no results'
    );
  });

  // ==========================================
  // INVALID UUID FORMAT
  // ==========================================

  await runTest('Get Product - Invalid UUID Format', async () => {
    const response = await makeRequest('GET', '/api/products/invalid-uuid-123');
    assert(response.status === 400, 'Should return 400 for invalid UUID format');
  });

  await runTest('Get Warehouse - Invalid UUID Format', async () => {
    const response = await makeRequest('GET', '/api/warehouses/not-a-uuid');
    assert(response.status === 400, 'Should return 400 for invalid UUID format');
  });

  // ==========================================
  // SUMMARY
  // ==========================================

  printSection('TEST SUMMARY');
  console.log(`Total Tests: ${totalTests}`);
  console.log(`${colors.green}Passed: ${passedTests}${colors.reset}`);
  console.log(`${colors.red}Failed: ${failedTests}${colors.reset}`);

  if (failedTests === 0) {
    console.log(`\n${colors.green}✓ ALL TESTS PASSED!${colors.reset}\n`);
  } else {
    console.log(`\n${colors.red}✗ SOME TESTS FAILED${colors.reset}\n`);
    process.exit(1);
  }
}

// Run the test suite
console.log('Waiting 2 seconds for server to be ready...\n');
setTimeout(() => {
  runTests().catch((error) => {
    console.error(`${colors.red}Fatal error:${colors.reset}`, error);
    process.exit(1);
  });
}, 2000);

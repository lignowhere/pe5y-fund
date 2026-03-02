/**
 * Multi-Channel Inventory Management System - Phase 2 API Testing (CORRECTED)
 *
 * Tests all 11 endpoints with correct field names from Prisma schema
 * Run with: node test-api-corrected.js
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
  console.log('  (With Correct Field Names)');
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
  });

  printSection('PRODUCTS API TESTS (6 endpoints)');

  // ==========================================
  // 1. CREATE PRODUCT - VALID DATA
  // ==========================================

  let productId1 = null;
  let testSKU = 'TEST-ALPHA-' + Date.now();

  await runTest('Create Product - Valid Data', async () => {
    const productData = {
      sku: testSKU,
      name: 'Test Product Alpha',
      description: 'Test product for API testing',
      category: 'Electronics',
      costPriceUsd: 50.00,
      sellingPriceUsd: 99.99,
      weightKg: 1.5,
      dimensions: {
        length: 30,
        width: 20,
        height: 10,
        unit: 'cm'
      },
      barcode: '1234567890' + Date.now(),
      imageUrl: 'https://example.com/product.jpg'
    };

    const response = await makeRequest('POST', '/api/products', productData);
    console.log('Response:', JSON.stringify(response.body, null, 2));

    assert(response.status === 201, `Status code should be 201, got ${response.status}`);

    if (response.body && response.body.data && response.body.data.id) {
      productId1 = response.body.data.id;
      assert(true, 'Product created successfully with ID: ' + productId1);
    } else {
      assert(false, 'Product ID not returned in response');
    }
  });

  // ==========================================
  // 2. CREATE PRODUCT - DUPLICATE SKU (409)
  // ==========================================

  await runTest('Create Product - Duplicate SKU (Should Return 409)', async () => {
    const productData = {
      sku: testSKU, // Same SKU as before
      name: 'Test Product Beta',
      description: 'Attempting duplicate SKU',
      category: 'Electronics',
      costPriceUsd: 40.00,
      sellingPriceUsd: 79.99
    };

    const response = await makeRequest('POST', '/api/products', productData);
    console.log('Response status:', response.status);
    console.log('Response body:', response.body);

    assert(response.status === 409, `Status code should be 409 for duplicate SKU, got ${response.status}`);
  });

  // ==========================================
  // 3. CREATE PRODUCT - INVALID DATA (400)
  // ==========================================

  await runTest('Create Product - Invalid Data (Should Return 400)', async () => {
    const invalidData = {
      sku: 'IN', // Too short
      name: '', // Empty
      costPriceUsd: -10, // Negative
      sellingPriceUsd: 'not-a-number' // Invalid type
    };

    const response = await makeRequest('POST', '/api/products', invalidData);
    console.log('Response status:', response.status);

    assert(response.status === 400, `Status code should be 400 for invalid data, got ${response.status}`);
  });

  // ==========================================
  // 4. GET ALL PRODUCTS - DEFAULT PAGINATION
  // ==========================================

  await runTest('Get All Products - Default Pagination', async () => {
    const response = await makeRequest('GET', '/api/products');
    console.log('Products count:', response.body?.data?.products?.length || 0);
    console.log('Meta:', response.body?.data?.meta);

    assert(response.status === 200, 'Status code should be 200');
    assert(response.body.success === true, 'Response should indicate success');
    assert(Array.isArray(response.body.data.products), 'Should return products array');
  });

  // ==========================================
  // 5. GET ALL PRODUCTS - CUSTOM PAGINATION
  // ==========================================

  await runTest('Get All Products - Custom Pagination (page=1, limit=5)', async () => {
    const response = await makeRequest('GET', '/api/products?page=1&limit=5');
    console.log('Products count:', response.body?.data?.products?.length || 0);
    console.log('Meta:', response.body?.data?.meta);

    assert(response.status === 200, 'Status code should be 200');
    assert(response.body.data.products.length <= 5, 'Should return at most 5 products');
  });

  // ==========================================
  // 6. GET SINGLE PRODUCT BY ID
  // ==========================================

  if (productId1) {
    await runTest('Get Single Product by ID', async () => {
      const response = await makeRequest('GET', `/api/products/${productId1}`);
      console.log('Product ID:', response.body?.data?.id);

      assert(response.status === 200, 'Status code should be 200');
      assert(response.body.data.id === productId1, 'Product ID should match');
    });
  } else {
    console.log(`${colors.red}Skipping Get Product test - no product ID${colors.reset}`);
  }

  // ==========================================
  // 7. GET SINGLE PRODUCT - NOT FOUND (404)
  // ==========================================

  await runTest('Get Single Product - Non-existent ID (Should Return 404)', async () => {
    const fakeId = '99999999-0000-0000-0000-000000000000';
    const response = await makeRequest('GET', `/api/products/${fakeId}`);
    console.log('Response status:', response.status);

    assert(response.status === 404, `Status code should be 404, got ${response.status}`);
  });

  // ==========================================
  // 8. UPDATE PRODUCT - PARTIAL UPDATE
  // ==========================================

  if (productId1) {
    await runTest('Update Product - Partial Update', async () => {
      const updateData = {
        name: 'Test Product Alpha - Updated',
        sellingPriceUsd: 109.99,
        description: 'Updated description'
      };

      const response = await makeRequest('PUT', `/api/products/${productId1}`, updateData);
      console.log('Updated product:', response.body?.data?.name);

      assert(response.status === 200, 'Status code should be 200');
      assert(response.body.data.name === updateData.name, 'Name should be updated');
    });
  } else {
    console.log(`${colors.red}Skipping Update Product test - no product ID${colors.reset}`);
  }

  // ==========================================
  // 9. SEARCH PRODUCTS
  // ==========================================

  await runTest('Search Products - By Query', async () => {
    const response = await makeRequest('GET', '/api/products/search?q=Test');
    console.log('Search results count:', response.body?.data?.products?.length || 0);

    assert(response.status === 200, 'Status code should be 200');
    assert(Array.isArray(response.body.data.products), 'Should return products array');
  });

  await runTest('Search Products - By Category', async () => {
    const response = await makeRequest('GET', '/api/products/search?category=Electronics');
    console.log('Category search results:', response.body?.data?.products?.length || 0);

    assert(response.status === 200, 'Status code should be 200');
  });

  // ==========================================
  // 10. DELETE PRODUCT - SOFT DELETE
  // ==========================================

  await runTest('Delete Product - Soft Delete', async () => {
    // Create a product to delete
    const deleteProductData = {
      sku: 'DELETE-TEST-' + Date.now(),
      name: 'Product To Delete',
      description: 'This product will be deleted',
      category: 'Test',
      costPriceUsd: 5.00,
      sellingPriceUsd: 9.99
    };

    const createResponse = await makeRequest('POST', '/api/products', deleteProductData);

    if (createResponse.status === 201 && createResponse.body.data) {
      const deleteProductId = createResponse.body.data.id;
      console.log('Created product for deletion:', deleteProductId);

      const deleteResponse = await makeRequest('DELETE', `/api/products/${deleteProductId}`);
      console.log('Delete response status:', deleteResponse.status);

      assert(deleteResponse.status === 200, 'Delete should return 200');

      // Verify product is soft-deleted (should return 404)
      const getResponse = await makeRequest('GET', `/api/products/${deleteProductId}`);
      assert(getResponse.status === 404, 'Deleted product should return 404');
    } else {
      assert(false, 'Failed to create product for deletion test');
    }
  });

  printSection('WAREHOUSES API TESTS (5 endpoints)');

  // ==========================================
  // 1. CREATE WAREHOUSE - VALID DATA
  // ==========================================

  let warehouseId1 = null;
  let testLocationCode = 'TW-ALPHA-' + Date.now();

  await runTest('Create Warehouse - Valid Data', async () => {
    const warehouseData = {
      name: 'Test Warehouse Alpha',
      locationCode: testLocationCode,
      address: '123 Test Street, Test City, TC 12345',
      country: 'United States'
    };

    const response = await makeRequest('POST', '/api/warehouses', warehouseData);
    console.log('Response:', JSON.stringify(response.body, null, 2));

    assert(response.status === 201, `Status code should be 201, got ${response.status}`);

    if (response.body && response.body.data && response.body.data.id) {
      warehouseId1 = response.body.data.id;
      assert(true, 'Warehouse created successfully with ID: ' + warehouseId1);
    } else {
      assert(false, 'Warehouse ID not returned in response');
    }
  });

  // ==========================================
  // 2. CREATE WAREHOUSE - DUPLICATE LOCATION CODE
  // ==========================================

  await runTest('Create Warehouse - Duplicate Location Code (Should Return 409)', async () => {
    const warehouseData = {
      name: 'Test Warehouse Beta',
      locationCode: testLocationCode, // Same location code
      address: '456 Another Street',
      country: 'Canada'
    };

    const response = await makeRequest('POST', '/api/warehouses', warehouseData);
    console.log('Response status:', response.status);

    assert(response.status === 409, `Status code should be 409 for duplicate location code, got ${response.status}`);
  });

  // ==========================================
  // 3. CREATE WAREHOUSE - INVALID DATA
  // ==========================================

  await runTest('Create Warehouse - Invalid Data (Should Return 400)', async () => {
    const invalidData = {
      name: '', // Empty
      locationCode: '' // Empty
    };

    const response = await makeRequest('POST', '/api/warehouses', invalidData);
    console.log('Response status:', response.status);

    assert(response.status === 400, `Status code should be 400 for invalid data, got ${response.status}`);
  });

  // ==========================================
  // 4. GET ALL WAREHOUSES
  // ==========================================

  await runTest('Get All Warehouses', async () => {
    const response = await makeRequest('GET', '/api/warehouses');
    console.log('Warehouses count:', response.body?.data?.length || 0);

    assert(response.status === 200, 'Status code should be 200');
    assert(Array.isArray(response.body.data), 'Should return warehouses array');
  });

  // ==========================================
  // 5. GET SINGLE WAREHOUSE BY ID
  // ==========================================

  if (warehouseId1) {
    await runTest('Get Single Warehouse by ID', async () => {
      const response = await makeRequest('GET', `/api/warehouses/${warehouseId1}`);
      console.log('Warehouse ID:', response.body?.data?.id);

      assert(response.status === 200, 'Status code should be 200');
      assert(response.body.data.id === warehouseId1, 'Warehouse ID should match');
    });
  } else {
    console.log(`${colors.red}Skipping Get Warehouse test - no warehouse ID${colors.reset}`);
  }

  // ==========================================
  // 6. GET SINGLE WAREHOUSE - NOT FOUND
  // ==========================================

  await runTest('Get Single Warehouse - Non-existent ID (Should Return 404)', async () => {
    const fakeId = '99999999-0000-0000-0000-000000000000';
    const response = await makeRequest('GET', `/api/warehouses/${fakeId}`);
    console.log('Response status:', response.status);

    assert(response.status === 404, `Status code should be 404, got ${response.status}`);
  });

  // ==========================================
  // 7. UPDATE WAREHOUSE - PARTIAL UPDATE
  // ==========================================

  if (warehouseId1) {
    await runTest('Update Warehouse - Partial Update', async () => {
      const updateData = {
        name: 'Test Warehouse Alpha - Updated',
        country: 'Mexico'
      };

      const response = await makeRequest('PUT', `/api/warehouses/${warehouseId1}`, updateData);
      console.log('Updated warehouse:', response.body?.data?.name);

      assert(response.status === 200, 'Status code should be 200');
      assert(response.body.data.name === updateData.name, 'Name should be updated');
    });
  } else {
    console.log(`${colors.red}Skipping Update Warehouse test - no warehouse ID${colors.reset}`);
  }

  // ==========================================
  // 8. DELETE WAREHOUSE - SOFT DELETE
  // ==========================================

  await runTest('Delete Warehouse - Soft Delete', async () => {
    // Create a warehouse to delete
    const deleteWarehouseData = {
      name: 'Warehouse To Delete',
      locationCode: 'DELETE-WH-' + Date.now(),
      address: '999 Delete Street'
    };

    const createResponse = await makeRequest('POST', '/api/warehouses', deleteWarehouseData);

    if (createResponse.status === 201 && createResponse.body.data) {
      const deleteWarehouseId = createResponse.body.data.id;
      console.log('Created warehouse for deletion:', deleteWarehouseId);

      const deleteResponse = await makeRequest('DELETE', `/api/warehouses/${deleteWarehouseId}`);
      console.log('Delete response status:', deleteResponse.status);

      assert(deleteResponse.status === 200, 'Delete should return 200');

      // Verify warehouse is soft-deleted (should return 404)
      const getResponse = await makeRequest('GET', `/api/warehouses/${deleteWarehouseId}`);
      assert(getResponse.status === 404, 'Deleted warehouse should return 404');
    } else {
      assert(false, 'Failed to create warehouse for deletion test');
    }
  });

  printSection('EDGE CASE TESTS');

  // ==========================================
  // PAGINATION EDGE CASES
  // ==========================================

  await runTest('Pagination - Page 0', async () => {
    const response = await makeRequest('GET', '/api/products?page=0&limit=10');
    assert(response.status === 200, 'Should handle page 0 gracefully');
  });

  await runTest('Pagination - Negative Limit', async () => {
    const response = await makeRequest('GET', '/api/products?page=1&limit=-5');
    assert(response.status === 200, 'Should handle negative limit gracefully');
  });

  await runTest('Pagination - High Page Number', async () => {
    const response = await makeRequest('GET', '/api/products?page=9999&limit=10');
    assert(response.status === 200, 'Should handle high page numbers');
  });

  // ==========================================
  // SEARCH EDGE CASES
  // ==========================================

  await runTest('Search - No Results', async () => {
    const response = await makeRequest('GET', '/api/products/search?q=NONEXISTENT_PRODUCT_XYZ_12345');
    assert(response.status === 200, 'Status code should be 200');
    assert(response.body.data.products.length === 0, 'Should return empty array');
  });

  await runTest('Search - Empty Query', async () => {
    const response = await makeRequest('GET', '/api/products/search?q=');
    assert(response.status === 200, 'Should handle empty query');
  });

  // ==========================================
  // INVALID UUID FORMAT
  // ==========================================

  await runTest('Get Product - Invalid UUID Format', async () => {
    const response = await makeRequest('GET', '/api/products/invalid-uuid-123');
    assert(response.status === 400, `Should return 400 for invalid UUID, got ${response.status}`);
  });

  await runTest('Get Warehouse - Invalid UUID Format', async () => {
    const response = await makeRequest('GET', '/api/warehouses/not-a-uuid');
    assert(response.status === 400, `Should return 400 for invalid UUID, got ${response.status}`);
  });

  // ==========================================
  // SUMMARY
  // ==========================================

  printSection('TEST SUMMARY');
  console.log(`Total Tests: ${totalTests}`);
  console.log(`${colors.green}Passed: ${passedTests}${colors.reset}`);
  console.log(`${colors.red}Failed: ${failedTests}${colors.reset}`);

  const passRate = ((passedTests / totalTests) * 100).toFixed(2);
  console.log(`Pass Rate: ${passRate}%`);

  if (failedTests === 0) {
    console.log(`\n${colors.green}✓ ALL TESTS PASSED!${colors.reset}\n`);
  } else {
    console.log(`\n${colors.red}✗ ${failedTests} TEST(S) FAILED${colors.reset}\n`);
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

# Phase 2 Backend API Testing Guide

## Overview

This document provides comprehensive testing procedures for the Multi-Channel Inventory Management System Phase 2 Backend API implementation.

**Tested Components:**
- 6 Products API endpoints
- 5 Warehouses API endpoints
- Validation (Zod schemas)
- Error handling
- Edge cases

## Prerequisites

1. **PostgreSQL Database Running**
   - Database: `inventory_db`
   - Port: 5432
   - Seed data loaded

2. **Backend Server**
   - Port: 3001
   - Node.js environment configured
   - Environment variables set (`.env` file)

3. **Dependencies Installed**
   ```bash
   cd /Users/0xtonytr/Documents/test-claudekit/backend
   npm install
   ```

## Starting the Backend Server

### Option 1: Development Mode (with auto-reload)
```bash
cd /Users/0xtonytr/Documents/test-claudekit/backend
npm run dev
```

### Option 2: Production Mode
```bash
cd /Users/0xtonytr/Documents/test-claudekit/backend
npm run build
npm start
```

### Verify Server is Running
```bash
curl http://localhost:3001/health
```

Expected response:
```json
{
  "status": "ok",
  "timestamp": "2025-12-07T...",
  "database": "connected"
}
```

## Running Tests

### Automated Test Suite (Node.js)

**Recommended method** - Comprehensive automated testing:

```bash
cd /Users/0xtonytr/Documents/test-claudekit/backend
node test-api.js
```

**What it tests:**
- All 11 API endpoints (6 Products + 5 Warehouses)
- Validation scenarios (400, 409 errors)
- Success scenarios (200, 201 responses)
- Edge cases (pagination, search, invalid UUIDs)
- Soft delete functionality
- Duplicate constraint violations

**Expected Output:**
```
==========================================
  Phase 2 API Testing - Backend
==========================================
Backend: http://localhost:3001

=== Health Check ===
✓ PASS: Status code should be 200
✓ PASS: Health status should be ok

...

==========================================
  TEST SUMMARY
==========================================
Total Tests: 40
Passed: 40
Failed: 0

✓ ALL TESTS PASSED!
```

### Shell Script Testing (Bash)

For Unix-based systems (macOS, Linux):

```bash
cd /Users/0xtonytr/Documents/test-claudekit/backend
chmod +x test-api.sh
./test-api.sh
```

## API Endpoints Reference

### Products API (6 endpoints)

| Method | Endpoint | Description | Expected Status |
|--------|----------|-------------|----------------|
| POST | `/api/products` | Create product | 201 |
| GET | `/api/products` | List all (paginated) | 200 |
| GET | `/api/products/:id` | Get single product | 200 |
| PUT | `/api/products/:id` | Update product | 200 |
| DELETE | `/api/products/:id` | Soft delete | 200 |
| GET | `/api/products/search` | Search by filters | 200 |

**Search Parameters:**
- `query` - Search by name or description
- `sku` - Exact SKU match
- `barcode` - Exact barcode match
- `category` - Category filter

**Pagination Parameters:**
- `page` - Page number (default: 1)
- `limit` - Items per page (default: 10)

### Warehouses API (5 endpoints)

| Method | Endpoint | Description | Expected Status |
|--------|----------|-------------|----------------|
| POST | `/api/warehouses` | Create warehouse | 201 |
| GET | `/api/warehouses` | List all | 200 |
| GET | `/api/warehouses/:id` | Get single warehouse | 200 |
| PUT | `/api/warehouses/:id` | Update warehouse | 200 |
| DELETE | `/api/warehouses/:id` | Soft delete | 200 |

## Manual Testing Examples

### Create Product

```bash
curl -X POST http://localhost:3001/api/products \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test Product",
    "sku": "TEST-001",
    "description": "A test product",
    "category": "Electronics",
    "unit_price": 99.99,
    "cost_price": 50.00,
    "barcode": "1234567890123",
    "min_stock_level": 10,
    "max_stock_level": 100,
    "reorder_point": 20
  }'
```

### Get All Products with Pagination

```bash
curl "http://localhost:3001/api/products?page=1&limit=10"
```

### Search Products

```bash
# By name
curl "http://localhost:3001/api/products/search?query=Laptop"

# By SKU
curl "http://localhost:3001/api/products/search?sku=PROD-001"

# By category
curl "http://localhost:3001/api/products/search?category=Electronics"
```

### Create Warehouse

```bash
curl -X POST http://localhost:3001/api/warehouses \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Main Warehouse",
    "location_code": "WH-001",
    "address": "123 Main St, City, State 12345",
    "type": "distribution",
    "contact_person": "John Doe",
    "contact_phone": "+1-555-0100",
    "contact_email": "john@example.com",
    "is_active": true
  }'
```

### Update Warehouse

```bash
curl -X PUT http://localhost:3001/api/warehouses/{warehouse-id} \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Main Warehouse - Updated",
    "contact_person": "Jane Smith"
  }'
```

### Delete Product (Soft Delete)

```bash
curl -X DELETE http://localhost:3001/api/products/{product-id}
```

## Validation Testing

### Expected Validation Errors (400)

**Invalid Product Data:**
```bash
curl -X POST http://localhost:3001/api/products \
  -H "Content-Type: application/json" \
  -d '{
    "name": "",
    "sku": "INV",
    "unit_price": -10
  }'
```

**Expected Response:**
```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Validation failed",
    "details": [...]
  }
}
```

### Expected Conflict Errors (409)

**Duplicate SKU:**
```bash
# Create first product
curl -X POST http://localhost:3001/api/products \
  -H "Content-Type: application/json" \
  -d '{"name": "Product A", "sku": "DUP-001", ...}'

# Attempt duplicate SKU
curl -X POST http://localhost:3001/api/products \
  -H "Content-Type: application/json" \
  -d '{"name": "Product B", "sku": "DUP-001", ...}'
```

**Expected Response:**
```json
{
  "success": false,
  "error": {
    "code": "CONFLICT",
    "message": "Product with this SKU already exists"
  }
}
```

### Expected Not Found Errors (404)

**Non-existent Product:**
```bash
curl http://localhost:3001/api/products/99999999-0000-0000-0000-000000000000
```

**Expected Response:**
```json
{
  "success": false,
  "error": {
    "code": "NOT_FOUND",
    "message": "Product not found"
  }
}
```

## Test Scenarios Covered

### ✅ Success Scenarios

1. **Create Resources**
   - Create product with all valid fields
   - Create warehouse with all valid fields
   - Verify returned data matches input

2. **Read Resources**
   - Get all products with pagination
   - Get single product by ID
   - Get all warehouses
   - Get single warehouse by ID

3. **Update Resources**
   - Partial update of product
   - Partial update of warehouse
   - Verify updates are persisted

4. **Delete Resources**
   - Soft delete product
   - Soft delete warehouse
   - Verify deleted resources not returned in queries

5. **Search & Filter**
   - Search products by name
   - Search by SKU
   - Search by barcode
   - Search by category
   - Pagination with custom limits

### ✅ Error Scenarios

1. **Validation Errors (400)**
   - Empty required fields
   - Invalid data types
   - Out-of-range values
   - Invalid enum values
   - Invalid UUID format

2. **Conflict Errors (409)**
   - Duplicate product SKU
   - Duplicate warehouse location_code
   - Delete warehouse with active inventory

3. **Not Found Errors (404)**
   - Get non-existent product
   - Get non-existent warehouse
   - Update non-existent resource
   - Delete non-existent resource
   - Get soft-deleted resource

### ✅ Edge Cases

1. **Pagination**
   - Page 0 handling
   - Limit 0 handling
   - Out-of-range page numbers
   - Large page numbers (no results)

2. **Search**
   - No results found
   - Empty query strings
   - Special characters in search

3. **Data Integrity**
   - UUID validation
   - Decimal precision (prices)
   - DateTime handling
   - Null vs empty string handling

## Performance Observations

**Metrics to Monitor:**

1. **Response Times**
   - List endpoints: < 200ms
   - Single resource: < 100ms
   - Create/Update: < 150ms
   - Search: < 300ms

2. **Database Queries**
   - N+1 query prevention
   - Index utilization
   - Query plan optimization

3. **Memory Usage**
   - No memory leaks
   - Efficient data structures
   - Proper connection pooling

## Common Issues & Troubleshooting

### Server Won't Start

**Issue:** Port already in use
```
Error: listen EADDRINUSE: address already in use :::3001
```

**Solution:**
```bash
# Kill process on port 3001
lsof -ti:3001 | xargs kill -9

# Or use different port
PORT=3002 npm run dev
```

### Database Connection Failed

**Issue:** Cannot connect to PostgreSQL

**Solution:**
```bash
# Check PostgreSQL is running
pg_isready -h localhost -p 5432

# Verify database exists
psql -U 0xtonytr -l | grep inventory_db

# Check .env DATABASE_URL
cat .env | grep DATABASE_URL
```

### Tests Failing

**Issue:** Random test failures

**Checklist:**
1. Ensure server is running
2. Database has seed data
3. No port conflicts
4. Correct environment variables
5. Network connectivity

### Validation Errors

**Issue:** Unexpected validation failures

**Debug:**
```bash
# Check Zod schema definitions
cat src/products/product.schemas.ts
cat src/warehouses/warehouse.schemas.ts

# Verify request payload format
curl -v -X POST http://localhost:3001/api/products \
  -H "Content-Type: application/json" \
  -d @test-product.json
```

## Test Coverage Summary

**Total Endpoints:** 11
- Products: 6/6 ✅
- Warehouses: 5/5 ✅

**Test Categories:**
- CRUD Operations: ✅ Complete
- Validation: ✅ Complete
- Error Handling: ✅ Complete
- Edge Cases: ✅ Complete
- Performance: ⚠️  Manual monitoring required

**HTTP Status Codes Tested:**
- 200 (OK): ✅
- 201 (Created): ✅
- 400 (Bad Request): ✅
- 404 (Not Found): ✅
- 409 (Conflict): ✅
- 500 (Server Error): ⚠️  Depends on runtime errors

## Next Steps

1. **Integration Tests**
   - Test cross-module interactions
   - Inventory + Products + Warehouses
   - Movement tracking

2. **Load Testing**
   - Concurrent user simulation
   - Stress testing
   - Performance benchmarks

3. **Security Testing**
   - SQL injection prevention
   - XSS protection
   - Authentication/Authorization (Phase 3)

4. **CI/CD Integration**
   - Automated test runs
   - Test coverage reporting
   - Quality gates

## Appendix: Test Data Examples

### Valid Product Data

```json
{
  "name": "Wireless Mouse",
  "sku": "MOUSE-WL-001",
  "description": "Ergonomic wireless mouse with USB receiver",
  "category": "Electronics",
  "unit_price": 29.99,
  "cost_price": 15.00,
  "barcode": "1234567890123",
  "min_stock_level": 20,
  "max_stock_level": 200,
  "reorder_point": 50
}
```

### Valid Warehouse Data

```json
{
  "name": "Central Distribution Center",
  "location_code": "CDC-001",
  "address": "500 Industrial Blvd, Springfield, IL 62702",
  "type": "distribution",
  "contact_person": "Sarah Johnson",
  "contact_phone": "+1-217-555-0100",
  "contact_email": "sarah.johnson@warehouse.com",
  "is_active": true
}
```

### Invalid Product Data (for validation testing)

```json
{
  "name": "",
  "sku": "AB",
  "unit_price": -50,
  "cost_price": "not-a-number"
}
```

## Report Generation

After running tests, create a report:

```bash
node test-api.js > test-results-$(date +%Y%m%d-%H%M%S).log 2>&1
```

This will generate a timestamped log file with all test results.

---

**Last Updated:** 2025-12-07
**Version:** 1.0.0
**Status:** Phase 2 Complete

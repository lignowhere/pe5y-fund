#!/bin/bash

# Multi-Channel Inventory Management System - Phase 2 API Testing Script
# Backend: http://localhost:3001
# Tests all 11 endpoints (6 Products + 5 Warehouses)

BASE_URL="http://localhost:3001"
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test counters
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

# Function to print test header
print_test() {
  echo -e "\n${YELLOW}=== $1 ===${NC}"
  TOTAL_TESTS=$((TOTAL_TESTS + 1))
}

# Function to print success
print_success() {
  echo -e "${GREEN}✓ PASS${NC}: $1"
  PASSED_TESTS=$((PASSED_TESTS + 1))
}

# Function to print failure
print_failure() {
  echo -e "${RED}✗ FAIL${NC}: $1"
  FAILED_TESTS=$((FAILED_TESTS + 1))
}

# Function to make HTTP request and check response
test_endpoint() {
  local method=$1
  local url=$2
  local data=$3
  local expected_status=$4
  local description=$5

  print_test "$description"
  echo "Request: $method $url"

  if [ -n "$data" ]; then
    echo "Body: $data"
    response=$(curl -s -w "\n%{http_code}" -X $method "$BASE_URL$url" \
      -H "Content-Type: application/json" \
      -d "$data")
  else
    response=$(curl -s -w "\n%{http_code}" -X $method "$BASE_URL$url" \
      -H "Content-Type: application/json")
  fi

  status=$(echo "$response" | tail -n1)
  body=$(echo "$response" | sed '$d')

  echo "Response Status: $status"
  echo "Response Body: $body"

  if [ "$status" = "$expected_status" ]; then
    print_success "$description"
  else
    print_failure "Expected $expected_status, got $status"
  fi

  echo "$body"
}

echo "=========================================="
echo "  Phase 2 API Testing - Backend"
echo "=========================================="
echo "Backend URL: $BASE_URL"
echo ""

# ==========================================
# HEALTH CHECK
# ==========================================

print_test "Health Check"
health=$(curl -s "$BASE_URL/health")
echo "Response: $health"
if echo "$health" | grep -q "ok"; then
  print_success "Health check passed"
else
  print_failure "Health check failed"
fi

# ==========================================
# DATABASE CONNECTION TEST
# ==========================================

print_test "Database Connection Test"
db_test=$(curl -s "$BASE_URL/api/test-db")
echo "Response: $db_test"
if echo "$db_test" | grep -q "Database connected successfully"; then
  print_success "Database connection successful"
else
  print_failure "Database connection failed"
fi

echo ""
echo "=========================================="
echo "  PRODUCTS API TESTS (6 endpoints)"
echo "=========================================="

# ==========================================
# 1. CREATE PRODUCT (POST /api/products)
# ==========================================

test_endpoint "POST" "/api/products" \
'{
  "name": "Test Product Alpha",
  "sku": "TEST-ALPHA-001",
  "description": "Test product for API testing",
  "category": "Electronics",
  "unit_price": 99.99,
  "cost_price": 50.00,
  "barcode": "1234567890123",
  "min_stock_level": 10,
  "max_stock_level": 100,
  "reorder_point": 20
}' \
"201" \
"Create Product - Valid Data"

# Save product ID for later tests
PRODUCT_ID_1=$(echo "$body" | grep -o '"id":"[^"]*' | cut -d'"' -f4 | head -n1)

# ==========================================
# 2. CREATE PRODUCT - DUPLICATE SKU (409)
# ==========================================

test_endpoint "POST" "/api/products" \
'{
  "name": "Test Product Beta",
  "sku": "TEST-ALPHA-001",
  "description": "Duplicate SKU test",
  "category": "Electronics",
  "unit_price": 79.99,
  "cost_price": 40.00
}' \
"409" \
"Create Product - Duplicate SKU (Should Fail)"

# ==========================================
# 3. CREATE PRODUCT - INVALID DATA (400)
# ==========================================

test_endpoint "POST" "/api/products" \
'{
  "name": "",
  "sku": "INVALID",
  "unit_price": -10
}' \
"400" \
"Create Product - Invalid Data (Should Fail)"

# ==========================================
# 4. GET ALL PRODUCTS (GET /api/products)
# ==========================================

test_endpoint "GET" "/api/products" "" "200" \
"Get All Products - Default Pagination"

# ==========================================
# 5. GET ALL PRODUCTS WITH PAGINATION
# ==========================================

test_endpoint "GET" "/api/products?page=1&limit=5" "" "200" \
"Get All Products - Custom Pagination (page=1, limit=5)"

# ==========================================
# 6. GET SINGLE PRODUCT (GET /api/products/:id)
# ==========================================

if [ -n "$PRODUCT_ID_1" ]; then
  test_endpoint "GET" "/api/products/$PRODUCT_ID_1" "" "200" \
  "Get Single Product by ID"
else
  print_failure "Cannot test Get Single Product - No product ID available"
fi

# ==========================================
# 7. GET SINGLE PRODUCT - NOT FOUND (404)
# ==========================================

test_endpoint "GET" "/api/products/99999999-0000-0000-0000-000000000000" "" "404" \
"Get Single Product - Non-existent ID (Should Fail)"

# ==========================================
# 8. UPDATE PRODUCT (PUT /api/products/:id)
# ==========================================

if [ -n "$PRODUCT_ID_1" ]; then
  test_endpoint "PUT" "/api/products/$PRODUCT_ID_1" \
'{
  "name": "Test Product Alpha - Updated",
  "unit_price": 109.99,
  "description": "Updated description"
}' \
"200" \
"Update Product - Partial Update"
else
  print_failure "Cannot test Update Product - No product ID available"
fi

# ==========================================
# 9. SEARCH PRODUCTS (GET /api/products/search)
# ==========================================

test_endpoint "GET" "/api/products/search?query=Test" "" "200" \
"Search Products - By Name"

test_endpoint "GET" "/api/products/search?sku=TEST-ALPHA-001" "" "200" \
"Search Products - By SKU"

test_endpoint "GET" "/api/products/search?category=Electronics" "" "200" \
"Search Products - By Category"

test_endpoint "GET" "/api/products/search?barcode=1234567890123" "" "200" \
"Search Products - By Barcode"

# ==========================================
# 10. DELETE PRODUCT (DELETE /api/products/:id)
# ==========================================

# Create a product specifically for deletion
delete_test_product=$(test_endpoint "POST" "/api/products" \
'{
  "name": "Product To Delete",
  "sku": "DELETE-TEST-001",
  "description": "This product will be deleted",
  "category": "Test",
  "unit_price": 9.99,
  "cost_price": 5.00
}' \
"201" \
"Create Product for Deletion Test")

DELETE_PRODUCT_ID=$(echo "$delete_test_product" | grep -o '"id":"[^"]*' | cut -d'"' -f4 | tail -n1)

if [ -n "$DELETE_PRODUCT_ID" ]; then
  test_endpoint "DELETE" "/api/products/$DELETE_PRODUCT_ID" "" "200" \
  "Delete Product - Soft Delete"

  # Verify product is soft-deleted (should not appear in normal queries)
  test_endpoint "GET" "/api/products/$DELETE_PRODUCT_ID" "" "404" \
  "Verify Product Deleted - Should Not Be Found"
else
  print_failure "Cannot test Delete Product - No product ID for deletion"
fi

echo ""
echo "=========================================="
echo "  WAREHOUSES API TESTS (5 endpoints)"
echo "=========================================="

# ==========================================
# 1. CREATE WAREHOUSE (POST /api/warehouses)
# ==========================================

test_endpoint "POST" "/api/warehouses" \
'{
  "name": "Test Warehouse Alpha",
  "location_code": "TW-ALPHA-001",
  "address": "123 Test Street, Test City, TC 12345",
  "type": "distribution",
  "contact_person": "John Doe",
  "contact_phone": "+1-555-0100",
  "contact_email": "john.doe@testwarehouse.com",
  "is_active": true
}' \
"201" \
"Create Warehouse - Valid Data"

# Save warehouse ID for later tests
WAREHOUSE_ID_1=$(echo "$body" | grep -o '"id":"[^"]*' | cut -d'"' -f4 | head -n1)

# ==========================================
# 2. CREATE WAREHOUSE - DUPLICATE LOCATION CODE (409)
# ==========================================

test_endpoint "POST" "/api/warehouses" \
'{
  "name": "Test Warehouse Beta",
  "location_code": "TW-ALPHA-001",
  "address": "456 Another Street",
  "type": "storage"
}' \
"409" \
"Create Warehouse - Duplicate Location Code (Should Fail)"

# ==========================================
# 3. CREATE WAREHOUSE - INVALID DATA (400)
# ==========================================

test_endpoint "POST" "/api/warehouses" \
'{
  "name": "",
  "location_code": "INV",
  "type": "invalid_type"
}' \
"400" \
"Create Warehouse - Invalid Data (Should Fail)"

# ==========================================
# 4. GET ALL WAREHOUSES (GET /api/warehouses)
# ==========================================

test_endpoint "GET" "/api/warehouses" "" "200" \
"Get All Warehouses"

# ==========================================
# 5. GET SINGLE WAREHOUSE (GET /api/warehouses/:id)
# ==========================================

if [ -n "$WAREHOUSE_ID_1" ]; then
  test_endpoint "GET" "/api/warehouses/$WAREHOUSE_ID_1" "" "200" \
  "Get Single Warehouse by ID"
else
  print_failure "Cannot test Get Single Warehouse - No warehouse ID available"
fi

# ==========================================
# 6. GET SINGLE WAREHOUSE - NOT FOUND (404)
# ==========================================

test_endpoint "GET" "/api/warehouses/99999999-0000-0000-0000-000000000000" "" "404" \
"Get Single Warehouse - Non-existent ID (Should Fail)"

# ==========================================
# 7. UPDATE WAREHOUSE (PUT /api/warehouses/:id)
# ==========================================

if [ -n "$WAREHOUSE_ID_1" ]; then
  test_endpoint "PUT" "/api/warehouses/$WAREHOUSE_ID_1" \
'{
  "name": "Test Warehouse Alpha - Updated",
  "contact_person": "Jane Smith",
  "contact_phone": "+1-555-0200"
}' \
"200" \
"Update Warehouse - Partial Update"
else
  print_failure "Cannot test Update Warehouse - No warehouse ID available"
fi

# ==========================================
# 8. DELETE WAREHOUSE - WITH ACTIVE INVENTORY (409)
# ==========================================

# Note: This test assumes there's no inventory linked to our test warehouse
# In a real scenario, you'd need to create inventory first

# ==========================================
# 9. DELETE WAREHOUSE (DELETE /api/warehouses/:id)
# ==========================================

# Create a warehouse specifically for deletion
delete_test_warehouse=$(test_endpoint "POST" "/api/warehouses" \
'{
  "name": "Warehouse To Delete",
  "location_code": "DELETE-WH-001",
  "address": "999 Delete Street",
  "type": "storage"
}' \
"201" \
"Create Warehouse for Deletion Test")

DELETE_WAREHOUSE_ID=$(echo "$delete_test_warehouse" | grep -o '"id":"[^"]*' | cut -d'"' -f4 | tail -n1)

if [ -n "$DELETE_WAREHOUSE_ID" ]; then
  test_endpoint "DELETE" "/api/warehouses/$DELETE_WAREHOUSE_ID" "" "200" \
  "Delete Warehouse - Soft Delete"

  # Verify warehouse is soft-deleted
  test_endpoint "GET" "/api/warehouses/$DELETE_WAREHOUSE_ID" "" "404" \
  "Verify Warehouse Deleted - Should Not Be Found"
else
  print_failure "Cannot test Delete Warehouse - No warehouse ID for deletion"
fi

# ==========================================
# EDGE CASE TESTS
# ==========================================

echo ""
echo "=========================================="
echo "  EDGE CASE TESTS"
echo "=========================================="

# Test pagination edge cases
test_endpoint "GET" "/api/products?page=0&limit=10" "" "200" \
"Pagination - Page 0 (Should handle gracefully)"

test_endpoint "GET" "/api/products?page=1&limit=0" "" "200" \
"Pagination - Limit 0 (Should handle gracefully)"

test_endpoint "GET" "/api/products?page=999&limit=10" "" "200" \
"Pagination - High Page Number (Empty results)"

# Test search with no results
test_endpoint "GET" "/api/products/search?query=NONEXISTENT_PRODUCT_XYZ" "" "200" \
"Search - No Results Found"

# Test invalid UUID format
test_endpoint "GET" "/api/products/invalid-uuid" "" "400" \
"Get Product - Invalid UUID Format (Should Fail)"

test_endpoint "GET" "/api/warehouses/not-a-uuid" "" "400" \
"Get Warehouse - Invalid UUID Format (Should Fail)"

# ==========================================
# SUMMARY
# ==========================================

echo ""
echo "=========================================="
echo "  TEST SUMMARY"
echo "=========================================="
echo -e "Total Tests: $TOTAL_TESTS"
echo -e "${GREEN}Passed: $PASSED_TESTS${NC}"
echo -e "${RED}Failed: $FAILED_TESTS${NC}"

if [ $FAILED_TESTS -eq 0 ]; then
  echo -e "\n${GREEN}✓ ALL TESTS PASSED!${NC}"
  exit 0
else
  echo -e "\n${RED}✗ SOME TESTS FAILED${NC}"
  exit 1
fi

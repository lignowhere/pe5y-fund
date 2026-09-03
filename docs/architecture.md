# Kiến trúc

## Runtime

PE5Y là một ứng dụng đơn máy, chỉ bind localhost:

```text
Trình duyệt
  │ http://127.0.0.1:8002
  ▼
Next.js :3000 ──────► FastAPI
                        │
                        ├─ fund: snapshot/planner/holdings
                        ├─ data: sync/provenance/migration
                        └─ strategy: signal/ADV helpers
                                  │
                                  ▼
                         vietnam_stocks.db
```

`start.bat` gọi `start.ps1`, chạy FastAPI bằng Python trong `.venv` và chạy
frontend từ production build. PID và log nằm trong `logs/`.

## Module

| Đường dẫn | Trách nhiệm |
|---|---|
| `backend/main.py` | Khởi tạo FastAPI và router |
| `backend/api/` | Validate HTTP, chuyển lỗi domain thành status code |
| `backend/fund/` | Chu kỳ active, snapshot, planner, performance, holdings |
| `backend/data/` | Đồng bộ, staging, provenance, revision, migration |
| `backend/database/` | Connection helper và transaction |
| `backend/strategy/` | Tạo tín hiệu PIT, lọc và tính ADV |
| `backend/backtest/` | Rebuild snapshot và nghiên cứu TTM/LAST8 10 năm |
| `frontend/src/app/` | Màn hình planner, dữ liệu, cấu hình, xác minh |
| `scripts/` | Setup, Scheduled Task, importer, legacy inventory |
| `tests/` | Regression test dữ liệu và nghiệp vụ |

Không còn backend Inventory/Prisma, SEO crawler hoặc bộ agent template trong
repository này.

## Luồng authoritative

```text
Tài liệu/quan sát có checksum
  → revision append-only
  → chọn fact có available_at <= signal_cutoff
  → tạo tín hiệu tại close trước 01/09
  → mô phỏng mua tại open phiên đầu từ 01/09
  → snapshot staging + checksum
  → kiểm tra portfolio/performance/backtest readiness
  → kích hoạt nguyên tử
  → POST /api/fund/portfolio-plan
```

Planner không gọi hàm tạo tín hiệu. Nó chỉ đọc cycle snapshot active. Nếu build
mới thất bại, config và snapshot cũ không đổi.

## Cổng an toàn

- `portfolio_ready`: đủ dữ liệu để xác định danh sách và số lượng mục tiêu.
- `performance_ready`: sổ corporate action và cash dividend tái tạo được hiệu
  suất.
- `backtest_ready`: các chu kỳ strict PIT và benchmark total-return đều được
  xác minh.

`investment_ready` chỉ đúng khi các cổng bắt buộc của chế độ đầu tư đạt. Cờ
`PE5Y_ALLOW_LEGACY_RESEARCH_PLANNER` chỉ cho phép đọc snapshot nghiên cứu; nó
không thay đổi các cổng này.

`user_confirmed_ready` là cổng riêng cho snapshot `trusted_local`. Nó chỉ đúng
khi có bản ghi xác nhận đang hoạt động, snapshot bất biến đủ tối thiểu 15 mã và
cấu hình khớp fingerprint. API/UI/CSV luôn trả `trust_tier = trusted_local`;
cờ này không được dùng để suy ra official provenance.

## API chính

| Method | Path | Mục đích |
|---|---|---|
| `POST` | `/api/fund/portfolio-plan` | Lập danh mục từ snapshot |
| `GET/PUT` | `/api/fund/preferences` | Chiến lược mặc định |
| `GET/PUT/DELETE` | `/api/fund/holdings` | Danh mục hiện tại |
| `GET` | `/api/data/sync/status` | Sync và readiness |
| `POST` | `/api/data/sync/start` | Chạy sync nền |
| `POST` | `/api/data/sync/cancel` | Yêu cầu hủy sync |
| `GET` | `/api/data/health` | Coverage/freshness đã tổng hợp |
| `GET/PUT` | `/api/strategy/config` | Cấu hình active/pending |

Endpoint chiến lược động cũ chỉ còn tombstone HTTP 410 để client cũ không tạo
ra kết quả khác snapshot.

# Phát triển và kiểm thử

## Nguyên tắc bắt buộc

1. Fail closed: thiếu nguồn, checksum, giá hoặc corporate action thì trả lỗi có
   cấu trúc; không bỏ qua mã.
2. Planner không tạo tín hiệu. Mọi thay đổi chiến lược phải dựng snapshot mới.
3. Revision PIT là append-only; không sửa `available_at` của revision cũ.
4. Build snapshot/config dùng staging và activation trong một transaction.
5. Nêu rõ đơn vị giá ở ranh giới nguồn. Canonical DB hiện lưu giá theo nghìn
   đồng; API nghiệp vụ trả VND.
6. Timestamp lưu UTC, giao diện hiển thị Asia/Ho_Chi_Minh.
7. Không trộn nguồn giá trong cùng series hoặc cùng snapshot.
8. Preference và holdings chỉ thay đổi sau hành động thành công, rõ ràng.

## Nơi đặt code

- HTTP schema/status code: `backend/api/`.
- Nghiệp vụ danh mục: `backend/fund/`.
- Sync/import/migration: `backend/data/`.
- SQL connection helper: `backend/database/`.
- Tín hiệu và hàm thị trường dùng khi build: `backend/strategy/`.
- Command nghiên cứu được hỗ trợ: `backend/backtest/`.
- PowerShell/CLI cho người vận hành: `scripts/`.
- Type API frontend: `frontend/src/lib/api.ts`.

Không thêm logic nghiệp vụ vào React component hoặc route handler. Không thêm
lại Node backend thứ hai trong `backend/`.

## Quy trình thay đổi chiến lược

1. Viết test cho cutoff, dữ liệu PIT và tập mã mong đợi.
2. Sửa signal/snapshot builder, không sửa planner để “khớp” kết quả.
3. Lưu config mới ở `pending`.
4. Dựng đủ snapshot staging và checksum.
5. Chạy regression cho tối thiểu 15 mã, ADV và execution price.
6. Kích hoạt nguyên tử; lỗi phải giữ active snapshot/preference cũ.
7. Cập nhật `strategy-and-data.md` và changelog nếu semantics đổi.

## Bộ kiểm tra

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests -q
npm run lint
npm run build
npm run audit
.venv\Scripts\python.exe -m pip_audit -r requirements.txt
```

Nhóm test quan trọng:

- `test_signal.py`: cửa sổ quý, bộ lọc và rank.
- `test_strategy_snapshots.py`: bất biến, cutoff và activation.
- `test_financial_safety.py`: revision/provenance/corporate action.
- `test_fund_planner.py`, `test_strategy_drift_planner.py`: NAV, drift,
  holdings, benchmark và fail closed.
- `test_sync_service.py`, `test_hardening.py`: staging, nguồn, provisional và
  khóa vận hành.

Sau build, smoke test trình duyệt phải bao phủ: tải trang, nhập NAV, tính danh
mục, sort bảng và xuất CSV; console không có exception.

## Checklist review

- Có thể tái tạo mọi con số từ snapshot metadata không?
- Ngày công bố sau cutoff có bị loại không?
- Giá tín hiệu và giá thực thi có khác vai trò, đúng phiên không?
- Có mã nào bị bỏ âm thầm do thiếu dữ liệu không?
- Cùng một hiệu suất có dùng chung ngày đầu/cuối và basis với benchmark không?
- Lỗi có giữ nguyên snapshot, preference và holdings hiện hành không?
- README/docs có còn mô tả đúng code vừa đổi không?

# PE5Y Fund Planner

Ứng dụng Windows chạy cục bộ để quy đổi NAV hiện tại thành số cổ phiếu mục
tiêu của chu kỳ chiến lược đang hoạt động. Planner chỉ đọc snapshot bất biến;
không tính lại tín hiệu lịch sử từ các bảng dữ liệu đang thay đổi.

## Bắt đầu nhanh

Yêu cầu Python 3.12 và Node.js 22.13 LTS.

```powershell
# Chỉ cần chạy lần đầu hoặc sau khi đổi dependency
powershell -ExecutionPolicy Bypass -File scripts\setup-runtime.ps1

# Khởi động backend + frontend production
start.bat
```

Mở <http://localhost:3000>. Backend ở
<http://127.0.0.1:8002/docs>. Database mặc định là
`vietnam_stocks.db` tại thư mục gốc và không được commit vào Git.

Đăng ký cập nhật dữ liệu ngầm:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register-background-sync.ps1
```

Task chạy khi đăng nhập Windows và lúc 18:30 từ thứ Hai đến thứ Sáu, có chạy
bù, retry và khóa chống chạy trùng.

## Luồng sử dụng

1. Chọn `LAST_8Q_PLUS` hoặc `TTM_20Q` và tỷ lệ 10/12/14/16%.
2. Nhập NAV hiện tại.
3. Tùy chọn nhập và lưu danh mục đang nắm giữ.
4. Bấm **Tính danh mục**, xem số lượng mục tiêu và chênh lệch MUA/BÁN/GIỮ.
5. Xuất CSV khi cần.

`POST /api/fund/portfolio-plan` là API lập danh mục duy nhất. Các endpoint
động cũ dưới `/api/strategy/portfolio`, `/api/strategy/optimize` và
`/api/strategy/history/*` trả HTTP 410.

## Trạng thái tin cậy dữ liệu

Hệ thống phân biệt ba cấp, không trộn nhãn:

- `strict_pit`: chỉ dùng revision và ngày công bố có bằng chứng chính thức.
- `trusted_local`: dùng database cục bộ mà chủ quỹ đã xác nhận chấp nhận.
  Snapshot, danh sách, rank và giá mua vẫn được khóa bất biến; chế độ này
  không tự nhận là đã đối chiếu tài liệu sở giao dịch.
- `legacy_research`: dùng snapshot bất biến dựng từ dữ liệu vendor đang có,
  chỉ phục vụ nghiên cứu.

Khi chủ quỹ quyết định dùng database hiện có, chạy một lần:

```powershell
.venv\Scripts\python.exe -m backend.backtest.activate_trusted_local
```

Lệnh tạo backup có checksum trước migration, ghi xác nhận, chạy lại backtest
10 năm và chỉ kích hoạt snapshot mới sau khi toàn bộ phép kiểm tra thành công.

Máy hiện có thể bật chế độ nghiên cứu bằng:

```dotenv
PE5Y_ALLOW_LEGACY_RESEARCH_PLANNER=1
```

Chế độ này không biến dữ liệu thành `investment_ready`. API, giao diện và CSV
vẫn gắn nhãn/cảnh báo `legacy_research`. Đặt lại thành `0` để fail closed hoàn
toàn: thiếu provenance thì planner trả HTTP 503 `SNAPSHOT_NOT_VERIFIED`.

## Quy tắc chiến lược cốt lõi

- Chu kỳ tháng 9 dùng giá đóng cửa phiên hoàn tất cuối trước 01/09 để tạo tín
  hiệu và giá mở cửa phiên đầu từ 01/09 để mô phỏng thực thi.
- Cả hai chiến lược định giá bằng tối đa 20 quý đã khả dụng tại cutoff.
  `LAST_8Q_PLUS` yêu cầu thêm EPS của 8 quý gần nhất đều dương.
- Mỗi chu kỳ cần tối thiểu 15 mã.
- NAV hiện tại chỉ co giãn danh mục đã mua tại ngày chiến lược; tỷ trọng được
  để trôi theo hiệu suất từng mã, không tái cân bằng đều tại hôm nay.
- ADV dùng 20 phiên hoàn tất trước ngày thực thi; số lượng làm tròn theo lô.
- Split/cổ tức cổ phiếu đổi số lượng; cổ tức tiền mặt đi vào cash. Sự kiện
  quyền chưa hỗ trợ hoặc dữ liệu xung đột làm phép tính dừng.

Chi tiết và công thức nằm tại
[Chiến lược và dữ liệu](docs/strategy-and-data.md).

## Cấu trúc mã nguồn

```text
backend/
  api/       HTTP routes
  fund/      snapshot, planner, holdings, corporate actions
  data/      sync, provenance, migration, nguồn dữ liệu
  strategy/  tín hiệu và các hàm ADV/sizing dùng chung
  backtest/  dựng snapshot và nghiên cứu 10 năm còn được hỗ trợ
frontend/    giao diện Next.js
scripts/     cài đặt, scheduler, import và rebuild
tests/       pytest cho dữ liệu, snapshot và planner
docs/        tài liệu kỹ thuật
```

Mục lục dành cho người tiếp quản:
[docs/README.md](docs/README.md).

## Kiểm tra trước khi bàn giao

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests -q
npm run lint
npm run build
npm run audit
.venv\Scripts\python.exe -m pip_audit -r requirements.txt
```

Không sửa trực tiếp snapshot active, revision PIT hoặc file DB. Mọi thay đổi
dữ liệu phải đi qua migration/importer, dựng snapshot staging, kiểm tra cổng
an toàn rồi mới kích hoạt nguyên tử.

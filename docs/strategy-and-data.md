# Chiến lược và dữ liệu

## Hai biến thể

`TTM_20Q` xếp hạng P/E bằng EPS bình quân của tối đa 20 quý đã biết tại ngày
tín hiệu.

`LAST_8Q_PLUS` dùng cùng cách định giá tối đa 20 quý và thêm điều kiện EPS của
8 quý gần nhất đều dương. Đây là chiến lược mặc định, với `select_pct = 10`.

Các năm đầu lịch sử dùng cửa sổ quý dài nhất có thể tạo được vũ trụ hợp lệ.
Mọi chu kỳ vẫn phải có ít nhất 15 mã; thiếu thì chu kỳ bị đánh dấu lỗi, không
được trình bày như một kết quả hợp lệ.

## Mốc thời gian chu kỳ tháng 9

- `signal_cutoff`: cuối phiên hoàn tất cuối cùng trước 01/09.
- `signal_price`: giá đóng cửa chưa điều chỉnh tại phiên đó.
- `execution_date`: phiên giao dịch đầu tiên từ 01/09 trở đi.
- `execution_price`: giá mở cửa chưa điều chỉnh tại phiên thực thi.
- ADV: 20 phiên hoàn tất, ngay trước ngày thực thi.

Ví dụ chu kỳ 2025: tín hiệu 29/08/2025 và thực thi mở cửa 03/09/2025.

Chỉ revision BCTC có `available_at <= signal_cutoff` được dùng. Restatement
công bố sau cutoff chỉ ảnh hưởng chu kỳ sau.

## Xếp hạng

Tại cutoff:

```text
annualized_eps = average(quarterly_basic_eps) × 4
P/E = signal_close_vnd / annualized_eps
market_cap = signal_close_vnd × shares_outstanding_at_signal
```

Tín hiệu dùng EPS cơ bản hợp nhất; chỉ dùng riêng lẻ khi không có báo cáo hợp
nhất và phải lưu rõ phạm vi. Fact xung đột, thiếu quý độc lập hoặc không xác
định được số cổ phiếu lưu hành làm mã bị chặn.

Sau các bộ lọc cấu hình, hệ thống xếp P/E tăng dần, chọn tỷ lệ 10/12/14/16% và
áp dụng `min_holdings = 15`. Danh sách, rank, revision ID, giá, ADV, tham số và
checksum được đóng băng trong snapshot.

## Quy đổi NAV hiện tại

Giả sử mỗi mã được mua cùng tỷ trọng ban đầu tại giá thực thi:

```text
growth_i = total_return_value_i / execution_value_i
drift_weight_i = initial_weight_i × growth_i
                 / Σ(initial_weight × growth)
desired_value_i = current_nav × drift_weight_i
target_shares_i = floor(desired_value_i / current_price_i / lot_size)
                  × lot_size
```

Giới hạn thanh khoản dùng ADV lịch sử, participation rate và số ngày tích lũy
trong snapshot/config. Phần không mua được và phần dư lô giữ thành tiền mặt.
Không dùng WATCH, SKIP hoặc EXPAND.

Danh mục hiện tại chỉ gồm `symbol` và `shares`. Planner tính
`delta_shares = target_shares - current_shares`; mã đang giữ nhưng không còn
trong mục tiêu được bán về 0. Phép tính không tự sửa holdings đã lưu.

## Hiệu suất

Authoritative performance dùng sổ nắm giữ:

- giá chưa điều chỉnh để mua/bán và định giá;
- split/cổ tức cổ phiếu nhân số lượng;
- cổ tức tiền mặt cộng cash tại ngày thanh toán, không tái đầu tư;
- quyền mua hoặc event chưa hỗ trợ làm phép tính dừng.

VNINDEX giá và benchmark total-return là hai chuỗi khác nhau. Chỉ
`benchmark_total_return_history` có tài liệu cho đúng ngày đầu/cuối mới được
gọi là so sánh authoritative.

Chuỗi giá điều chỉnh Vietcap có thể dùng để đối chiếu
`legacy_research`, nhưng không tự làm `performance_ready` chuyển thành đúng.

## Nguồn và cấp tin cậy

### strict_pit

Revision append-only có tài liệu HSX/HNX/UPCoM hoặc doanh nghiệp, URL,
timestamp/availability basis và SHA-256. Vendor chỉ hỗ trợ khám phá và đối
chiếu.

### trusted_local

Chủ quỹ chủ động xác nhận dùng dữ liệu hiện có trong `vietnam_stocks.db`.
Hệ thống tạo backup, lưu checksum và bản ghi xác nhận trước khi dựng lại
snapshot. Planner được mở bằng `user_confirmed_ready`; `investment_ready`
theo nghĩa đã xác minh tài liệu chính thức vẫn giữ nguyên là sai.

Danh sách mã, rank, giá tín hiệu, giá mở cửa thực thi, ADV, cấu hình và
checksum được khóa trong snapshot. Hiệu suất dùng chuỗi giá điều chỉnh đang
lưu và luôn mang nhãn `trusted_local`.

### legacy_research

Dữ liệu sẵn có trong `vietnam_stocks.db` được inventory, phân loại và đóng
snapshot để tránh tải lại từ đầu. Nó không được tự nâng thành evidence chính
thức. Chế độ này được phép chạy khi người vận hành chủ động bật cờ môi trường
và kết quả luôn phải mang cảnh báo.

Lệnh inventory/reconcile:

```powershell
.venv\Scripts\python.exe scripts\prepare_legacy_reuse.py `
  --db vietnam_stocks.db --start-year 2016 --end-year 2025 `
  --reconcile-stored-research
```

Kết quả nằm trong `legacy_symbol_inventory`, `legacy_cycle_inventory` và
`legacy_verification_queue`.

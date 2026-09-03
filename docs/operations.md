# Vận hành Windows

## Cài đặt

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-runtime.ps1
```

Script tạo `.venv`, cài dependency Python, chạy `npm ci` và build frontend.
Database mặc định là `vietnam_stocks.db`; đổi đường dẫn bằng `PE5Y_DB_PATH`.

## Khởi động

Chạy `start.bat`. Launcher:

- kiểm tra `.venv` và production build;
- từ chối chạy nếu cổng 3000/8002 bị tiến trình lạ chiếm;
- tái sử dụng service PE5Y đang chạy;
- ghi log/PID vào `logs/`;
- chờ health check rồi mới mở trình duyệt.

Khi cần phát triển:

```powershell
npm install
npm run dev
```

## Đồng bộ nền

```powershell
scripts\register-background-sync.ps1
scripts\unregister-background-sync.ps1
```

Scheduled Task chạy `pythonw -m backend.data.sync_runner` khi đăng nhập và lúc
18:30 các ngày trong tuần. Nó có `StartWhenAvailable`, retry, timeout 6 giờ,
không dừng khi dùng pin và `MultipleInstances IgnoreNew`. Bên trong sync còn
có file lock liên tiến trình.

Trình tự sync:

1. backup SQLite và kho provenance nếu đến hạn;
2. cập nhật lịch/phiên thị trường;
3. phát hiện mã thiếu hoặc chậm;
4. upsert giá hoàn tất, không khóa cứng daily bar tạm thời;
5. stage revision tài chính mới;
6. validate coverage/provenance;
7. dựng snapshot staging và chỉ kích hoạt nguyên tử khi đạt cổng;
8. cleanup staging lỗi và rotate log.

KBS chỉ là nguồn kiểm tra chéo. Không trộn tự động từng mã KBS vào chuỗi
Vietcap.

## Kiểm tra trạng thái

```powershell
Invoke-RestMethod http://127.0.0.1:8002/api/health
Invoke-RestMethod http://127.0.0.1:8002/api/data/sync/status
Invoke-RestMethod http://127.0.0.1:8002/api/data/health
```

Nếu planner trả `SNAPSHOT_NOT_VERIFIED`, xem `blocking_issues`; không sửa cờ
readiness trực tiếp trong SQLite.

## Dùng database cục bộ đã được chủ quỹ xác nhận

Chỉ chạy khi chủ quỹ đã quyết định chấp nhận dữ liệu hiện có:

```powershell
.venv\Scripts\python.exe -m backend.backtest.activate_trusted_local
```

Lệnh thực hiện theo thứ tự: backup SQLite và checksum, migration, ghi
attestation, dựng 10 năm cho cả `LAST_8Q_PLUS` và `TTM_20Q`, kiểm tra tối thiểu
15 mã rồi kích hoạt nguyên tử. Nếu bất kỳ bước nào lỗi, snapshot đang hoạt động
không bị thay thế. Manifest cục bộ nằm ở
`trusted_local_attestation.json`; báo cáo chạy nằm trong `output/`.

`trusted_local` cho phép lập danh mục nhưng không đổi các cờ strict PIT.
Khi dữ liệu tài chính active thay đổi, cần tạo xác nhận mới trước khi dựng
snapshot trusted-local tiếp theo.

## Dữ liệu đang có và provenance

Không cần tải lại toàn bộ DB để bắt đầu phân loại:

```powershell
.venv\Scripts\python.exe scripts\prepare_legacy_reuse.py `
  --db vietnam_stocks.db --start-year 2016 --end-year 2025 `
  --reconcile-stored-research
```

Nhập evidence đã review:

```powershell
.venv\Scripts\python.exe scripts\import_official_provenance.py `
  path\manifest.json --activate-financial-version
```

Importer kiểm tra file, SHA-256 và phân loại 100% universe bắt buộc trước
promotion. Schema manifest: [provenance-manifest.md](provenance-manifest.md).

## Backup và phục hồi

Backup ở `backups/`, kèm SHA-256 và `PRAGMA quick_check`. Kho tài liệu được lưu
theo content hash trong `provenance_documents/`.

Xác minh một backup:

```powershell
.venv\Scripts\python.exe -c "from pathlib import Path; from backend.utils.backup import verify_backup; print(verify_backup(Path('backups/TEN_FILE.db')))"
```

Không copy file DB đang mở bằng thao tác file thông thường. Dùng SQLite online
backup helper để có bản nhất quán.

## Xử lý sự cố nhanh

- Frontend trắng: xem `logs/frontend.err.log`, chạy lại `npm run build`.
- Backend không lên: xem `logs/backend.err.log`, kiểm tra `/api/health`.
- Cổng bị chiếm: tìm PID bằng `Get-NetTCPConnection -LocalPort 3000,8002`.
- Sync treo: gọi `POST /api/data/sync/cancel`; Scheduled Task có timeout 6 giờ.
- Dữ liệu cũ: kiểm tra completed session và provisional rows trong
  `/api/data/health`, không chỉ nhìn ngày lớn nhất trong bảng giá.

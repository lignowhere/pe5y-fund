# Manifest provenance chính thức

Manifest JSON chỉ được import sau khi mọi file trong `documents` tồn tại và
SHA-256 khớp. Các trường `document_sha256`/`payload_sha256` của evidence phải
tham chiếu một hash trong `documents`.

```json
{
  "batch": {
    "as_of_year": 2025,
    "as_of_quarter": 2,
    "classification_cutoff": "2025-08-29T08:00:00Z",
    "source_authority": "HSX",
    "observed_at": "2026-07-29T10:00:00Z"
  },
  "documents": [
    {
      "path": "evidence/AAA_2025_Q2.pdf",
      "sha256": "<64 ký tự hex>"
    }
  ],
  "filings": [
    {
      "symbol": "AAA",
      "year": 2025,
      "quarter": 2,
      "statement_scope": "consolidated",
      "basic_eps_vnd": 1234.5,
      "published_at": "2025-08-20T10:30:00+07:00",
      "first_observed_at": "2025-08-20T10:35:00+07:00",
      "availability_basis": "official_timestamp",
      "source_authority": "HSX",
      "source_url": "https://...",
      "document_sha256": "<hash file PDF>",
      "content_sha256": "<hash nội dung fact đã chuẩn hóa>",
      "is_independent_quarter": true
    }
  ],
  "shares_outstanding": [],
  "prices": [],
  "corporate_actions": [],
  "corporate_action_coverage": [],
  "benchmark_total_return": [
    {
      "symbol": "VNINDEX",
      "price_date": "2025-09-03",
      "index_value": 1234.567,
      "source_authority": "HSX",
      "source_url": "https://...",
      "document_sha256": "<hash tài liệu benchmark>",
      "observed_at": "2025-09-03T17:00:00+07:00",
      "verification_status": "verified"
    }
  ],
  "symbol_classifications": [
    {
      "symbol": "AAA",
      "status": "verified",
      "source_authority": "HSX",
      "source_url": "https://...",
      "document_sha256": "<hash tài liệu phân loại>",
      "observed_at": "2026-07-29T10:00:00Z",
      "reason": "Đã đối chiếu toàn bộ disclosure đến cutoff"
    }
  ]
}
```

Quy tắc availability:

- `official_timestamp`: dùng timestamp công bố và chuẩn hóa UTC.
- `official_date_next_session`: ngày công bố không có giờ; chỉ khả dụng từ
  phiên VNINDEX kế tiếp.
- `live_observed`: chỉ dùng thời điểm hệ thống thực sự quan sát revision.

`prices` dùng giá VND thô và `is_session_final=true`; importer tự chuẩn hóa về
đơn vị nghìn đồng của database. Corporate action tiền mặt bắt buộc có
`payment_date`; split/cổ tức cổ phiếu bắt buộc có `share_factor`.
`benchmark_total_return` là chuỗi chỉ số tổng lợi nhuận độc lập có tài liệu gốc;
không được thay bằng VNINDEX giá thông thường.

Chỉ kích hoạt version strict PIT sau khi manifest phân loại **mọi mã bắt buộc**:

```powershell
.venv\Scripts\python.exe scripts\import_official_provenance.py manifest.json `
  --activate-financial-version
```

Các trạng thái `source_empty`, `ingestion_missing` và `conflict` làm promotion
thất bại. Chỉ `verified`, `not_published` và `not_applicable` được phép đi qua
cổng phân loại; mã `verified` còn phải có fact EPS độc lập đã xác minh.

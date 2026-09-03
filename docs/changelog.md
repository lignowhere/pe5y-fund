# Changelog

Chỉ ghi các mốc kiến trúc còn ảnh hưởng tới cách vận hành hiện tại.

## 2026-07-30 — Dữ liệu cục bộ do chủ quỹ xác nhận

- Thêm trust tier `trusted_local`, tách biệt hoàn toàn với `strict_pit` và
  `legacy_research`.
- Backup và checksum database trước khi ghi attestation.
- Dựng lại snapshot và backtest 10 năm cho cả hai chiến lược; chỉ kích hoạt
  nguyên tử khi chu kỳ hiện hành đủ tối thiểu 15 mã.
- API/UI/CSV hiển thị rõ dữ liệu do chủ quỹ xác nhận, không nâng giả cờ
  provenance chính thức.

## 2026-07-30 — Thu gọn repository và tài liệu

- Loại backend Inventory/Prisma, SEO automation, agent template và plan/report
  thử nghiệm không thuộc sản phẩm quỹ.
- Loại component EXPAND/rebalance cũ và các bộ backtest đã nghỉ.
- Endpoint chiến lược động chỉ còn HTTP 410; Fund Planner là luồng duy nhất.
- Hợp nhất tài liệu thành kiến trúc, chiến lược/dữ liệu, vận hành, phát triển
  và manifest provenance.

## 2026-07-30 — Chế độ legacy research có kiểm soát

- Tái sử dụng `vietnam_stocks.db` thay vì tải lại toàn bộ.
- Snapshot vendor bất biến có thể được planner đọc khi người vận hành chủ động
  bật cờ môi trường.
- API/UI/CSV luôn gắn `legacy_research`; `investment_ready` không bị nâng giả.

## 2026-07-29 — Cổng dữ liệu tài chính an toàn

- Thêm revision PIT append-only, document checksum, lịch sử cổ phiếu lưu hành,
  corporate action ledger và benchmark total-return riêng.
- Tách `portfolio_ready`, `performance_ready`, `backtest_ready`.
- Signal dùng close trước 01/09; execution dùng open phiên đầu từ 01/09.
- Snapshot staging chỉ kích hoạt nguyên tử sau validation.

## 2026-07-28 — Planner theo NAV hiện tại

- Lưu preference và holdings trong SQLite.
- Tỷ trọng trôi từ danh mục ngày chiến lược; số lượng làm tròn lô và giới hạn
  ADV lịch sử.
- Hiển thị MUA/BÁN/GIỮ, hiệu suất, VNINDEX tham khảo, sort cột và CSV.
- Thêm Scheduled Task Windows, sync lock, backup và production launcher.

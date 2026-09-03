# Tài liệu PE5Y

Đọc theo thứ tự sau để nắm hệ thống mà không cần dò lịch sử thay đổi:

1. [Kiến trúc](architecture.md) — runtime, module và luồng dữ liệu.
2. [Chiến lược và dữ liệu](strategy-and-data.md) — công thức, PIT, snapshot và
   hai cấp tin cậy.
3. [Vận hành Windows](operations.md) — cài đặt, khởi động, sync, backup và xử
   lý sự cố.
4. [Phát triển và kiểm thử](development.md) — quy ước thay đổi an toàn và bộ
   kiểm tra bắt buộc.
5. [Manifest provenance](provenance-manifest.md) — schema nhập bằng chứng
   chính thức.
6. [Changelog](changelog.md) — các mốc kiến trúc còn hiệu lực.

`README.md` ở thư mục gốc là hướng dẫn ngắn cho người dùng. Tài liệu trong thư
mục này mô tả trạng thái hiện tại; không phải nhật ký của mọi thử nghiệm cũ.

Nếu code và tài liệu mâu thuẫn, dừng phát hành và kiểm tra lại bằng test cùng
snapshot metadata. Không tự chọn bên “có vẻ hợp lý” trong hệ thống tài chính.

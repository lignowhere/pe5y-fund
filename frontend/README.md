# PE5Y frontend

Next.js chỉ cung cấp giao diện cho Fund Planner cục bộ. Tài liệu cài đặt,
kiến trúc, dữ liệu và kiểm thử nằm tại [README gốc](../README.md) và
[mục lục tài liệu](../docs/README.md).

```powershell
npm ci
npm run lint
npm run build
npm run dev
```

Frontend gọi FastAPI tại `http://127.0.0.1:8002` theo mặc định. Có thể đổi
bằng `NEXT_PUBLIC_API_URL`. Không thêm lại client cho các endpoint
`/api/strategy/portfolio` hoặc `/api/strategy/optimize`; chúng đã nghỉ và trả
HTTP 410.

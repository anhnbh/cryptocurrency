# Local Crypto Portfolio (CoinGecko-style UI)

Self-hosted portfolio tracker mô phỏng kiểu CoinGecko Portfolio, chạy local bằng Docker, transaction không giới hạn.

## Có gì trong bản này

- Nhiều `portfolio` (tạo, chuyển tab).
- Thêm transaction không giới hạn (`buy`, `sell`, `transfer_in`, `transfer_out`).
- Tính holdings, average cost, realized/unrealized PnL.
- Dashboard summary: current balance, 24h portfolio change, total PnL, top performer.
- Tự động cập nhật giá hiện tại từ Binance Spot mỗi `5 phút` (mặc định) cho các symbol trong portfolio.
- Vẫn có `Set Market Price` để override thủ công nếu cần.
- Dữ liệu transaction/portfolio nằm local SQLite (`/app/data/portfolio.db` trong Docker volume).
- Có Nginx reverse proxy, route public qua port `80`.

## Chạy trên VPS bằng Docker

```bash
docker compose up --build -d
```

Mở trên internet:

- `http://<VPS_PUBLIC_IP>`

## Cấu trúc service

- `portfolio`: app FastAPI chạy nội bộ cổng `8000` (không public trực tiếp).
- `nginx`: public cổng `80`, reverse proxy vào `portfolio:8000`.
- `portfolio_data`: Docker named volume lưu DB bền vững qua các lần rebuild/recreate container.

## Auto Price Sync (Binance)

Mặc định bật sẵn trong `docker-compose.yml`:

- `PRICE_SYNC_ENABLED=true`
- `PRICE_SYNC_INTERVAL_SECONDS=300`
- `BINANCE_API_BASE=https://api.binance.com`
- `PRICE_SYNC_QUOTE_ASSET=USDT`

Ví dụ `BTC` sẽ map thành cặp `BTCUSDT`.

## API chính

- `GET /api/state?portfolio_id=...`
- `POST /api/portfolios`
- `POST /api/prices`
- `POST /api/transactions`

## Lưu ý VPS

- Mở firewall/security group cho TCP `80`.
- Nếu VPS đã có web server khác đang dùng port `80`, cần tắt nó hoặc đổi mapping port ở `docker-compose.yml`.
- Không dùng `docker compose down -v` nếu muốn giữ dữ liệu cũ (`-v` sẽ xóa volume).

## Ghi chú

- Đây là bản self-hosted để tự quản lý, tránh giới hạn transaction kiểu free plan trên các nền tảng public.
- UI/flow tương tự, không phải clone 100% toàn bộ tính năng CoinGecko.

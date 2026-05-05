# Local Crypto Portfolio (CoinGecko-style UI)

Self-hosted portfolio tracker mô phỏng kiểu CoinGecko Portfolio, chạy local bằng Docker, transaction không giới hạn.

## Có gì trong bản này

- Nhiều `portfolio` (tạo, chuyển tab).
- Thêm transaction không giới hạn (`buy`, `sell`, `transfer_in`, `transfer_out`).
- Tính holdings, average cost, realized/unrealized PnL.
- Dashboard summary: current balance, 24h portfolio change, total PnL, top performer.
- Tự động cập nhật giá hiện tại từ Binance Spot realtime qua WebSocket stream.
- Form add transaction có dropdown search asset lấy từ Binance (không nhập symbol linh tinh).
- Có nút `Update Now` để trigger sync REST thủ công (fallback).
- Có nút `Detail` cho từng coin: xem transaction history + card tổng quan theo coin.
- Có nút `Delete` coin khỏi portfolio (xóa toàn bộ transaction của coin đó trong portfolio hiện tại).
- Detail coin mở thành màn hình riêng, có nút `Back` về home.
- Form Add Transaction thu gọn, bấm nút mới xổ khung nhập.
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
- `PRICE_STREAM_ENABLED=true`
- `TRACKED_SYMBOLS_REFRESH_SECONDS=30`
- `BINANCE_API_BASE=https://api.binance.com`
- `BINANCE_WS_BASE=wss://stream.binance.com:9443`
- `BINANCE_WS_STREAM=!miniTicker@arr`
- `PRICE_SYNC_QUOTE_ASSET=USDT`

Ví dụ `BTC` sẽ map thành cặp `BTCUSDT`.

## API chính

- `GET /api/state?portfolio_id=...`
- `POST /api/portfolios`
- `GET /api/assets`
- `POST /api/prices/sync-now`
- `POST /api/transactions`
- `GET /api/coins/{symbol}/detail?portfolio_id=...`
- `DELETE /api/coins/{symbol}?portfolio_id=...`

## Lưu ý VPS

- Mở firewall/security group cho TCP `80`.
- Nếu VPS đã có web server khác đang dùng port `80`, cần tắt nó hoặc đổi mapping port ở `docker-compose.yml`.
- Không dùng `docker compose down -v` nếu muốn giữ dữ liệu cũ (`-v` sẽ xóa volume).

## Ghi chú

- Đây là bản self-hosted để tự quản lý, tránh giới hạn transaction kiểu free plan trên các nền tảng public.
- UI/flow tương tự, không phải clone 100% toàn bộ tính năng CoinGecko.

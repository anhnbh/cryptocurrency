# Local Crypto Portfolio (CoinGecko-style)

Self-hosted portfolio tracker mô phỏng luồng Portfolio của CoinGecko, chạy local bằng Docker, không giới hạn số lượng transaction.

## Có gì trong bản này

- Nhiều `portfolio` (tạo, chuyển tab).
- Thêm transaction không giới hạn (`buy`, `sell`, `transfer_in`, `transfer_out`).
- Tự tính holdings, cost basis (average cost), realized/unrealized PnL.
- Dashboard summary: current balance, 24h portfolio change, total PnL, top performer.
- Lấy giá realtime từ CoinGecko `/simple/price` theo symbol.
- Dữ liệu lưu local SQLite (`./data/portfolio.db`).

## Chạy bằng Docker

```bash
docker compose up --build
```

Mở: `http://localhost:8000`

## Biến môi trường (tuỳ chọn)

Copy file env mẫu:

```bash
cp .env.example .env
```

- `COINGECKO_DEMO_API_KEY`: optional, giúp ổn định rate limit nếu bạn có key demo.

## API chính

- `GET /api/state?portfolio_id=...`
- `POST /api/portfolios`
- `POST /api/transactions`

## Ghi chú

- Mục tiêu là self-hosted tracker local tương tự trải nghiệm CoinGecko Portfolio, không phụ thuộc giới hạn free plan transaction.
- Không phải bản clone 100% UI/feature của CoinGecko (không có toàn bộ module analytics nâng cao).

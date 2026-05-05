from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
import websockets
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, create_engine, delete, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from zoneinfo import ZoneInfo

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/portfolio.db")
BINANCE_API_BASE = os.getenv("BINANCE_API_BASE", "https://api.binance.com")
PRICE_SYNC_QUOTE_ASSET = os.getenv("PRICE_SYNC_QUOTE_ASSET", "USDT").upper().strip()
BINANCE_WS_BASE = os.getenv("BINANCE_WS_BASE", "wss://stream.binance.com:9443")
BINANCE_WS_STREAM = os.getenv("BINANCE_WS_STREAM", "!miniTicker@arr")


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


PRICE_SYNC_ENABLED = env_bool("PRICE_SYNC_ENABLED", True)
PRICE_SYNC_INTERVAL_SECONDS = max(60, env_int("PRICE_SYNC_INTERVAL_SECONDS", 300))
PRICE_STREAM_ENABLED = env_bool("PRICE_STREAM_ENABLED", True)
TRACKED_SYMBOLS_REFRESH_SECONDS = max(10, env_int("TRACKED_SYMBOLS_REFRESH_SECONDS", 30))
ASSET_CACHE_TTL_SECONDS = max(300, env_int("ASSET_CACHE_TTL_SECONDS", 21600))
DISPLAY_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("portfolio")


class Base(DeclarativeBase):
    pass


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="portfolio", cascade="all, delete-orphan")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    coin_name: Mapped[str] = mapped_column(String(120), nullable=False)
    tx_type: Mapped[str] = mapped_column(String(30), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    price_usd: Mapped[Decimal | None] = mapped_column(Numeric(28, 10), nullable=True)
    fee_usd: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False, default=Decimal("0"))
    fee_currency: Mapped[str] = mapped_column(String(30), nullable=False, default=PRICE_SYNC_QUOTE_ASSET)
    note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    tx_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    portfolio: Mapped[Portfolio] = relationship(back_populates="transactions")


class AssetPrice(Base):
    __tablename__ = "asset_prices"

    symbol: Mapped[str] = mapped_column(String(30), primary_key=True)
    coin_name: Mapped[str] = mapped_column(String(120), nullable=False)
    price_usd: Mapped[Decimal] = mapped_column(Numeric(28, 10), nullable=False)
    change_24h_pct: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

app = FastAPI(title="Local Crypto Portfolio")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
price_sync_task: asyncio.Task[Any] | None = None
price_stream_task: asyncio.Task[Any] | None = None
assets_cache: list[dict[str, str]] = []
assets_cache_expiry: datetime | None = None
assets_cache_lock = asyncio.Lock()
price_sync_lock = asyncio.Lock()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class TransactionCreate(BaseModel):
    portfolio_id: int
    symbol: str = Field(min_length=1, max_length=30)
    coin_name: str | None = Field(default=None, max_length=120)
    tx_type: str = Field(pattern=r"^(buy|sell|transfer_in|transfer_out)$")
    quantity: str
    price_usdt: str | None = None
    fee_usdt: str | None = "0"
    price_usd: str | None = None
    fee_usd: str | None = None
    fee_currency: str | None = None
    note: str | None = ""
    tx_time: str


@app.on_event("startup")
async def startup() -> None:
    global price_sync_task, price_stream_task

    Base.metadata.create_all(bind=engine)
    ensure_schema_migrations()
    with SessionLocal() as db:
        if db.scalar(select(Portfolio.id).limit(1)) is None:
            seed = Portfolio(name="Main Portfolio")
            db.add(seed)
            db.commit()

    await get_binance_assets(force_refresh=True)

    if PRICE_STREAM_ENABLED:
        price_stream_task = asyncio.create_task(price_stream_loop(), name="binance-price-stream")
    elif PRICE_SYNC_ENABLED:
        price_sync_task = asyncio.create_task(price_sync_loop(), name="binance-price-sync")


@app.on_event("shutdown")
async def shutdown() -> None:
    global price_sync_task, price_stream_task

    if price_sync_task is not None:
        price_sync_task.cancel()
        try:
            await price_sync_task
        except asyncio.CancelledError:
            pass
        price_sync_task = None

    if price_stream_task is not None:
        price_stream_task.cancel()
        try:
            await price_stream_task
        except asyncio.CancelledError:
            pass
        price_stream_task = None


def _table_has_column(db: Session, table_name: str, column_name: str) -> bool:
    rows = db.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    return any(str(r[1]).lower() == column_name.lower() for r in rows)


def ensure_schema_migrations() -> None:
    with SessionLocal() as db:
        if not _table_has_column(db, "transactions", "fee_currency"):
            db.execute(text("ALTER TABLE transactions ADD COLUMN fee_currency VARCHAR(30)"))
            db.execute(
                text("UPDATE transactions SET fee_currency = :quote WHERE fee_currency IS NULL OR fee_currency = ''"),
                {"quote": PRICE_SYNC_QUOTE_ASSET},
            )
            db.commit()


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/portfolios")
def create_portfolio(payload: PortfolioCreate, db: Session = Depends(get_db)):
    record = Portfolio(name=payload.name.strip())
    db.add(record)
    db.commit()
    db.refresh(record)
    return {"id": record.id, "name": record.name}


@app.get("/api/portfolios")
def list_portfolios(db: Session = Depends(get_db)):
    rows = db.scalars(select(Portfolio).order_by(Portfolio.id.asc())).all()
    return [{"id": row.id, "name": row.name} for row in rows]


@app.get("/api/assets")
async def list_assets():
    assets = await get_binance_assets()
    return {"quote_asset": PRICE_SYNC_QUOTE_ASSET, "assets": assets}


@app.post("/api/prices/sync-now")
async def sync_prices_now():
    updated = await run_price_sync_once()
    with SessionLocal() as db:
        last_updated = db.scalar(select(AssetPrice.updated_at).order_by(AssetPrice.updated_at.desc()).limit(1))
    return {
        "updated": updated,
        "quote_asset": PRICE_SYNC_QUOTE_ASSET,
        "price_last_updated_at": to_utc_iso(last_updated) if last_updated else None,
        "price_last_updated_at_utc7": to_display_tz_text(last_updated) if last_updated else None,
    }


def parse_decimal(text: str | None, field_name: str, allow_none: bool = False) -> Decimal | None:
    if text is None or str(text).strip() == "":
        if allow_none:
            return None
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    try:
        value = Decimal(str(text))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} must be a number") from exc
    return value


def to_utc_iso(dt: datetime) -> str:
    # SQLite may return naive datetimes; treat them as UTC for consistent frontend conversion.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


def to_display_tz_text(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.astimezone(DISPLAY_TZ).strftime("%d/%m/%Y, %H:%M:%S")


def normalize_fee_currency(tx_type: str, symbol: str, fee_currency_input: str | None) -> str:
    symbol = symbol.upper().strip()
    quote = PRICE_SYNC_QUOTE_ASSET
    raw = (fee_currency_input or "").upper().strip()

    if tx_type in {"buy", "transfer_in"}:
        fee_currency = raw or symbol
        if fee_currency != symbol:
            raise HTTPException(status_code=400, detail=f"{tx_type} fee must be in {symbol}")
        return fee_currency

    if tx_type in {"sell", "transfer_out"}:
        fee_currency = raw or quote
        if fee_currency != quote:
            raise HTTPException(status_code=400, detail=f"{tx_type} fee must be in {quote}")
        return fee_currency

    return raw or quote


@app.post("/api/transactions")
def create_transaction(payload: TransactionCreate, db: Session = Depends(get_db)):
    portfolio = db.get(Portfolio, payload.portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    symbol = payload.symbol.upper().strip()

    quantity = parse_decimal(payload.quantity, "quantity")
    if quantity is None or quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be > 0")

    input_price = payload.price_usdt if payload.price_usdt is not None else payload.price_usd
    input_fee = payload.fee_usdt if payload.fee_usdt is not None else payload.fee_usd

    price = parse_decimal(input_price, "price_usdt", allow_none=True)
    fee = parse_decimal(input_fee or "0", "fee_usdt")
    if fee is None or fee < 0:
        raise HTTPException(status_code=400, detail="fee_usdt must be >= 0")

    fee_currency = normalize_fee_currency(payload.tx_type, symbol, payload.fee_currency)

    if payload.tx_type in {"buy", "sell"} and (price is None or price <= 0):
        raise HTTPException(status_code=400, detail="buy/sell requires price_usdt > 0")

    if payload.tx_type in {"buy", "transfer_in"} and fee_currency == symbol and fee >= quantity:
        raise HTTPException(status_code=400, detail=f"{payload.tx_type} fee ({symbol}) must be smaller than quantity")

    if payload.tx_type in {"sell", "transfer_out"}:
        holdings = build_holdings_state(db, payload.portfolio_id)
        held_qty = Decimal(str(holdings.get(symbol, {}).get("quantity", 0)))
        if held_qty < quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Not enough {symbol} to {payload.tx_type}. Holding: {held_qty}",
            )

    try:
        tx_time = datetime.fromisoformat(payload.tx_time.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="tx_time must be ISO format") from exc

    tx = Transaction(
        portfolio_id=payload.portfolio_id,
        symbol=symbol,
        coin_name=resolve_coin_name(symbol, payload.coin_name),
        tx_type=payload.tx_type,
        quantity=quantity,
        price_usd=price,
        fee_usd=fee,
        fee_currency=fee_currency,
        note=(payload.note or "").strip() or None,
        tx_time=tx_time,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    return {"id": tx.id}


@app.delete("/api/coins/{symbol}")
def delete_coin(symbol: str, portfolio_id: int, db: Session = Depends(get_db)):
    portfolio = db.get(Portfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    coin_symbol = symbol.upper().strip()
    existing = db.scalar(
        select(Transaction.id).where(Transaction.portfolio_id == portfolio_id, Transaction.symbol == coin_symbol).limit(1)
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Coin not found in this portfolio")

    result = db.execute(
        delete(Transaction).where(Transaction.portfolio_id == portfolio_id, Transaction.symbol == coin_symbol)
    )

    still_used = db.scalar(select(Transaction.id).where(Transaction.symbol == coin_symbol).limit(1))
    if still_used is None and coin_symbol != PRICE_SYNC_QUOTE_ASSET:
        row = db.get(AssetPrice, coin_symbol)
        if row is not None:
            db.delete(row)

    db.commit()
    return {"symbol": coin_symbol, "deleted_transactions": int(result.rowcount or 0)}


@app.get("/api/coins/{symbol}/detail")
def coin_detail(symbol: str, portfolio_id: int, db: Session = Depends(get_db)):
    portfolio = db.get(Portfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    coin_symbol = symbol.upper().strip()
    txs = db.scalars(
        select(Transaction)
        .where(Transaction.portfolio_id == portfolio_id, Transaction.symbol == coin_symbol)
        .order_by(Transaction.tx_time.asc(), Transaction.id.asc())
    ).all()
    if not txs:
        raise HTTPException(status_code=404, detail="No transactions for this coin")

    prices = get_price_map(db)
    coin_state = build_holdings_state(db, portfolio_id, include_zero=True).get(
        coin_symbol,
        {
            "coin_name": txs[-1].coin_name,
            "quantity": Decimal("0"),
            "cost_basis": Decimal("0"),
            "realized_pnl": Decimal("0"),
            "last_buy_price": Decimal("0"),
        },
    )

    current_price = Decimal(str(prices.get(coin_symbol, {}).get("price_usdt", coin_state["last_buy_price"] or 0)))
    price_change_24h = Decimal(str(prices.get(coin_symbol, {}).get("change_24h_pct", 0) or 0))

    holdings_qty = coin_state["quantity"]
    holdings_value = holdings_qty * current_price
    total_cost = coin_state["cost_basis"]
    total_pnl = (holdings_value - total_cost) + coin_state["realized_pnl"]
    avg_net_cost = (total_cost / holdings_qty) if holdings_qty > 0 else Decimal("0")

    run_qty = Decimal("0")
    run_cost = Decimal("0")
    tx_rows: list[dict[str, Any]] = []
    for tx in txs:
        qty = Decimal(tx.quantity)
        fee = Decimal(tx.fee_usd)
        fee_currency = (tx.fee_currency or PRICE_SYNC_QUOTE_ASSET).upper()
        price = Decimal(tx.price_usd) if tx.price_usd is not None else Decimal("0")
        tx_cost: Decimal | None = None
        tx_proceeds: Decimal | None = None
        tx_pnl: Decimal | None = None
        signed_qty = qty

        if tx.tx_type in {"buy", "transfer_in"}:
            received_qty = qty - fee if fee_currency == coin_symbol else qty
            if received_qty < Decimal("0"):
                received_qty = Decimal("0")
            signed_qty = received_qty
            tx_cost = qty * price + (fee if fee_currency == PRICE_SYNC_QUOTE_ASSET else Decimal("0"))
            tx_proceeds = Decimal("0")
            # Mark-to-market PnL per lot: current value of net received coin minus original cost paid.
            tx_pnl = (current_price * received_qty) - tx_cost
            run_qty += received_qty
            run_cost += tx_cost
        elif tx.tx_type == "sell":
            signed_qty = -qty
            avg_cost = (run_cost / run_qty) if run_qty > 0 else Decimal("0")
            removed_cost = avg_cost * qty
            tx_proceeds = qty * price - (fee if fee_currency == PRICE_SYNC_QUOTE_ASSET else Decimal("0"))
            tx_pnl = tx_proceeds - removed_cost
            run_qty -= qty
            run_cost -= removed_cost
        elif tx.tx_type == "transfer_out":
            signed_qty = -qty
            avg_cost = (run_cost / run_qty) if run_qty > 0 else Decimal("0")
            removed_cost = avg_cost * qty
            tx_proceeds = Decimal("0")
            tx_pnl = -(fee if fee_currency == PRICE_SYNC_QUOTE_ASSET else Decimal("0"))
            run_qty -= qty
            run_cost -= removed_cost

        if run_qty < 0:
            run_qty = Decimal("0")
        if run_cost < 0:
            run_cost = Decimal("0")

        tx_rows.append(
            {
                "id": tx.id,
                "tx_type": tx.tx_type,
                "price_usdt": float(price) if tx.price_usd is not None else None,
                "quantity_signed": float(signed_qty),
                "quantity": float(qty),
                "fee_usdt": float(fee),
                "fee_currency": fee_currency,
                "cost_usdt": float(tx_cost) if tx_cost is not None else None,
                "proceeds_usdt": float(tx_proceeds) if tx_proceeds is not None else None,
                "pnl_usdt": float(tx_pnl) if tx_pnl is not None else None,
                "note": tx.note,
                "tx_time": to_utc_iso(tx.tx_time),
            }
        )

    tx_rows.reverse()

    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": portfolio.name,
        "symbol": coin_symbol,
        "coin_name": coin_state["coin_name"] or txs[-1].coin_name,
        "quote_asset": PRICE_SYNC_QUOTE_ASSET,
        "price_usdt": float(current_price),
        "price_change_24h": float(price_change_24h),
        "holdings_value": float(holdings_value),
        "holdings_quantity": float(holdings_qty),
        "total_cost": float(total_cost),
        "average_net_cost": float(avg_net_cost),
        "total_profit_loss": float(total_pnl),
        "transactions": tx_rows,
    }


def build_portfolio_performance_points(
    db: Session, portfolio_id: int, prices: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    txs = db.scalars(
        select(Transaction)
        .where(Transaction.portfolio_id == portfolio_id)
        .order_by(Transaction.tx_time.asc(), Transaction.id.asc())
    ).all()
    if not txs:
        return []

    qty_map: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    points: list[dict[str, Any]] = []

    def snapshot_value() -> Decimal:
        total = Decimal("0")
        for symbol, qty in qty_map.items():
            if qty <= 0:
                continue
            current_price = Decimal(str(prices.get(symbol, {}).get("price_usdt", 0) or 0))
            total += qty * current_price
        return total

    for tx in txs:
        symbol = tx.symbol.upper()
        qty = Decimal(tx.quantity)
        fee = Decimal(tx.fee_usd)
        fee_currency = (tx.fee_currency or PRICE_SYNC_QUOTE_ASSET).upper()

        if tx.tx_type in {"buy", "transfer_in"}:
            received_qty = qty - fee if fee_currency == symbol else qty
            if received_qty < 0:
                received_qty = Decimal("0")
            qty_map[symbol] += received_qty
        elif tx.tx_type in {"sell", "transfer_out"}:
            qty_map[symbol] -= qty
            if qty_map[symbol] < 0:
                qty_map[symbol] = Decimal("0")

        points.append(
            {
                "ts": to_utc_iso(tx.tx_time),
                "value_usdt": float(snapshot_value()),
            }
        )

    now = datetime.now(timezone.utc)
    points.append({"ts": to_utc_iso(now), "value_usdt": float(snapshot_value())})
    return points


def build_portfolio_snapshot(
    db: Session,
    portfolio: Portfolio,
    prices: dict[str, dict[str, Any]],
    include_transactions: bool = True,
    transaction_limit: int = 200,
) -> dict[str, Any]:
    all_asset_state = build_holdings_state(db, portfolio.id, include_zero=True)
    holdings_state = {k: v for k, v in all_asset_state.items() if v["quantity"] > 0}

    holdings: list[dict[str, Any]] = []
    current_balance = Decimal("0")
    total_cost = Decimal("0")
    total_realized = Decimal("0")
    weighted_24h_delta = Decimal("0")

    for symbol, item in holdings_state.items():
        price_item = prices.get(symbol, {})
        current_price = Decimal(str(price_item.get("price_usdt", item["last_buy_price"] or 0)))
        price_change_24h = Decimal(str(price_item.get("change_24h_pct", 0) or 0))

        qty = item["quantity"]
        cost = item["cost_basis"]
        market_value = qty * current_price
        unrealized = market_value - cost
        pnl = unrealized + item["realized_pnl"]

        current_balance += market_value
        total_cost += cost
        total_realized += item["realized_pnl"]
        weighted_24h_delta += market_value * (price_change_24h / Decimal("100"))

        holdings.append(
            {
                "symbol": symbol,
                "coin_name": item["coin_name"],
                "quantity": float(qty),
                "avg_cost": float((cost / qty) if qty > 0 else Decimal("0")),
                "current_price": float(current_price),
                "cost_basis": float(cost),
                "market_value": float(market_value),
                "unrealized_pnl": float(unrealized),
                "realized_pnl": float(item["realized_pnl"]),
                "total_pnl": float(pnl),
                "pnl_pct": float((pnl / cost * Decimal("100")) if cost > 0 else Decimal("0")),
                "price_change_24h": float(price_change_24h),
            }
        )

    for item in all_asset_state.values():
        if item["quantity"] <= 0:
            total_realized += item["realized_pnl"]

    holdings.sort(key=lambda x: x["market_value"], reverse=True)
    top = max(holdings, key=lambda x: x["total_pnl"]) if holdings else None

    total_profit_loss = (current_balance - total_cost) + total_realized
    total_profit_loss_pct = (
        (total_profit_loss / total_cost * Decimal("100")) if total_cost > 0 else Decimal("0")
    )

    transactions: list[dict[str, Any]] = []
    if include_transactions:
        tx_rows = db.scalars(
            select(Transaction)
            .where(Transaction.portfolio_id == portfolio.id)
            .order_by(Transaction.tx_time.desc(), Transaction.id.desc())
            .limit(transaction_limit)
        ).all()
        transactions = [
            {
                "id": t.id,
                "symbol": t.symbol,
                "coin_name": t.coin_name,
                "tx_type": t.tx_type,
                "quantity": float(t.quantity),
                "price_usdt": float(t.price_usd) if t.price_usd is not None else None,
                "fee_usdt": float(t.fee_usd),
                "fee_currency": t.fee_currency or PRICE_SYNC_QUOTE_ASSET,
                "note": t.note,
                "tx_time": to_utc_iso(t.tx_time),
            }
            for t in tx_rows
        ]

    performance_points = build_portfolio_performance_points(db, portfolio.id, prices)

    return {
        "portfolio_id": portfolio.id,
        "portfolio_name": portfolio.name,
        "summary": {
            "current_balance": float(current_balance),
            "total_profit_loss": float(total_profit_loss),
            "total_profit_loss_pct": float(total_profit_loss_pct),
            "portfolio_change_24h": float(weighted_24h_delta),
            "top_performer": top,
        },
        "holdings": holdings,
        "transactions": transactions,
        "performance_points": performance_points,
        "_metrics": {
            "current_balance": current_balance,
            "total_cost": total_cost,
            "total_realized": total_realized,
            "total_profit_loss": total_profit_loss,
            "weighted_24h_delta": weighted_24h_delta,
        },
    }


@app.get("/api/state")
def portfolio_state(portfolio_id: int | None = None, include_overview: bool = False, db: Session = Depends(get_db)):
    portfolios = db.scalars(select(Portfolio).order_by(Portfolio.id.asc())).all()
    if not portfolios:
        p = Portfolio(name="Main Portfolio")
        db.add(p)
        db.commit()
        db.refresh(p)
        portfolios = [p]

    active = None
    if portfolio_id is not None:
        active = next((p for p in portfolios if p.id == portfolio_id), None)
    if not active:
        active = portfolios[0]

    prices = get_price_map(db)
    active_snapshot = build_portfolio_snapshot(db, active, prices, include_transactions=True, transaction_limit=200)

    price_rows = db.scalars(select(AssetPrice).order_by(AssetPrice.symbol.asc())).all()
    last_updated = max((p.updated_at for p in price_rows), default=None)

    response: dict[str, Any] = {
        "portfolios": [{"id": p.id, "name": p.name} for p in portfolios],
        "active_portfolio_id": active.id,
        "quote_asset": PRICE_SYNC_QUOTE_ASSET,
        "price_sync_interval_seconds": PRICE_SYNC_INTERVAL_SECONDS,
        "price_sync_mode": "realtime" if PRICE_STREAM_ENABLED else "polling",
        "price_last_updated_at": to_utc_iso(last_updated) if last_updated else None,
        "price_last_updated_at_utc7": to_display_tz_text(last_updated) if last_updated else None,
        "summary": active_snapshot["summary"],
        "holdings": active_snapshot["holdings"],
        "prices": [
            {
                "symbol": p.symbol,
                "coin_name": p.coin_name,
                "price_usdt": float(p.price_usd),
                "change_24h_pct": float(p.change_24h_pct),
                "updated_at": to_utc_iso(p.updated_at),
            }
            for p in price_rows
        ],
        "transactions": active_snapshot["transactions"],
    }

    if include_overview:
        snapshots: list[dict[str, Any]] = []
        total_balance = Decimal("0")
        total_cost = Decimal("0")
        total_profit_loss = Decimal("0")
        total_change_24h = Decimal("0")
        all_holdings: list[dict[str, Any]] = []

        for p in portfolios:
            snap = active_snapshot if p.id == active.id else build_portfolio_snapshot(
                db, p, prices, include_transactions=False, transaction_limit=0
            )
            snapshots.append(
                {
                    "portfolio_id": p.id,
                    "portfolio_name": p.name,
                    "summary": snap["summary"],
                    "holdings": snap["holdings"],
                    "performance_points": snap["performance_points"],
                }
            )
            metrics = snap["_metrics"]
            total_balance += metrics["current_balance"]
            total_cost += metrics["total_cost"]
            total_profit_loss += metrics["total_profit_loss"]
            total_change_24h += metrics["weighted_24h_delta"]
            all_holdings.extend(snap["holdings"])

        top_global = max(all_holdings, key=lambda x: x["total_pnl"]) if all_holdings else None
        total_pnl_pct = (total_profit_loss / total_cost * Decimal("100")) if total_cost > 0 else Decimal("0")

        response["overview"] = {
            "summary": {
                "current_balance": float(total_balance),
                "total_profit_loss": float(total_profit_loss),
                "total_profit_loss_pct": float(total_pnl_pct),
                "portfolio_change_24h": float(total_change_24h),
                "top_performer": top_global,
            },
            "portfolios": snapshots,
        }

    return JSONResponse(response)


def build_holdings_state(db: Session, portfolio_id: int, include_zero: bool = False) -> dict[str, dict[str, Any]]:
    txs = db.scalars(
        select(Transaction)
        .where(Transaction.portfolio_id == portfolio_id)
        .order_by(Transaction.tx_time.asc(), Transaction.id.asc())
    ).all()

    state: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "coin_name": "",
            "quantity": Decimal("0"),
            "cost_basis": Decimal("0"),
            "realized_pnl": Decimal("0"),
            "last_buy_price": Decimal("0"),
        }
    )

    for tx in txs:
        key = tx.symbol.upper()
        row = state[key]
        row["coin_name"] = tx.coin_name
        qty = Decimal(tx.quantity)
        fee = Decimal(tx.fee_usd)
        fee_currency = (tx.fee_currency or PRICE_SYNC_QUOTE_ASSET).upper()
        price = Decimal(tx.price_usd) if tx.price_usd is not None else Decimal("0")

        if tx.tx_type == "buy":
            received_qty = qty - fee if fee_currency == key else qty
            if received_qty < Decimal("0"):
                received_qty = Decimal("0")
            row["quantity"] += received_qty
            row["cost_basis"] += qty * price + (fee if fee_currency == PRICE_SYNC_QUOTE_ASSET else Decimal("0"))
            row["last_buy_price"] = price
        elif tx.tx_type == "transfer_in":
            received_qty = qty - fee if fee_currency == key else qty
            if received_qty < Decimal("0"):
                received_qty = Decimal("0")
            row["quantity"] += received_qty
            row["cost_basis"] += qty * price + (fee if fee_currency == PRICE_SYNC_QUOTE_ASSET else Decimal("0"))
            if price > 0:
                row["last_buy_price"] = price
        elif tx.tx_type == "sell":
            if row["quantity"] <= 0:
                continue
            avg = row["cost_basis"] / row["quantity"] if row["quantity"] > 0 else Decimal("0")
            removed_cost = avg * qty
            proceeds = qty * price - (fee if fee_currency == PRICE_SYNC_QUOTE_ASSET else Decimal("0"))
            row["quantity"] -= qty
            row["cost_basis"] -= removed_cost
            row["realized_pnl"] += proceeds - removed_cost
        elif tx.tx_type == "transfer_out":
            if row["quantity"] <= 0:
                continue
            avg = row["cost_basis"] / row["quantity"] if row["quantity"] > 0 else Decimal("0")
            removed_cost = avg * qty
            row["quantity"] -= qty
            row["cost_basis"] -= removed_cost
            row["realized_pnl"] -= fee if fee_currency == PRICE_SYNC_QUOTE_ASSET else Decimal("0")

        if row["quantity"] < Decimal("0"):
            row["quantity"] = Decimal("0")
        if row["cost_basis"] < Decimal("0"):
            row["cost_basis"] = Decimal("0")

    if include_zero:
        return dict(state)
    return {k: v for k, v in state.items() if v["quantity"] > 0}


def get_price_map(db: Session) -> dict[str, dict[str, Any]]:
    rows = db.scalars(select(AssetPrice)).all()
    return {
        row.symbol.upper(): {
            "price_usdt": float(row.price_usd),
            "change_24h_pct": float(row.change_24h_pct),
        }
        for row in rows
    }


def resolve_coin_name(symbol: str, provided_name: str | None) -> str:
    if provided_name and provided_name.strip():
        return provided_name.strip()
    for item in assets_cache:
        if item["symbol"] == symbol:
            return item["coin_name"]
    return symbol


def get_assets_from_db() -> list[dict[str, str]]:
    with SessionLocal() as db:
        found: dict[str, str] = {}
        for row in db.scalars(select(AssetPrice).order_by(AssetPrice.symbol.asc())).all():
            sym = row.symbol.upper().strip()
            if sym:
                found[sym] = row.coin_name or sym
        for symbol, coin_name in db.execute(select(Transaction.symbol, Transaction.coin_name).distinct()).all():
            sym = (symbol or "").upper().strip()
            if sym and sym not in found:
                found[sym] = coin_name or sym
        return [{"symbol": k, "coin_name": v} for k, v in sorted(found.items())]


async def fetch_binance_assets() -> list[dict[str, str]]:
    params = {"permissions": "SPOT"}
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(f"{BINANCE_API_BASE}/api/v3/exchangeInfo", params=params)
        resp.raise_for_status()
        data = resp.json()

    symbols = data.get("symbols", []) if isinstance(data, dict) else []
    base_assets: set[str] = set()
    for row in symbols:
        if not isinstance(row, dict):
            continue
        if row.get("status") != "TRADING":
            continue
        if str(row.get("quoteAsset", "")).upper() != PRICE_SYNC_QUOTE_ASSET:
            continue
        base = str(row.get("baseAsset", "")).upper().strip()
        if base:
            base_assets.add(base)

    return [{"symbol": s, "coin_name": s} for s in sorted(base_assets)]


async def get_binance_assets(force_refresh: bool = False) -> list[dict[str, str]]:
    global assets_cache, assets_cache_expiry

    now = datetime.now(timezone.utc)
    if not force_refresh and assets_cache and assets_cache_expiry and now < assets_cache_expiry:
        return assets_cache

    async with assets_cache_lock:
        now = datetime.now(timezone.utc)
        if not force_refresh and assets_cache and assets_cache_expiry and now < assets_cache_expiry:
            return assets_cache
        try:
            fresh = await fetch_binance_assets()
            if fresh:
                assets_cache = fresh
                assets_cache_expiry = now + timedelta(seconds=ASSET_CACHE_TTL_SECONDS)
                return assets_cache
        except Exception:
            logger.exception("Failed to fetch Binance asset list")

        fallback = get_assets_from_db()
        assets_cache = fallback
        assets_cache_expiry = now + timedelta(seconds=300)
        return assets_cache


def get_symbol_name_map_for_sync(db: Session) -> dict[str, str]:
    symbol_name: dict[str, str] = {}

    for row in db.scalars(select(AssetPrice)).all():
        symbol_name[row.symbol.upper()] = row.coin_name

    for symbol, coin_name in db.execute(select(Transaction.symbol, Transaction.coin_name).distinct()).all():
        s = (symbol or "").upper().strip()
        if s and s not in symbol_name:
            symbol_name[s] = coin_name or s

    return symbol_name


def get_pair_name_map_for_sync(db: Session) -> dict[str, tuple[str, str]]:
    symbol_name = get_symbol_name_map_for_sync(db)
    pairs: dict[str, tuple[str, str]] = {}
    for base_symbol, coin_name in symbol_name.items():
        base = base_symbol.upper().strip()
        if not base or base == PRICE_SYNC_QUOTE_ASSET:
            continue
        pair = f"{base}{PRICE_SYNC_QUOTE_ASSET}"
        pairs[pair] = (base, coin_name or base)
    return pairs


def upsert_price_row(
    db: Session,
    symbol: str,
    coin_name: str,
    price: Decimal,
    change_24h_pct: Decimal,
    now: datetime,
) -> None:
    row = db.get(AssetPrice, symbol)
    if row is None:
        db.add(
            AssetPrice(
                symbol=symbol,
                coin_name=coin_name or symbol,
                price_usd=price,
                change_24h_pct=change_24h_pct,
                updated_at=now,
            )
        )
        return

    row.coin_name = row.coin_name or coin_name or symbol
    row.price_usd = price
    row.change_24h_pct = change_24h_pct
    row.updated_at = now


async def fetch_binance_24h_ticker(client: httpx.AsyncClient, pair_symbol: str) -> dict[str, Any] | None:
    try:
        response = await client.get(f"{BINANCE_API_BASE}/api/v3/ticker/24hr", params={"symbol": pair_symbol})
        if response.status_code != 200:
            return None
        data = response.json()
        if isinstance(data, dict) and "lastPrice" in data and "priceChangePercent" in data:
            return data
    except Exception:
        return None
    return None


async def price_stream_loop() -> None:
    ws_url = f"{BINANCE_WS_BASE}/ws/{BINANCE_WS_STREAM.lower()}"
    logger.info("Realtime price stream enabled: %s", ws_url)

    pair_map: dict[str, tuple[str, str]] = {}
    next_refresh = datetime.now(timezone.utc)

    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=60, close_timeout=10) as ws:
                while True:
                    now = datetime.now(timezone.utc)
                    if now >= next_refresh:
                        with SessionLocal() as db:
                            pair_map = get_pair_name_map_for_sync(db)
                        next_refresh = now + timedelta(seconds=TRACKED_SYMBOLS_REFRESH_SECONDS)

                        async with price_sync_lock:
                            with SessionLocal() as db:
                                upsert_price_row(
                                    db,
                                    PRICE_SYNC_QUOTE_ASSET,
                                    PRICE_SYNC_QUOTE_ASSET,
                                    Decimal("1"),
                                    Decimal("0"),
                                    now,
                                )
                                db.commit()

                    raw = await ws.recv()
                    payload = json.loads(raw)
                    rows = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
                    if isinstance(rows, dict):
                        rows = [rows]
                    if not isinstance(rows, list):
                        continue
                    if not pair_map:
                        continue

                    updates: dict[str, tuple[str, Decimal, Decimal]] = {}
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        pair = str(row.get("s", "")).upper().strip()
                        mapping = pair_map.get(pair)
                        if not mapping:
                            continue
                        base_symbol, coin_name = mapping
                        try:
                            last_price = Decimal(str(row.get("c", "0")))
                            open_price = Decimal(str(row.get("o", "0")))
                        except InvalidOperation:
                            continue
                        if last_price <= 0:
                            continue
                        change = ((last_price - open_price) / open_price * Decimal("100")) if open_price > 0 else Decimal("0")
                        updates[base_symbol] = (coin_name, last_price, change)

                    if not updates:
                        continue

                    write_time = datetime.now(timezone.utc)
                    async with price_sync_lock:
                        with SessionLocal() as db:
                            for base_symbol, (coin_name, price, change) in updates.items():
                                upsert_price_row(db, base_symbol, coin_name, price, change, write_time)
                            db.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Realtime Binance stream disconnected, reconnecting in 3s")
            await asyncio.sleep(3)


async def run_price_sync_once() -> int:
    async with price_sync_lock:
        with SessionLocal() as db:
            symbol_name_map = get_symbol_name_map_for_sync(db)
            if not symbol_name_map:
                return 0

            updated = 0
            now = datetime.now(timezone.utc)

            async with httpx.AsyncClient(timeout=10) as client:
                for symbol, coin_name in symbol_name_map.items():
                    if symbol == PRICE_SYNC_QUOTE_ASSET:
                        upsert_price_row(db, symbol, coin_name, Decimal("1"), Decimal("0"), now)
                        updated += 1
                        continue

                    pair = f"{symbol}{PRICE_SYNC_QUOTE_ASSET}"
                    ticker = await fetch_binance_24h_ticker(client, pair)
                    if ticker is None:
                        continue

                    try:
                        price = Decimal(str(ticker["lastPrice"]))
                        change = Decimal(str(ticker["priceChangePercent"]))
                    except (InvalidOperation, KeyError):
                        continue

                    if price <= 0:
                        continue

                    upsert_price_row(db, symbol, coin_name, price, change, now)
                    updated += 1

            if updated > 0:
                db.commit()

            return updated


async def price_sync_loop() -> None:
    logger.info(
        "Price sync enabled: base=%s, interval=%ss, quote=%s",
        BINANCE_API_BASE,
        PRICE_SYNC_INTERVAL_SECONDS,
        PRICE_SYNC_QUOTE_ASSET,
    )

    while True:
        try:
            updated = await run_price_sync_once()
            if updated > 0:
                logger.info("Binance sync updated %s symbols", updated)
        except Exception:
            logger.exception("Binance price sync failed")

        await asyncio.sleep(PRICE_SYNC_INTERVAL_SECONDS)

from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/portfolio.db")
BINANCE_API_BASE = os.getenv("BINANCE_API_BASE", "https://api.binance.com")
PRICE_SYNC_QUOTE_ASSET = os.getenv("PRICE_SYNC_QUOTE_ASSET", "USDT").upper().strip()


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
ASSET_CACHE_TTL_SECONDS = max(300, env_int("ASSET_CACHE_TTL_SECONDS", 21600))

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
assets_cache: list[dict[str, str]] = []
assets_cache_expiry: datetime | None = None
assets_cache_lock = asyncio.Lock()


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
    price_usd: str | None = None
    fee_usd: str | None = "0"
    note: str | None = ""
    tx_time: str


@app.on_event("startup")
async def startup() -> None:
    global price_sync_task

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if db.scalar(select(Portfolio.id).limit(1)) is None:
            seed = Portfolio(name="Main Portfolio")
            db.add(seed)
            db.commit()

    await get_binance_assets(force_refresh=True)

    if PRICE_SYNC_ENABLED:
        price_sync_task = asyncio.create_task(price_sync_loop(), name="binance-price-sync")


@app.on_event("shutdown")
async def shutdown() -> None:
    global price_sync_task

    if price_sync_task is not None:
        price_sync_task.cancel()
        try:
            await price_sync_task
        except asyncio.CancelledError:
            pass
        price_sync_task = None


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


@app.post("/api/transactions")
def create_transaction(payload: TransactionCreate, db: Session = Depends(get_db)):
    portfolio = db.get(Portfolio, payload.portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    quantity = parse_decimal(payload.quantity, "quantity")
    if quantity is None or quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be > 0")

    price = parse_decimal(payload.price_usd, "price_usd", allow_none=True)
    fee = parse_decimal(payload.fee_usd, "fee_usd")
    if fee is None or fee < 0:
        raise HTTPException(status_code=400, detail="fee_usd must be >= 0")

    if payload.tx_type in {"buy", "sell"} and (price is None or price <= 0):
        raise HTTPException(status_code=400, detail="buy/sell requires price_usd > 0")

    if payload.tx_type in {"sell", "transfer_out"}:
        holdings = build_holdings_state(db, payload.portfolio_id)
        held_qty = Decimal(str(holdings.get(payload.symbol.upper(), {}).get("quantity", 0)))
        if held_qty < quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Not enough {payload.symbol.upper()} to {payload.tx_type}. Holding: {held_qty}",
            )

    try:
        tx_time = datetime.fromisoformat(payload.tx_time.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="tx_time must be ISO format") from exc

    tx = Transaction(
        portfolio_id=payload.portfolio_id,
        symbol=payload.symbol.upper().strip(),
        coin_name=resolve_coin_name(payload.symbol.upper().strip(), payload.coin_name),
        tx_type=payload.tx_type,
        quantity=quantity,
        price_usd=price,
        fee_usd=fee,
        note=(payload.note or "").strip() or None,
        tx_time=tx_time,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    return {"id": tx.id}


@app.get("/api/state")
def portfolio_state(portfolio_id: int | None = None, db: Session = Depends(get_db)):
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

    all_asset_state = build_holdings_state(db, active.id, include_zero=True)
    holdings_state = {k: v for k, v in all_asset_state.items() if v["quantity"] > 0}
    prices = get_price_map(db)

    holdings = []
    current_balance = Decimal("0")
    total_cost = Decimal("0")
    total_realized = Decimal("0")
    weighted_24h_delta = Decimal("0")

    for symbol, item in holdings_state.items():
        price_item = prices.get(symbol, {})
        current_price = Decimal(str(price_item.get("price_usd", item["last_buy_price"] or 0)))
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

    transactions = db.scalars(
        select(Transaction)
        .where(Transaction.portfolio_id == active.id)
        .order_by(Transaction.tx_time.desc(), Transaction.id.desc())
        .limit(200)
    ).all()

    price_rows = db.scalars(select(AssetPrice).order_by(AssetPrice.symbol.asc())).all()

    return JSONResponse(
        {
            "portfolios": [{"id": p.id, "name": p.name} for p in portfolios],
            "active_portfolio_id": active.id,
            "summary": {
                "current_balance": float(current_balance),
                "total_profit_loss": float((current_balance - total_cost) + total_realized),
                "total_profit_loss_pct": float(
                    (((current_balance - total_cost) + total_realized) / total_cost * Decimal("100"))
                    if total_cost > 0
                    else Decimal("0")
                ),
                "portfolio_change_24h": float(weighted_24h_delta),
                "top_performer": top,
            },
            "holdings": holdings,
            "prices": [
                {
                    "symbol": p.symbol,
                    "coin_name": p.coin_name,
                    "price_usd": float(p.price_usd),
                    "change_24h_pct": float(p.change_24h_pct),
                    "updated_at": p.updated_at.isoformat(),
                }
                for p in price_rows
            ],
            "transactions": [
                {
                    "id": t.id,
                    "symbol": t.symbol,
                    "coin_name": t.coin_name,
                    "tx_type": t.tx_type,
                    "quantity": float(t.quantity),
                    "price_usd": float(t.price_usd) if t.price_usd is not None else None,
                    "fee_usd": float(t.fee_usd),
                    "note": t.note,
                    "tx_time": t.tx_time.isoformat(),
                }
                for t in transactions
            ],
        }
    )


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
        price = Decimal(tx.price_usd) if tx.price_usd is not None else Decimal("0")

        if tx.tx_type == "buy":
            row["quantity"] += qty
            row["cost_basis"] += qty * price + fee
            row["last_buy_price"] = price
        elif tx.tx_type == "transfer_in":
            row["quantity"] += qty
            row["cost_basis"] += qty * price + fee
            if price > 0:
                row["last_buy_price"] = price
        elif tx.tx_type == "sell":
            if row["quantity"] <= 0:
                continue
            avg = row["cost_basis"] / row["quantity"] if row["quantity"] > 0 else Decimal("0")
            removed_cost = avg * qty
            proceeds = qty * price - fee
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
            row["realized_pnl"] -= fee

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
            "price_usd": float(row.price_usd),
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


async def run_price_sync_once() -> int:
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

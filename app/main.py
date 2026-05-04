from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/portfolio.db")


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
    coin_name: str = Field(min_length=1, max_length=120)
    tx_type: str = Field(pattern=r"^(buy|sell|transfer_in|transfer_out)$")
    quantity: str
    price_usd: str | None = None
    fee_usd: str | None = "0"
    note: str | None = ""
    tx_time: str


class PriceUpdate(BaseModel):
    symbol: str = Field(min_length=1, max_length=30)
    coin_name: str = Field(min_length=1, max_length=120)
    price_usd: str
    change_24h_pct: str | None = "0"


app = FastAPI(title="Local Crypto Portfolio")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if db.scalar(select(Portfolio.id).limit(1)) is None:
            seed = Portfolio(name="Main Portfolio")
            db.add(seed)
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


@app.post("/api/prices")
def upsert_price(payload: PriceUpdate, db: Session = Depends(get_db)):
    symbol = payload.symbol.upper().strip()
    coin_name = payload.coin_name.strip()

    price = parse_decimal(payload.price_usd, "price_usd")
    if price is None or price <= 0:
        raise HTTPException(status_code=400, detail="price_usd must be > 0")

    change = parse_decimal(payload.change_24h_pct, "change_24h_pct")
    if change is None:
        change = Decimal("0")

    row = db.get(AssetPrice, symbol)
    now = datetime.now(timezone.utc)
    if row is None:
        row = AssetPrice(
            symbol=symbol,
            coin_name=coin_name,
            price_usd=price,
            change_24h_pct=change,
            updated_at=now,
        )
        db.add(row)
    else:
        row.coin_name = coin_name
        row.price_usd = price
        row.change_24h_pct = change
        row.updated_at = now

    db.commit()
    return {"symbol": symbol}


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
        coin_name=payload.coin_name.strip(),
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

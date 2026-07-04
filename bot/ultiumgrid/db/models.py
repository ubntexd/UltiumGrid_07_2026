"""Schéma SQLAlchemy — cycles, trades, sacs, état bot, configurations."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    JSON,
    ForeignKey,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Cycle(Base):
    __tablename__ = "cycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|closed
    center_price: Mapped[float] = mapped_column(Float)
    grid_profit: Mapped[float] = mapped_column(Float, default=0.0)
    floating_profit: Mapped[float] = mapped_column(Float, default=0.0)
    funding_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    gross_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    net_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    levels_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    config_id: Mapped[int | None] = mapped_column(ForeignKey("configurations.id"), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    trades: Mapped[list["Trade"]] = relationship(back_populates="cycle")


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("cycles.id"), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    level_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    cycle: Mapped[Cycle | None] = relationship(back_populates="trades")


class Bag(Base):
    __tablename__ = "bags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|closed
    source: Mapped[str] = mapped_column(String(32), default="cut")  # cut|manual
    cut_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BotState(Base):
    __tablename__ = "bot_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    value_json: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Configuration(Base):
    __tablename__ = "configurations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, default="BTCUSDT")
    params_json: Mapped[dict] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Résultats agrégés (remplis après usage réel)
    cycles_won: Mapped[int] = mapped_column(Integer, default=0)
    cycles_lost: Mapped[int] = mapped_column(Integer, default=0)
    net_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    avg_cycle_duration_sec: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class PnlSnapshot(Base):
    """Points de courbe PnL (granularité minute)."""

    __tablename__ = "pnl_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    grid_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    bags_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    closed_cycles_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    cumulative_pnl: Mapped[float] = mapped_column(Float, default=0.0)


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(16))  # info|warn|critical
    kind: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OrderAttempt(Base):
    """Journal des tentatives d'ordres — anti-doublon post -1007 / audit instabilité testnet."""

    __tablename__ = "order_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    order_type: Mapped[str] = mapped_column(String(16))
    purpose: Mapped[str] = mapped_column(String(32), default="normal")
    client_order_id: Mapped[str] = mapped_column(String(64), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(64), index=True)
    # success | timeout_not_found | duplicate_avoided | throttled | anomaly_1008_priority | error
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    binance_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    binance_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verify_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def make_engine(database_url: str):
    return create_engine(database_url, pool_pre_ping=True)


def make_session_factory(database_url: str):
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False), engine

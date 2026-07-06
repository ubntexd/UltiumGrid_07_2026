"""Schéma SQLAlchemy — cycles, trades, sacs, état bot, configurations."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    JSON,
    ForeignKey,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Cycle(Base):
    __tablename__ = "cycles"
    __table_args__ = (
        # Un seul cycle "open" par symbole (PostgreSQL partial unique index).
        Index(
            "uq_cycles_one_open_per_symbol",
            "symbol",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

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
    status: Mapped[str] = mapped_column(String(32), default="open")
    # open | sold_manual | sold_panic | closed (legacy)
    source: Mapped[str] = mapped_column(String(32), default="cut")  # cut|manual
    cut_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    creation_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cycle_id_origin: Mapped[int | None] = mapped_column(ForeignKey("cycles.id"), nullable=True, index=True)
    incomplete_levels_at_creation: Mapped[list | None] = mapped_column(JSON, nullable=True)
    market_price_at_creation: Mapped[float | None] = mapped_column(Float, nullable=True)
    sold_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    sold_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trailing_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trailing_delta_bips: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trailing_limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    activation_stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    hard_stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_exit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    floating_snapshots: Mapped[list["BagFloatingSnapshot"]] = relationship(back_populates="bag")


class BagFloatingSnapshot(Base):
    """Historique périodique du flottant d'un sac (module vente indépendant futur)."""

    __tablename__ = "bag_floating_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bag_id: Mapped[int] = mapped_column(ForeignKey("bags.id"), index=True)
    mark_price: Mapped[float] = mapped_column(Float)
    floating_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    bag: Mapped[Bag] = relationship(back_populates="floating_snapshots")


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


class PriceTick(Base):
    """Historique de prix réel (alimente la courbe UI — jamais interpolé)."""

    __tablename__ = "price_ticks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    price: Mapped[float] = mapped_column(Float)
    range_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    range_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class FeePaid(Base):
    """Commissions réelles Binance (myTrades) — jamais un frais théorique."""

    __tablename__ = "fees_paid"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    trade_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    commission_asset: Mapped[str] = mapped_column(String(16), default="")
    commission_usdt: Mapped[float] = mapped_column(Float, default=0.0)
    cycle_id: Mapped[int | None] = mapped_column(ForeignKey("cycles.id"), nullable=True, index=True)
    is_buyer: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(16))  # info|warn|critical
    kind: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EgaliseurState(Base):
    """État / config du Bot Égaliseur — écrit uniquement par le service egaliseur."""

    __tablename__ = "egaliseur_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    value_json: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class EgaliseurAction(Base):
    """Journal des actions du Bot Égaliseur (alerting permanent)."""

    __tablename__ = "egaliseur_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bag_id: Mapped[int | None] = mapped_column(ForeignKey("bags.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


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


def apply_schema_migrations(engine) -> None:
    """Ajoute les colonnes/tables manquantes (SQLite / PostgreSQL)."""
    Base.metadata.create_all(engine)
    is_sqlite = engine.dialect.name == "sqlite"
    bag_cols_sqlite = [
        ("creation_reason", "TEXT"),
        ("cycle_id_origin", "INTEGER"),
        ("incomplete_levels_at_creation", "TEXT"),
        ("market_price_at_creation", "REAL"),
        ("sold_price", "REAL"),
        ("sold_by", "TEXT"),
    ]
    bag_cols_pg = [
        ("creation_reason", "VARCHAR(64)"),
        ("cycle_id_origin", "INTEGER"),
        ("incomplete_levels_at_creation", "JSON"),
        ("market_price_at_creation", "DOUBLE PRECISION"),
        ("sold_price", "DOUBLE PRECISION"),
        ("sold_by", "VARCHAR(64)"),
        ("trailing_order_id", "VARCHAR(64)"),
        ("trailing_delta_bips", "INTEGER"),
        ("trailing_limit_price", "DOUBLE PRECISION"),
        ("activation_stop_price", "DOUBLE PRECISION"),
        ("hard_stop_price", "DOUBLE PRECISION"),
        ("max_exit_at", "TIMESTAMP WITH TIME ZONE"),
    ]
    bag_cols_sqlite_extra = [
        ("trailing_order_id", "TEXT"),
        ("trailing_delta_bips", "INTEGER"),
        ("trailing_limit_price", "REAL"),
        ("activation_stop_price", "REAL"),
        ("hard_stop_price", "REAL"),
        ("max_exit_at", "TEXT"),
    ]
    with engine.begin() as conn:
        if is_sqlite:
            try:
                existing = {
                    row[1] for row in conn.execute(text("PRAGMA table_info(bags)")).fetchall()
                }
            except Exception:
                existing = set()
            for col, ddl in bag_cols_sqlite:
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE bags ADD COLUMN {col} {ddl}"))
            for col, ddl in bag_cols_sqlite_extra:
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE bags ADD COLUMN {col} {ddl}"))
        else:
            for col, ddl in bag_cols_pg:
                conn.execute(text(f"ALTER TABLE bags ADD COLUMN IF NOT EXISTS {col} {ddl}"))


def make_session_factory(database_url: str):
    engine = make_engine(database_url)
    apply_schema_migrations(engine)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False), engine

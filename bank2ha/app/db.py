"""Database schema (SQLAlchemy Core). Works with SQLite (default), PostgreSQL and MariaDB/MySQL."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, MetaData, Numeric,
    String, Table, Text, create_engine, event,
)
from sqlalchemy.engine import Engine

metadata = MetaData()

bank_sessions = Table(
    "bank_sessions", metadata,
    Column("session_id", String(64), primary_key=True),
    Column("aspsp_name", String(100)),
    Column("aspsp_country", String(2)),
    Column("valid_until", DateTime(timezone=True)),
    Column("status", String(20), nullable=False, default="active"),  # active | expired | revoked
    Column("created_at", DateTime(timezone=True)),
)

auth_states = Table(
    "auth_states", metadata,
    Column("state", String(64), primary_key=True),
    Column("created_at", DateTime(timezone=True)),
    Column("valid_until", DateTime(timezone=True)),
)

accounts = Table(
    "accounts", metadata,
    # identification_hash is stable across consents; account uid changes every session
    Column("id", String(128), primary_key=True),
    Column("current_uid", String(64)),
    Column("session_id", String(64), ForeignKey("bank_sessions.session_id")),
    Column("iban", String(40)),
    Column("bban", String(40)),
    Column("name", String(200)),
    Column("display_name", String(100)),   # your own alias, e.g. "Budgetkonto"
    Column("currency", String(3)),
    Column("product", String(200)),
    Column("cash_account_type", String(10)),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("updated_at", DateTime(timezone=True)),
)

balances = Table(
    "balances", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("account_id", String(128), ForeignKey("accounts.id"), nullable=False),
    Column("balance_type", String(10)),          # CLBD, ITAV, XPCD ...
    Column("name", String(100)),
    Column("amount", Numeric(16, 2)),
    Column("currency", String(3)),
    Column("reference_date", Date),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
)
Index("ix_balances_account_fetched", balances.c.account_id, balances.c.fetched_at)

transactions = Table(
    "transactions", metadata,
    Column("id", String(64), primary_key=True),   # deterministic hash (dedup key)
    Column("account_id", String(128), ForeignKey("accounts.id"), nullable=False),
    Column("status", String(4)),                  # BOOK | PDNG
    Column("booking_date", Date),
    Column("value_date", Date),
    Column("transaction_date", Date),
    Column("amount", Numeric(16, 2), nullable=False),  # signed: negative = money out
    Column("currency", String(3)),
    Column("counterparty", String(200)),
    Column("description", Text),
    Column("entry_reference", String(128)),
    Column("transaction_id", String(128)),
    Column("bank_transaction_code", String(100)),
    Column("balance_after", Numeric(16, 2)),
    Column("category", String(50)),
    Column("raw_json", Text),
    Column("first_seen", DateTime(timezone=True)),
    Column("last_seen", DateTime(timezone=True)),
)
Index("ix_tx_account_date", transactions.c.account_id, transactions.c.booking_date)
Index("ix_tx_category", transactions.c.category)

sync_log = Table(
    "sync_log", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("ok", Boolean),
    Column("new_transactions", Integer),
    Column("message", Text),
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite:///"):
        path = Path(url.replace("sqlite:///", "", 1))
        path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not path.exists()
        engine = create_engine(url, future=True, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA secure_delete=ON")
            cur.close()

        metadata.create_all(engine)
        if new_file or path.exists():
            # Only the owner may read the bank database
            for p in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
                if p.exists():
                    os.chmod(p, 0o600)
        return engine

    engine = create_engine(url, future=True, pool_pre_ping=True)
    metadata.create_all(engine)
    return engine

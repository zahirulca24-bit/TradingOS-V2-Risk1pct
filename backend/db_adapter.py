"""TradingOS V2 Dual Database Storage Adapter (PostgreSQL / SQLite).
Strictly DRY_RUN_ONLY. Zero order execution.

Supports:
1. Neon PostgreSQL (durable cloud storage for Render deployments when DATABASE_URL is set).
2. Local SQLite fallback (WAL mode, preserving 100% local Windows / offline baseline).
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

try:
    import psycopg2
    import psycopg2.extras
    from psycopg2.extras import RealDictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    psycopg2 = None
    RealDictCursor = None
    PSYCOPG2_AVAILABLE = False

import sqlite3

ROOT_DIR = Path(__file__).resolve().parent.parent
DB_DIR = ROOT_DIR / "database"
DEFAULT_SQLITE_PATH = DB_DIR / "tradingos_journal.db"

# Advisory lock integer hash for PostgreSQL distributed scanner coordination
PIPELINE_LOCK_KEY = 782405912
POSITION_MONITOR_LOCK_KEY = 782405913

SQLITE_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS journal_cycles (
    cycle_id TEXT PRIMARY KEY,
    candle_close_utc TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    status TEXT NOT NULL,
    total_scanned INTEGER NOT NULL,
    directional_count INTEGER NOT NULL,
    neutral_excluded INTEGER NOT NULL,
    ready_count INTEGER NOT NULL,
    wait_count INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL,
    schema_version TEXT NOT NULL DEFAULT '1.0',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS journal_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL,
    candle_close_utc TEXT NOT NULL,
    symbol TEXT NOT NULL,
    trend TEXT NOT NULL,
    direction TEXT,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    pattern TEXT,
    sweep_level REAL,
    sweep_time INTEGER,
    entry_reference REAL,
    sl REAL,
    tp REAL,
    rr REAL,
    created_at TEXT NOT NULL,
    UNIQUE(cycle_id, symbol),
    FOREIGN KEY(cycle_id) REFERENCES journal_cycles(cycle_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_signals_cycle ON journal_signals(cycle_id);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON journal_signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_status ON journal_signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_direction ON journal_signals(direction);
CREATE INDEX IF NOT EXISTS idx_signals_candle_close ON journal_signals(candle_close_utc);

CREATE TABLE IF NOT EXISTS journal_dry_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    status TEXT NOT NULL,
    reasons TEXT,
    reason_messages TEXT,
    equity_basis_usd REAL,
    risk_budget_usd REAL,
    proposed_quantity TEXT,
    qty_step TEXT,
    entry_reference REAL,
    sl REAL,
    tp REAL,
    rr REAL,
    estimated_fees_slippage_usd REAL,
    total_projected_risk_usd REAL,
    occupied_slots INTEGER,
    remaining_slots INTEGER,
    order_execution TEXT NOT NULL DEFAULT 'ORDER_EXECUTION_DISABLED',
    evaluated_at TEXT NOT NULL,
    UNIQUE(cycle_id, symbol),
    FOREIGN KEY(cycle_id) REFERENCES journal_cycles(cycle_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_dry_runs_cycle ON journal_dry_runs(cycle_id);
CREATE INDEX IF NOT EXISTS idx_dry_runs_symbol ON journal_dry_runs(symbol);
CREATE INDEX IF NOT EXISTS idx_dry_runs_status ON journal_dry_runs(status);
CREATE INDEX IF NOT EXISTS idx_dry_runs_evaluated ON journal_dry_runs(evaluated_at);

CREATE TABLE IF NOT EXISTS pipeline_cycles (
    cycle_id TEXT PRIMARY KEY,
    candle_close_utc TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    status TEXT NOT NULL,
    manifest TEXT NOT NULL,
    scanner_report TEXT NOT NULL,
    trade_plans_report TEXT NOT NULL,
    opportunities_report TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS demo_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    order_type TEXT NOT NULL,
    qty TEXT NOT NULL,
    entry_price REAL,
    sl REAL NOT NULL,
    tp REAL NOT NULL,
    rr REAL NOT NULL,
    risk_budget_usd REAL NOT NULL,
    bybit_order_id TEXT,
    order_link_id TEXT UNIQUE,
    status TEXT NOT NULL,
    error_message TEXT,
    exit_notified INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_demo_executions_cycle ON demo_executions(cycle_id);
CREATE INDEX IF NOT EXISTS idx_demo_executions_symbol ON demo_executions(symbol);
CREATE INDEX IF NOT EXISTS idx_demo_executions_link ON demo_executions(order_link_id);

CREATE TABLE IF NOT EXISTS journal_signal_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_reference REAL NOT NULL,
    sl REAL NOT NULL,
    tp REAL NOT NULL,
    rr REAL NOT NULL,
    pattern TEXT,
    candle_time INTEGER NOT NULL,
    candle_close_utc TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    exit_price REAL,
    exit_time TEXT,
    exit_candle_index INTEGER,
    post_mortem TEXT,
    created_at TEXT NOT NULL,
    CONSTRAINT uq_signal_tracking UNIQUE (cycle_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_signal_tracking_status ON journal_signal_tracking(status);

CREATE TABLE IF NOT EXISTS order_intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_link_id TEXT UNIQUE NOT NULL,
    cycle_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    qty TEXT NOT NULL,
    entry_reference REAL NOT NULL,
    sl REAL NOT NULL,
    tp REAL NOT NULL,
    rr REAL NOT NULL,
    risk_budget_usd REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    error_message TEXT,
    exchange_order_id TEXT,
    avg_fill_price REAL,
    cum_exec_qty TEXT,
    cum_exec_fee REAL,
    reconciled_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_order_intents_cycle ON order_intents(cycle_id);
CREATE INDEX IF NOT EXISTS idx_order_intents_symbol ON order_intents(symbol);
CREATE INDEX IF NOT EXISTS idx_order_intents_status ON order_intents(status);

CREATE TABLE IF NOT EXISTS slot_reservations (
    slot_id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    order_link_id TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'RESERVED',
    reserved_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    released_at TEXT,
    release_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_slot_reservations_status ON slot_reservations(status);
CREATE INDEX IF NOT EXISTS idx_slot_reservations_link ON slot_reservations(order_link_id);

CREATE TABLE IF NOT EXISTS system_controls (
    key TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    triggered_by TEXT NOT NULL DEFAULT 'SYSTEM',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_control_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    old_state INTEGER,
    new_state INTEGER NOT NULL,
    reason TEXT,
    triggered_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sys_control_audit_key ON system_control_audit(key);

CREATE TABLE IF NOT EXISTS daily_risk_records (
    trading_date TEXT PRIMARY KEY,
    starting_equity_usd REAL NOT NULL DEFAULT 0.0,
    current_equity_usd REAL NOT NULL DEFAULT 0.0,
    peak_equity_usd REAL NOT NULL DEFAULT 0.0,
    realized_pnl_usd REAL NOT NULL DEFAULT 0.0,
    unrealized_pnl_usd REAL NOT NULL DEFAULT 0.0,
    max_drawdown_pct REAL NOT NULL DEFAULT 0.0,
    drawdown_limit_pct REAL NOT NULL DEFAULT 3.0,
    circuit_breaker_triggered INTEGER NOT NULL DEFAULT 0,
    circuit_breaker_triggered_at TEXT,
    trades_count INTEGER NOT NULL DEFAULT 0,
    wins_count INTEGER NOT NULL DEFAULT 0,
    losses_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS closed_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    close_id TEXT UNIQUE NOT NULL,
    order_id TEXT NOT NULL,
    order_link_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    closed_pnl REAL NOT NULL,
    fee REAL NOT NULL DEFAULT 0.0,
    is_partial INTEGER NOT NULL DEFAULT 0,
    fill_count INTEGER DEFAULT 1,
    leverage TEXT,
    exchange_created_at TEXT,
    exchange_updated_at TEXT,
    fed_to_breaker INTEGER NOT NULL DEFAULT 0,
    notified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_closed_trades_symbol ON closed_trades(symbol);
CREATE INDEX IF NOT EXISTS idx_closed_trades_order ON closed_trades(order_id);
CREATE INDEX IF NOT EXISTS idx_closed_trades_breaker ON closed_trades(fed_to_breaker);
CREATE INDEX IF NOT EXISTS idx_closed_trades_notified ON closed_trades(notified);

CREATE TABLE IF NOT EXISTS notification_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT UNIQUE NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 5,
    last_error TEXT,
    sent_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON notification_outbox(status);
CREATE INDEX IF NOT EXISTS idx_outbox_event_type ON notification_outbox(event_type);
"""

POSTGRES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS journal_cycles (
    cycle_id VARCHAR(32) PRIMARY KEY,
    candle_close_utc TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    total_scanned INTEGER NOT NULL,
    directional_count INTEGER NOT NULL,
    neutral_excluded INTEGER NOT NULL,
    ready_count INTEGER NOT NULL,
    wait_count INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL,
    schema_version VARCHAR(16) NOT NULL DEFAULT '1.0',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS journal_signals (
    id BIGSERIAL PRIMARY KEY,
    cycle_id VARCHAR(32) NOT NULL REFERENCES journal_cycles(cycle_id) ON DELETE CASCADE,
    candle_close_utc TEXT NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    trend VARCHAR(16) NOT NULL,
    direction VARCHAR(16),
    status VARCHAR(32) NOT NULL,
    reason TEXT NOT NULL,
    pattern VARCHAR(64),
    sweep_level DOUBLE PRECISION,
    sweep_time BIGINT,
    entry_reference DOUBLE PRECISION,
    sl DOUBLE PRECISION,
    tp DOUBLE PRECISION,
    rr DOUBLE PRECISION,
    created_at TEXT NOT NULL,
    CONSTRAINT uq_journal_signals_cycle_symbol UNIQUE (cycle_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_signals_cycle ON journal_signals(cycle_id);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON journal_signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_status ON journal_signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_direction ON journal_signals(direction);
CREATE INDEX IF NOT EXISTS idx_signals_candle_close ON journal_signals(candle_close_utc);

CREATE TABLE IF NOT EXISTS journal_dry_runs (
    id BIGSERIAL PRIMARY KEY,
    cycle_id VARCHAR(32) NOT NULL REFERENCES journal_cycles(cycle_id) ON DELETE CASCADE,
    symbol VARCHAR(32) NOT NULL,
    direction VARCHAR(16) NOT NULL,
    status VARCHAR(64) NOT NULL,
    reasons TEXT,
    reason_messages TEXT,
    equity_basis_usd DOUBLE PRECISION,
    risk_budget_usd DOUBLE PRECISION,
    proposed_quantity VARCHAR(32),
    qty_step VARCHAR(32),
    entry_reference DOUBLE PRECISION,
    sl DOUBLE PRECISION,
    tp DOUBLE PRECISION,
    rr DOUBLE PRECISION,
    estimated_fees_slippage_usd DOUBLE PRECISION,
    total_projected_risk_usd DOUBLE PRECISION,
    occupied_slots INTEGER,
    remaining_slots INTEGER,
    order_execution VARCHAR(64) NOT NULL DEFAULT 'ORDER_EXECUTION_DISABLED',
    evaluated_at TEXT NOT NULL,
    CONSTRAINT uq_journal_dry_runs_cycle_symbol UNIQUE (cycle_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_dry_runs_cycle ON journal_dry_runs(cycle_id);
CREATE INDEX IF NOT EXISTS idx_dry_runs_symbol ON journal_dry_runs(symbol);
CREATE INDEX IF NOT EXISTS idx_dry_runs_status ON journal_dry_runs(status);
CREATE INDEX IF NOT EXISTS idx_dry_runs_evaluated ON journal_dry_runs(evaluated_at);

CREATE TABLE IF NOT EXISTS pipeline_cycles (
    cycle_id VARCHAR(32) PRIMARY KEY,
    candle_close_utc TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    manifest TEXT NOT NULL,
    scanner_report TEXT NOT NULL,
    trade_plans_report TEXT NOT NULL,
    opportunities_report TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_meta (
    key VARCHAR(64) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS demo_executions (
    id BIGSERIAL PRIMARY KEY,
    cycle_id VARCHAR(32) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    direction VARCHAR(16) NOT NULL,
    order_type VARCHAR(16) NOT NULL,
    qty VARCHAR(32) NOT NULL,
    entry_price DOUBLE PRECISION,
    sl DOUBLE PRECISION NOT NULL,
    tp DOUBLE PRECISION NOT NULL,
    rr DOUBLE PRECISION NOT NULL,
    risk_budget_usd DOUBLE PRECISION NOT NULL,
    bybit_order_id VARCHAR(64),
    order_link_id VARCHAR(64) UNIQUE,
    status VARCHAR(32) NOT NULL,
    error_message TEXT,
    exit_notified INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_demo_executions_cycle ON demo_executions(cycle_id);
CREATE INDEX IF NOT EXISTS idx_demo_executions_symbol ON demo_executions(symbol);
CREATE INDEX IF NOT EXISTS idx_demo_executions_link ON demo_executions(order_link_id);

CREATE TABLE IF NOT EXISTS journal_signal_tracking (
    id BIGSERIAL PRIMARY KEY,
    cycle_id VARCHAR(32) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    direction VARCHAR(16) NOT NULL,
    entry_reference DOUBLE PRECISION NOT NULL,
    sl DOUBLE PRECISION NOT NULL,
    tp DOUBLE PRECISION NOT NULL,
    rr DOUBLE PRECISION NOT NULL,
    pattern VARCHAR(64),
    candle_time BIGINT NOT NULL,
    candle_close_utc TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    exit_price DOUBLE PRECISION,
    exit_time TEXT,
    exit_candle_index INTEGER,
    post_mortem TEXT,
    created_at TEXT NOT NULL,
    CONSTRAINT uq_signal_tracking UNIQUE (cycle_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_signal_tracking_status ON journal_signal_tracking(status);

CREATE TABLE IF NOT EXISTS order_intents (
    id BIGSERIAL PRIMARY KEY,
    order_link_id VARCHAR(64) UNIQUE NOT NULL,
    cycle_id VARCHAR(32) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    direction VARCHAR(16) NOT NULL,
    qty VARCHAR(32) NOT NULL,
    entry_reference DOUBLE PRECISION NOT NULL,
    sl DOUBLE PRECISION NOT NULL,
    tp DOUBLE PRECISION NOT NULL,
    rr DOUBLE PRECISION NOT NULL,
    risk_budget_usd DOUBLE PRECISION NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    error_message TEXT,
    exchange_order_id VARCHAR(64),
    avg_fill_price DOUBLE PRECISION,
    cum_exec_qty VARCHAR(32),
    cum_exec_fee DOUBLE PRECISION,
    reconciled_at TIMESTAMPTZ,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_order_intents_cycle ON order_intents(cycle_id);
CREATE INDEX IF NOT EXISTS idx_order_intents_symbol ON order_intents(symbol);
CREATE INDEX IF NOT EXISTS idx_order_intents_status ON order_intents(status);

CREATE TABLE IF NOT EXISTS slot_reservations (
    slot_id INTEGER PRIMARY KEY,
    symbol VARCHAR(32) NOT NULL,
    order_link_id VARCHAR(64) UNIQUE NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'RESERVED',
    reserved_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    released_at TEXT,
    release_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_slot_reservations_status ON slot_reservations(status);
CREATE INDEX IF NOT EXISTS idx_slot_reservations_link ON slot_reservations(order_link_id);

CREATE TABLE IF NOT EXISTS system_controls (
    key VARCHAR(64) PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    triggered_by VARCHAR(64) NOT NULL DEFAULT 'SYSTEM',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_control_audit (
    id BIGSERIAL PRIMARY KEY,
    key VARCHAR(64) NOT NULL,
    old_state INTEGER,
    new_state INTEGER NOT NULL,
    reason TEXT,
    triggered_by VARCHAR(64) NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sys_control_audit_key ON system_control_audit(key);

CREATE TABLE IF NOT EXISTS daily_risk_records (
    trading_date VARCHAR(16) PRIMARY KEY,
    starting_equity_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    current_equity_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    peak_equity_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    realized_pnl_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    unrealized_pnl_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    max_drawdown_pct DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    drawdown_limit_pct DOUBLE PRECISION NOT NULL DEFAULT 3.0,
    circuit_breaker_triggered INTEGER NOT NULL DEFAULT 0,
    circuit_breaker_triggered_at TEXT,
    trades_count INTEGER NOT NULL DEFAULT 0,
    wins_count INTEGER NOT NULL DEFAULT 0,
    losses_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS closed_trades (
    id BIGSERIAL PRIMARY KEY,
    close_id VARCHAR(128) UNIQUE NOT NULL,
    order_id VARCHAR(64) NOT NULL,
    order_link_id VARCHAR(64),
    symbol VARCHAR(32) NOT NULL,
    side VARCHAR(16) NOT NULL,
    qty VARCHAR(32) NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    exit_price DOUBLE PRECISION NOT NULL,
    closed_pnl DOUBLE PRECISION NOT NULL,
    fee DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    is_partial INTEGER NOT NULL DEFAULT 0,
    fill_count INTEGER DEFAULT 1,
    leverage VARCHAR(16),
    exchange_created_at TEXT,
    exchange_updated_at TEXT,
    fed_to_breaker INTEGER NOT NULL DEFAULT 0,
    notified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_closed_trades_symbol ON closed_trades(symbol);
CREATE INDEX IF NOT EXISTS idx_closed_trades_order ON closed_trades(order_id);
CREATE INDEX IF NOT EXISTS idx_closed_trades_breaker ON closed_trades(fed_to_breaker);
CREATE INDEX IF NOT EXISTS idx_closed_trades_notified ON closed_trades(notified);

CREATE TABLE IF NOT EXISTS notification_outbox (
    id BIGSERIAL PRIMARY KEY,
    event_key VARCHAR(128) UNIQUE NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    payload TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 5,
    last_error TEXT,
    sent_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON notification_outbox(status);
CREATE INDEX IF NOT EXISTS idx_outbox_event_type ON notification_outbox(event_type);
"""


def get_database_url() -> Optional[str]:
    """Retrieve DATABASE_URL (or DATA_BASE_URL) from environment, if present and non-empty."""
    url = os.getenv("DATABASE_URL") or os.getenv("DATA_BASE_URL")
    if url and url.strip():
        return url.strip()
    return None


def get_active_engine(db_path: Optional[Union[str, Path]] = None, dsn: Optional[str] = None) -> str:
    """Determine whether the storage adapter targets PostgreSQL or SQLite.
    
    Returns:
        'postgres' if dsn or DATABASE_URL is configured and psycopg2 is available.
        'sqlite' otherwise.
    """
    if db_path is not None:
        return "sqlite"
    target_dsn = dsn or get_database_url()
    if target_dsn and PSYCOPG2_AVAILABLE:
        return "postgres"
    return "sqlite"


def normalize_postgres_dsn(dsn: str) -> str:
    """Normalize postgres:// prefix to postgresql:// and enforce sslmode if missing."""
    clean = dsn.strip()
    if clean.startswith("postgres://"):
        clean = "postgresql://" + clean[len("postgres://"):]
    if "sslmode=" not in clean:
        separator = "&" if "?" in clean else "?"
        clean = f"{clean}{separator}sslmode=require"
    return clean


def adapt_sql_for_postgres(sql: str) -> str:
    """Translate SQLite parameter markers and conflict syntax to PostgreSQL."""
    clean_sql = sql.replace("'BLOCKED_%'", "'BLOCKED_%%'")
    clean_sql = clean_sql.replace("?", "%s")
    # Convert SQLite INSERT OR IGNORE to PostgreSQL ON CONFLICT DO NOTHING
    if "INSERT OR IGNORE INTO journal_cycles" in clean_sql:
        clean_sql = clean_sql.replace("INSERT OR IGNORE INTO journal_cycles", "INSERT INTO journal_cycles")
        if "ON CONFLICT" not in clean_sql:
            clean_sql = clean_sql.rstrip().rstrip(";") + " ON CONFLICT (cycle_id) DO NOTHING;"
    elif "INSERT OR IGNORE INTO journal_signals" in clean_sql:
        clean_sql = clean_sql.replace("INSERT OR IGNORE INTO journal_signals", "INSERT INTO journal_signals")
        if "ON CONFLICT" not in clean_sql:
            clean_sql = clean_sql.rstrip().rstrip(";") + " ON CONFLICT (cycle_id, symbol) DO NOTHING;"
    elif "INSERT OR IGNORE INTO journal_dry_runs" in clean_sql:
        clean_sql = clean_sql.replace("INSERT OR IGNORE INTO journal_dry_runs", "INSERT INTO journal_dry_runs")
        if "ON CONFLICT" not in clean_sql:
            clean_sql = clean_sql.rstrip().rstrip(";") + " ON CONFLICT (cycle_id, symbol) DO NOTHING;"
    elif "INSERT OR IGNORE INTO pipeline_cycles" in clean_sql:
        clean_sql = clean_sql.replace("INSERT OR IGNORE INTO pipeline_cycles", "INSERT INTO pipeline_cycles")
        if "ON CONFLICT" not in clean_sql:
            clean_sql = clean_sql.rstrip().rstrip(";") + " ON CONFLICT (cycle_id) DO NOTHING;"
    elif "INSERT OR IGNORE INTO journal_signal_tracking" in clean_sql:
        clean_sql = clean_sql.replace("INSERT OR IGNORE INTO journal_signal_tracking", "INSERT INTO journal_signal_tracking")
        if "ON CONFLICT" not in clean_sql:
            clean_sql = clean_sql.rstrip().rstrip(";") + " ON CONFLICT (cycle_id, symbol) DO NOTHING;"
    elif "INSERT OR IGNORE INTO order_intents" in clean_sql:
        clean_sql = clean_sql.replace("INSERT OR IGNORE INTO order_intents", "INSERT INTO order_intents")
        if "ON CONFLICT" not in clean_sql:
            clean_sql = clean_sql.rstrip().rstrip(";") + " ON CONFLICT (order_link_id) DO NOTHING;"
    elif "INSERT OR IGNORE INTO slot_reservations" in clean_sql:
        clean_sql = clean_sql.replace("INSERT OR IGNORE INTO slot_reservations", "INSERT INTO slot_reservations")
        if "ON CONFLICT" not in clean_sql:
            clean_sql = clean_sql.rstrip().rstrip(";") + " ON CONFLICT (slot_id) DO NOTHING;"
    elif "INSERT OR IGNORE INTO system_controls" in clean_sql:
        clean_sql = clean_sql.replace("INSERT OR IGNORE INTO system_controls", "INSERT INTO system_controls")
        if "ON CONFLICT" not in clean_sql:
            clean_sql = clean_sql.rstrip().rstrip(";") + " ON CONFLICT (key) DO NOTHING;"
    elif "INSERT OR IGNORE INTO daily_risk_records" in clean_sql:
        clean_sql = clean_sql.replace("INSERT OR IGNORE INTO daily_risk_records", "INSERT INTO daily_risk_records")
        if "ON CONFLICT" not in clean_sql:
            clean_sql = clean_sql.rstrip().rstrip(";") + " ON CONFLICT (trading_date) DO NOTHING;"
    return clean_sql


class RowDict(dict):
    """Dict subclass that allows column name access (row['col']) and integer index access (row[0]),
    matching the native dual behavior of sqlite3.Row while remaining a standard dict.
    """
    def __getitem__(self, item):
        if isinstance(item, int):
            try:
                return list(self.values())[item]
            except IndexError:
                raise IndexError(f"Tuple index out of range: {item}")
        return super().__getitem__(item)


class SQLiteCursorWrapper:
    """Wraps sqlite3.Cursor to provide a dict-like RealDictCursor experience."""
    def __init__(self, cursor: sqlite3.Cursor):
        self._cur = cursor

    def execute(self, sql: str, params: Optional[Union[tuple, list, dict]] = None):
        if params is None:
            return self._cur.execute(sql)
        return self._cur.execute(sql, params)

    def executemany(self, sql: str, seq_of_params):
        return self._cur.executemany(sql, seq_of_params)

    def fetchone(self) -> Optional[Dict[str, Any]]:
        row = self._cur.fetchone()
        if row is None:
            return None
        return RowDict(row)

    def fetchall(self) -> List[Dict[str, Any]]:
        rows = self._cur.fetchall()
        return [RowDict(r) for r in rows]

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    def close(self):
        try:
            self._cur.close()
        except Exception:
            pass


class SQLiteConnectionWrapper:
    """Wraps sqlite3.Connection with RealDict row factory and context management."""
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def cursor(self) -> SQLiteCursorWrapper:
        return SQLiteCursorWrapper(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def executescript(self, sql: str):
        return self._conn.executescript(sql)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()
        self.close()


class PostgresCursorWrapper:
    """Wraps psycopg2 cursor with parameter placeholder translation (? -> %s)."""
    def __init__(self, cursor):
        self._cur = cursor

    def execute(self, sql: str, params: Optional[Union[tuple, list, dict]] = None):
        clean_sql = adapt_sql_for_postgres(sql)
        if params is None:
            return self._cur.execute(clean_sql)
        return self._cur.execute(clean_sql, params)

    def executemany(self, sql: str, seq_of_params):
        clean_sql = adapt_sql_for_postgres(sql)
        return self._cur.executemany(clean_sql, seq_of_params)

    def fetchone(self) -> Optional[Dict[str, Any]]:
        row = self._cur.fetchone()
        if row is None:
            return None
        return RowDict(row)

    def fetchall(self) -> List[Dict[str, Any]]:
        rows = self._cur.fetchall()
        return [RowDict(r) for r in rows]

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    def close(self):
        try:
            self._cur.close()
        except Exception:
            pass


class PostgresConnectionWrapper:
    """Wraps psycopg2 connection providing uniform cursor interface and context management."""
    def __init__(self, conn):
        self._conn = conn

    def cursor(self) -> PostgresCursorWrapper:
        return PostgresCursorWrapper(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def executescript(self, sql: str):
        with self._conn.cursor() as cur:
            cur.execute(sql)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()
        self.close()


def get_db_connection(db_path: Optional[Union[str, Path]] = None, dsn: Optional[str] = None) -> Union[SQLiteConnectionWrapper, PostgresConnectionWrapper]:
    """Return unified database connection wrapper supporting context management.
    
    Usage:
        with get_db_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT ...")
            conn.commit()
    """
    engine = get_active_engine(db_path=db_path, dsn=dsn)
    if engine == "postgres":
        if not PSYCOPG2_AVAILABLE:
            raise RuntimeError("psycopg2 is required for PostgreSQL connections")
        target_dsn = normalize_postgres_dsn(dsn or get_database_url())
        raw_conn = psycopg2.connect(target_dsn, cursor_factory=RealDictCursor, connect_timeout=15)
        return PostgresConnectionWrapper(raw_conn)
    else:
        target_path = Path(db_path) if db_path else DEFAULT_SQLITE_PATH
        target_path.parent.mkdir(parents=True, exist_ok=True)
        raw_conn = sqlite3.connect(str(target_path), timeout=30.0)
        raw_conn.row_factory = sqlite3.Row
        return SQLiteConnectionWrapper(raw_conn)


def init_db(db_path: Optional[Union[str, Path]] = None, dsn: Optional[str] = None) -> str:
    """Initialize all journal, pipeline, execution, risk, and control tables idempotently.
    
    Returns:
        Engine name: 'postgres' or 'sqlite'.
    """
    engine = get_active_engine(db_path=db_path, dsn=dsn)
    schema_sql = POSTGRES_SCHEMA_SQL if engine == "postgres" else SQLITE_SCHEMA_SQL
    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        conn.executescript(schema_sql)
        # Safe backward-compatible column migrations for existing databases
        cur = conn.cursor()
        if engine == "sqlite":
            try:
                cur.execute("ALTER TABLE demo_executions ADD COLUMN exit_notified INTEGER DEFAULT 0")
            except Exception:
                pass
            for col_name, col_type in [
                ("exchange_order_id", "TEXT"),
                ("avg_fill_price", "REAL"),
                ("cum_exec_qty", "TEXT"),
                ("cum_exec_fee", "REAL"),
                ("reconciled_at", "TEXT"),
            ]:
                try:
                    cur.execute(f"ALTER TABLE order_intents ADD COLUMN {col_name} {col_type}")
                except Exception:
                    pass
        else:
            for col_name, col_type in [
                ("exchange_order_id", "VARCHAR(64)"),
                ("avg_fill_price", "DOUBLE PRECISION"),
                ("cum_exec_qty", "VARCHAR(32)"),
                ("cum_exec_fee", "DOUBLE PRECISION"),
                ("reconciled_at", "TIMESTAMPTZ"),
            ]:
                try:
                    cur.execute(f"ALTER TABLE order_intents ADD COLUMN IF NOT EXISTS {col_name} {col_type}")
                except Exception:
                    pass
        conn.commit()
    return engine


# ---------------------------------------------------------------------------
# DURABLE PIPELINE STATE OPERATIONS (Neon / Cloud support)
# ---------------------------------------------------------------------------

def save_pipeline_cycle(reports: Dict[str, Any], dsn: Optional[str] = None, db_path: Optional[Union[str, Path]] = None) -> str:
    """Persist a complete verified 3-report scanner cycle durably in database.
    
    Ensures cycle reports survive container sleep and redeployments.
    """
    from pipeline_contract import is_complete_cycle
    if not is_complete_cycle(list(reports.values())):
        raise ValueError("Reports are not from one complete successful cycle")

    ident = reports["scanner"]["cycle_id"]
    close_utc = reports["scanner"]["candle_close_utc"]
    now_iso = datetime.now(timezone.utc).isoformat()

    manifest = {
        "cycle_id": ident,
        "candle_close_utc": close_utc,
        "committed_at": now_iso,
        "status": "success",
    }

    engine = init_db(db_path=db_path, dsn=dsn)

    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()

        # Check existing active pointer to ensure monotonic pointer advancement
        placeholder = "%s" if engine == "postgres" else "?"
        cur.execute(f"SELECT value FROM pipeline_meta WHERE key = {placeholder}", ("current_cycle_id",))
        existing_meta = cur.fetchone()
        should_update_pointer = True
        if existing_meta and existing_meta.get("value"):
            try:
                curr_manifest = json.loads(existing_meta["value"])
                # Don't overwrite newer cycle pointer with an older one
                if curr_manifest.get("candle_close_utc") and curr_manifest["candle_close_utc"] > close_utc:
                    should_update_pointer = False
            except Exception:
                pass

        # Insert into pipeline_cycles
        if engine == "postgres":
            cur.execute("""
                INSERT INTO pipeline_cycles (
                    cycle_id, candle_close_utc, committed_at, status,
                    manifest, scanner_report, trade_plans_report, opportunities_report, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (cycle_id) DO NOTHING
            """, (
                ident, close_utc, now_iso, "success",
                json.dumps(manifest),
                json.dumps(reports["scanner"]),
                json.dumps(reports["trade_plans"]),
                json.dumps(reports["opportunities"]),
                now_iso
            ))
            # Update active cycle pointer in pipeline_meta safely
            if should_update_pointer:
                cur.execute("""
                    INSERT INTO pipeline_meta (key, value, updated_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """, ("current_cycle_id", json.dumps(manifest), now_iso))
        else:
            cur.execute("""
                INSERT OR IGNORE INTO pipeline_cycles (
                    cycle_id, candle_close_utc, committed_at, status,
                    manifest, scanner_report, trade_plans_report, opportunities_report, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ident, close_utc, now_iso, "success",
                json.dumps(manifest),
                json.dumps(reports["scanner"]),
                json.dumps(reports["trade_plans"]),
                json.dumps(reports["opportunities"]),
                now_iso
            ))
            if should_update_pointer:
                cur.execute("""
                    INSERT INTO pipeline_meta (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """, ("current_cycle_id", json.dumps(manifest), now_iso))

        conn.commit()

    return ident


def load_committed_pipeline_cycle(dsn: Optional[str] = None, db_path: Optional[Union[str, Path]] = None) -> Optional[Dict[str, Any]]:
    """Retrieve the latest committed cycle from database storage."""
    engine = get_active_engine(db_path=db_path, dsn=dsn)
    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        # Find active cycle id
        placeholder = "%s" if engine == "postgres" else "?"
        cur.execute(f"SELECT value FROM pipeline_meta WHERE key = {placeholder}", ("current_cycle_id",))
        row = cur.fetchone()
        if not row:
            # Fall back to latest cycle by candle_close_utc
            cur.execute("SELECT manifest, scanner_report, trade_plans_report, opportunities_report FROM pipeline_cycles ORDER BY candle_close_utc DESC LIMIT 1")
            cycle_row = cur.fetchone()
            if not cycle_row:
                return None
        else:
            manifest_info = json.loads(row["value"])
            cycle_id = manifest_info["cycle_id"]
            cur.execute(f"SELECT manifest, scanner_report, trade_plans_report, opportunities_report FROM pipeline_cycles WHERE cycle_id = {placeholder}", (cycle_id,))
            cycle_row = cur.fetchone()
            if not cycle_row:
                cur.execute("SELECT manifest, scanner_report, trade_plans_report, opportunities_report FROM pipeline_cycles ORDER BY candle_close_utc DESC LIMIT 1")
                cycle_row = cur.fetchone()
                if not cycle_row:
                    return None

        manifest = json.loads(cycle_row["manifest"])
        scanner = json.loads(cycle_row["scanner_report"])
        trade_plans = json.loads(cycle_row["trade_plans_report"])
        opportunities = json.loads(cycle_row["opportunities_report"])

        return {
            "manifest": manifest,
            "scanner": scanner,
            "trade_plans": trade_plans,
            "opportunities": opportunities,
        }


def save_pipeline_last_attempt(status_dict: Dict[str, Any], dsn: Optional[str] = None, db_path: Optional[Union[str, Path]] = None) -> None:
    """Record the latest scan attempt diagnostics durably."""
    engine = init_db(db_path=db_path, dsn=dsn)
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        if engine == "postgres":
            cur.execute("""
                INSERT INTO pipeline_meta (key, value, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """, ("last_attempt", json.dumps(status_dict), now_iso))
        else:
            cur.execute("""
                INSERT INTO pipeline_meta (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """, ("last_attempt", json.dumps(status_dict), now_iso))
        conn.commit()


def load_pipeline_last_attempt(dsn: Optional[str] = None, db_path: Optional[Union[str, Path]] = None) -> Optional[Dict[str, Any]]:
    """Retrieve the latest scan attempt diagnostics."""
    engine = get_active_engine(db_path=db_path, dsn=dsn)
    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        placeholder = "%s" if engine == "postgres" else "?"
        cur.execute(f"SELECT value FROM pipeline_meta WHERE key = {placeholder}", ("last_attempt",))
        row = cur.fetchone()
        if row and row.get("value"):
            try:
                return json.loads(row["value"])
            except (ValueError, TypeError):
                return None
    return None


# ---------------------------------------------------------------------------
# DISTRIBUTED LOCKING (PostgreSQL advisory lock / Neon coordination)
# ---------------------------------------------------------------------------

@contextmanager
def postgres_distributed_lock(dsn: Optional[str] = None, lock_key: int = PIPELINE_LOCK_KEY) -> Generator[bool, None, None]:
    """Non-blocking distributed advisory lock for PostgreSQL / Neon.
    
    Prevents duplicate or concurrent scanner executions across multiple cloud instances.
    """
    if not PSYCOPG2_AVAILABLE:
        raise RuntimeError("psycopg2 is required for PostgreSQL advisory locks")
    target_dsn = normalize_postgres_dsn(dsn or get_database_url())
    raw_conn = psycopg2.connect(target_dsn, connect_timeout=10)
    raw_conn.autocommit = True
    cur = raw_conn.cursor()
    try:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
        result = cur.fetchone()
        locked = result[0] if result else False
        if not locked:
            raise RuntimeError("Another scheduler/scan instance is actively executing (advisory lock held)")
        yield True
    finally:
        try:
            cur.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
        except Exception:
            pass
        cur.close()
        raw_conn.close()


def save_demo_execution(exec_data: Dict[str, Any], dsn: Optional[str] = None, db_path: Optional[Union[str, Path]] = None) -> None:
    """Record a Bybit Demo order execution attempt durably."""
    engine = init_db(db_path=db_path, dsn=dsn)
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        if engine == "postgres":
            cur.execute("""
                INSERT INTO demo_executions (
                    cycle_id, symbol, direction, order_type, qty, entry_price, sl, tp, rr,
                    risk_budget_usd, bybit_order_id, order_link_id, status, error_message, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (order_link_id) DO UPDATE SET
                    status = excluded.status,
                    bybit_order_id = excluded.bybit_order_id,
                    error_message = excluded.error_message
            """, (
                exec_data.get("cycle_id"),
                exec_data.get("symbol"),
                exec_data.get("direction"),
                exec_data.get("order_type", "Market"),
                str(exec_data.get("qty")),
                exec_data.get("entry_price"),
                exec_data.get("sl"),
                exec_data.get("tp"),
                exec_data.get("rr"),
                exec_data.get("risk_budget_usd"),
                exec_data.get("bybit_order_id"),
                exec_data.get("order_link_id"),
                exec_data.get("status"),
                exec_data.get("error_message"),
                now_iso
            ))
        else:
            cur.execute("""
                INSERT INTO demo_executions (
                    cycle_id, symbol, direction, order_type, qty, entry_price, sl, tp, rr,
                    risk_budget_usd, bybit_order_id, order_link_id, status, error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (order_link_id) DO UPDATE SET
                    status = excluded.status,
                    bybit_order_id = excluded.bybit_order_id,
                    error_message = excluded.error_message
            """, (
                exec_data.get("cycle_id"),
                exec_data.get("symbol"),
                exec_data.get("direction"),
                exec_data.get("order_type", "Market"),
                str(exec_data.get("qty")),
                exec_data.get("entry_price"),
                exec_data.get("sl"),
                exec_data.get("tp"),
                exec_data.get("rr"),
                exec_data.get("risk_budget_usd"),
                exec_data.get("bybit_order_id"),
                exec_data.get("order_link_id"),
                exec_data.get("status"),
                exec_data.get("error_message"),
                now_iso
            ))
        conn.commit()


def get_demo_executions(limit: int = 50, dsn: Optional[str] = None, db_path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """Retrieve historical demo order executions."""
    engine = get_active_engine(db_path=db_path, dsn=dsn)
    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        placeholder = "%s" if engine == "postgres" else "?"
        cur.execute(f"SELECT * FROM demo_executions ORDER BY id DESC LIMIT {placeholder}", (limit,))
        rows = cur.fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# ORDER INTENTS (Persistent Idempotency & Pre-Execution Intent Tracking)
# ---------------------------------------------------------------------------

def create_order_intent(intent_data: Dict[str, Any], dsn: Optional[str] = None, db_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Persist an order intent prior to exchange submission to enforce strict idempotency."""
    order_link_id = intent_data.get("order_link_id")
    if not order_link_id:
        raise ValueError("order_link_id is required for persistent order intent")

    init_db(db_path=db_path, dsn=dsn)
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM order_intents WHERE order_link_id = ?", (order_link_id,))
        existing = cur.fetchone()
        if existing:
            return dict(existing)

        cur.execute("""
            INSERT INTO order_intents (
                order_link_id, cycle_id, symbol, direction, qty, entry_reference,
                sl, tp, rr, risk_budget_usd, status, retry_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """, (
            order_link_id,
            intent_data.get("cycle_id", "UNKNOWN"),
            intent_data.get("symbol"),
            intent_data.get("direction"),
            str(intent_data.get("qty", "0")),
            float(intent_data.get("entry_reference") or 0.0),
            float(intent_data.get("sl") or 0.0),
            float(intent_data.get("tp") or 0.0),
            float(intent_data.get("rr") or 0.0),
            float(intent_data.get("risk_budget_usd") or 0.0),
            intent_data.get("status", "PENDING"),
            now_iso,
            now_iso,
        ))
        conn.commit()

        cur.execute("SELECT * FROM order_intents WHERE order_link_id = ?", (order_link_id,))
        created = cur.fetchone()
        return dict(created) if created else {}


def get_order_intent(order_link_id: str, dsn: Optional[str] = None, db_path: Optional[Union[str, Path]] = None) -> Optional[Dict[str, Any]]:
    """Retrieve an order intent by its unique idempotency order_link_id."""
    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM order_intents WHERE order_link_id = ?", (order_link_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def update_order_intent_status(
    order_link_id: str,
    status: str,
    error_message: Optional[str] = None,
    increment_retry: bool = False,
    exchange_order_id: Optional[str] = None,
    avg_fill_price: Optional[float] = None,
    cum_exec_qty: Optional[str] = None,
    cum_exec_fee: Optional[float] = None,
    reconciled_at: Optional[str] = None,
    dsn: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None
) -> None:
    """Update lifecycle status and execution reconciliation fields of an order intent."""
    now_iso = datetime.now(timezone.utc).isoformat()
    init_db(db_path=db_path, dsn=dsn)
    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM order_intents WHERE order_link_id = ?", (order_link_id,))
        row = cur.fetchone()
        row_dict = dict(row) if row else {}

        rec_at = reconciled_at or (now_iso if status in ("FILLED", "PARTIALLY_FILLED", "VERIFIED_REJECTED", "VERIFIED_CANCELLED", "NEVER_SUBMITTED") else row_dict.get("reconciled_at"))
        ex_id = exchange_order_id if exchange_order_id is not None else row_dict.get("exchange_order_id")
        fill_pr = avg_fill_price if avg_fill_price is not None else row_dict.get("avg_fill_price")
        exec_qty = cum_exec_qty if cum_exec_qty is not None else row_dict.get("cum_exec_qty")
        exec_fee = cum_exec_fee if cum_exec_fee is not None else row_dict.get("cum_exec_fee")

        if increment_retry:
            cur.execute("""
                UPDATE order_intents
                SET status = ?, error_message = ?, retry_count = retry_count + 1, last_attempt_at = ?,
                    exchange_order_id = ?, avg_fill_price = ?, cum_exec_qty = ?, cum_exec_fee = ?,
                    reconciled_at = ?, updated_at = ?
                WHERE order_link_id = ?
            """, (status, error_message, now_iso, ex_id, fill_pr, exec_qty, exec_fee, rec_at, now_iso, order_link_id))
        else:
            cur.execute("""
                UPDATE order_intents
                SET status = ?, error_message = ?, exchange_order_id = ?, avg_fill_price = ?,
                    cum_exec_qty = ?, cum_exec_fee = ?, reconciled_at = ?, updated_at = ?
                WHERE order_link_id = ?
            """, (status, error_message, ex_id, fill_pr, exec_qty, exec_fee, rec_at, now_iso, order_link_id))
        conn.commit()


# ---------------------------------------------------------------------------
# ATOMIC 3-SLOT CAPACITY ENFORCEMENT & VERIFIED RECONCILIATION
# ---------------------------------------------------------------------------

VERIFIED_RELEASE_STATUSES = frozenset({
    "VERIFIED_REJECTED",
    "VERIFIED_CANCELLED",
    "VERIFIED_CLOSED",
    "NEVER_SUBMITTED",
})

NON_RELEASABLE_STATUSES = frozenset({
    "PENDING",
    "SUBMITTED",
    "UNKNOWN",
    "FILLED",
    "PARTIALLY_FILLED",
    "POSSIBLY_ACCEPTED",
})


def reserve_atomic_slot(
    symbol: str,
    order_link_id: str,
    ttl_seconds: int = 60,
    max_slots: int = 3,
    dsn: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None
) -> Optional[int]:
    """Atomically reserve one of 3 risk capacity slots before exchange order placement.
    
    Never auto-releases unconfirmed orders on TTL alone. Orders whose TTL expires
    are marked HELD_PENDING_RECONCILIATION to preserve capacity until verified on exchange.
    
    Returns:
        slot_id (1, 2, or 3) if reserved successfully; None if all 3 slots occupied/held.
    """
    init_db(db_path=db_path, dsn=dsn)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    expires_iso = datetime.fromtimestamp(now.timestamp() + ttl_seconds, tz=timezone.utc).isoformat()

    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        # 1. Any expired temporary reservation transitions to HELD_PENDING_RECONCILIATION.
        # It is NEVER released on TTL alone to prevent capacity leakage / over-allocation.
        cur.execute("""
            UPDATE slot_reservations
            SET status = 'HELD_PENDING_RECONCILIATION', release_reason = 'EXPIRED_TTL_PENDING_EXCHANGE_RECONCILIATION'
            WHERE status = 'RESERVED' AND expires_at < ?
        """, (now_iso,))

        # 2. Check if this exact order_link_id already holds an active/reserved/held slot (idempotent)
        cur.execute("SELECT slot_id FROM slot_reservations WHERE order_link_id = ? AND status IN ('RESERVED', 'ACTIVE', 'HELD_PENDING_RECONCILIATION')", (order_link_id,))
        existing = cur.fetchone()
        if existing:
            conn.commit()
            return int(existing["slot_id"])

        # 3. Query all currently occupied slots (RESERVED, ACTIVE, or HELD_PENDING_RECONCILIATION)
        cur.execute("SELECT slot_id FROM slot_reservations WHERE status IN ('RESERVED', 'ACTIVE', 'HELD_PENDING_RECONCILIATION')")
        held_slots = {int(r["slot_id"]) for r in cur.fetchall()}

        if len(held_slots) >= max_slots:
            conn.commit()
            return None

        # 4. Pick lowest available slot in 1..max_slots
        available_slot = None
        for s_id in range(1, max_slots + 1):
            if s_id not in held_slots:
                available_slot = s_id
                break

        if available_slot is None:
            conn.commit()
            return None

        # 5. Insert or replace slot reservation
        cur.execute("DELETE FROM slot_reservations WHERE slot_id = ?", (available_slot,))
        cur.execute("""
            INSERT INTO slot_reservations (
                slot_id, symbol, order_link_id, status, reserved_at, expires_at
            ) VALUES (?, ?, ?, 'RESERVED', ?, ?)
        """, (available_slot, symbol, order_link_id, now_iso, expires_iso))
        conn.commit()
        return available_slot


def activate_atomic_slot(order_link_id: str, dsn: Optional[str] = None, db_path: Optional[Union[str, Path]] = None) -> bool:
    """Transition slot status from RESERVED to ACTIVE upon exchange fill confirmation."""
    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        cur.execute("UPDATE slot_reservations SET status = 'ACTIVE' WHERE order_link_id = ? AND status IN ('RESERVED', 'HELD_PENDING_RECONCILIATION')", (order_link_id,))
        conn.commit()
        return cur.rowcount > 0


def reconcile_and_release_slot(
    order_link_id: str,
    verified_status: str,
    reason: str = "",
    dsn: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None
) -> bool:
    """Release a reserved, held, or active slot ONLY after verified exchange reconciliation.
    
    Refuses to release slots for PENDING, SUBMITTED, UNKNOWN, or FILLED orders.
    """
    v_stat = (verified_status or "").strip().upper()
    if v_stat in NON_RELEASABLE_STATUSES:
        raise ValueError(
            f"Cannot release slot for order in unverified state '{verified_status}'. "
            f"Explicit verified exchange reconciliation (one of {sorted(VERIFIED_RELEASE_STATUSES)}) required."
        )
    if v_stat not in VERIFIED_RELEASE_STATUSES:
        raise ValueError(
            f"Invalid reconciliation status '{verified_status}'. Must be one of {sorted(VERIFIED_RELEASE_STATUSES)}"
        )

    init_db(db_path=db_path, dsn=dsn)
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        full_reason = f"{v_stat}: {reason}".strip() if reason else v_stat
        cur.execute("""
            UPDATE slot_reservations
            SET status = 'RELEASED', released_at = ?, release_reason = ?
            WHERE order_link_id = ? AND status IN ('RESERVED', 'ACTIVE', 'HELD_PENDING_RECONCILIATION')
        """, (now_iso, full_reason, order_link_id))
        updated_slots = cur.rowcount

        # Also sync order_intents lifecycle if intent exists
        cur.execute("""
            UPDATE order_intents
            SET status = ?, updated_at = ?
            WHERE order_link_id = ? AND status IN ('PENDING', 'SUBMITTED', 'UNKNOWN')
        """, (v_stat, now_iso, order_link_id))

        conn.commit()
        return updated_slots > 0


def release_atomic_slot(order_link_id: str, reason: str = "COMPLETED", dsn: Optional[str] = None, db_path: Optional[Union[str, Path]] = None) -> bool:
    """Release a slot mapping safe reasons to verified release statuses."""
    clean_reason = str(reason).strip().upper()
    if clean_reason in VERIFIED_RELEASE_STATUSES:
        v_stat = clean_reason
    elif "CLOSED" in clean_reason:
        v_stat = "VERIFIED_CLOSED"
    elif "REJECT" in clean_reason:
        v_stat = "VERIFIED_REJECTED"
    elif "CANCEL" in clean_reason:
        v_stat = "VERIFIED_CANCELLED"
    elif any(k in clean_reason for k in ("PRE_SUBMISSION", "TEST", "ABORT", "NEVER")):
        v_stat = "NEVER_SUBMITTED"
    elif clean_reason in NON_RELEASABLE_STATUSES:
        raise ValueError(f"Cannot release slot for order in state '{reason}'; verified exchange reconciliation required")
    else:
        v_stat = "VERIFIED_CLOSED"

    return reconcile_and_release_slot(order_link_id, verified_status=v_stat, reason=str(reason), dsn=dsn, db_path=db_path)


def get_active_slot_reservations(dsn: Optional[str] = None, db_path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """Return all currently occupied slots (RESERVED, ACTIVE, and HELD_PENDING_RECONCILIATION)."""
    init_db(db_path=db_path, dsn=dsn)
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        # Mark expired reservations as HELD_PENDING_RECONCILIATION (NEVER auto-release to RELEASED on TTL alone)
        cur.execute("""
            UPDATE slot_reservations
            SET status = 'HELD_PENDING_RECONCILIATION', release_reason = 'EXPIRED_TTL_PENDING_EXCHANGE_RECONCILIATION'
            WHERE status = 'RESERVED' AND expires_at < ?
        """, (now_iso,))
        conn.commit()

        cur.execute("SELECT * FROM slot_reservations WHERE status IN ('RESERVED', 'ACTIVE', 'HELD_PENDING_RECONCILIATION') ORDER BY slot_id ASC")
        return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# EMERGENCY EXECUTION KILL SWITCH
# ---------------------------------------------------------------------------

def get_kill_switch_status(dsn: Optional[str] = None, db_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Check whether execution is halted by emergency kill switch (Env var or DB control)."""
    env_val = os.getenv("TRADINGOS_KILL_SWITCH", "").strip().lower()
    if env_val in ("1", "true", "yes", "on", "active"):
        return {
            "enabled": True,
            "reason": "Emergency kill switch active via TRADINGOS_KILL_SWITCH environment variable",
            "triggered_by": "ENV_VAR",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    init_db(db_path=db_path, dsn=dsn)
    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM system_controls WHERE key = 'EMERGENCY_KILL_SWITCH'")
        row = cur.fetchone()
        if row:
            return {
                "enabled": bool(row["enabled"]),
                "reason": row["reason"] or "",
                "triggered_by": row["triggered_by"] or "SYSTEM",
                "updated_at": row["updated_at"],
            }
        return {
            "enabled": False,
            "reason": "Kill switch inactive (Normal operation)",
            "triggered_by": "DEFAULT",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


def set_kill_switch(enabled: bool, reason: str = "", triggered_by: str = "MANUAL", dsn: Optional[str] = None, db_path: Optional[Union[str, Path]] = None) -> None:
    """Set persistent emergency kill switch state and record audit log."""
    init_db(db_path=db_path, dsn=dsn)
    now_iso = datetime.now(timezone.utc).isoformat()
    new_state = 1 if enabled else 0

    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT enabled FROM system_controls WHERE key = 'EMERGENCY_KILL_SWITCH'")
        old_row = cur.fetchone()
        old_state = old_row["enabled"] if old_row else 0

        cur.execute("DELETE FROM system_controls WHERE key = 'EMERGENCY_KILL_SWITCH'")
        cur.execute("""
            INSERT INTO system_controls (key, enabled, reason, triggered_by, updated_at)
            VALUES ('EMERGENCY_KILL_SWITCH', ?, ?, ?, ?)
        """, (new_state, reason, triggered_by, now_iso))

        cur.execute("""
            INSERT INTO system_control_audit (key, old_state, new_state, reason, triggered_by, created_at)
            VALUES ('EMERGENCY_KILL_SWITCH', ?, ?, ?, ?, ?)
        """, (old_state, new_state, reason, triggered_by, now_iso))
        conn.commit()


# ---------------------------------------------------------------------------
# DAILY RISK TRACKING & 3% DRAWDOWN CIRCUIT BREAKER
# ---------------------------------------------------------------------------

def get_daily_risk_record(trading_date: Optional[str] = None, dsn: Optional[str] = None, db_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Retrieve daily risk and drawdown tracking record for the given date (default: today UTC)."""
    init_db(db_path=db_path, dsn=dsn)
    target_date = trading_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM daily_risk_records WHERE trading_date = ?", (target_date,))
        row = cur.fetchone()
        if row:
            return dict(row)

        cur.execute("""
            INSERT INTO daily_risk_records (
                trading_date, starting_equity_usd, current_equity_usd, peak_equity_usd,
                realized_pnl_usd, unrealized_pnl_usd, max_drawdown_pct, drawdown_limit_pct,
                circuit_breaker_triggered, trades_count, wins_count, losses_count, updated_at
            ) VALUES (?, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 3.0, 0, 0, 0, 0, ?)
        """, (target_date, now_iso))
        conn.commit()

        cur.execute("SELECT * FROM daily_risk_records WHERE trading_date = ?", (target_date,))
        created = cur.fetchone()
        return dict(created) if created else {}


def update_daily_risk_pnl(
    trading_date: Optional[str] = None,
    realized_pnl_delta: float = 0.0,
    starting_equity: Optional[float] = None,
    current_equity: Optional[float] = None,
    unrealized_pnl: Optional[float] = None,
    is_win: Optional[bool] = None,
    drawdown_limit_pct: float = 3.0,
    dsn: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """Atomically update daily PnL and trigger circuit breaker if max drawdown reaches or exceeds threshold (3.0%)."""
    record = get_daily_risk_record(trading_date=trading_date, dsn=dsn, db_path=db_path)
    t_date = record["trading_date"]
    now_iso = datetime.now(timezone.utc).isoformat()

    start_eq = float(starting_equity if starting_equity is not None and starting_equity > 0 else (record.get("starting_equity_usd") or 0.0))
    curr_eq = float(current_equity if current_equity is not None and current_equity > 0 else (record.get("current_equity_usd") or start_eq))
    peak_eq = max(float(record.get("peak_equity_usd") or 0.0), start_eq, curr_eq)

    realized = float(record.get("realized_pnl_usd") or 0.0) + float(realized_pnl_delta)
    unrealized = float(unrealized_pnl if unrealized_pnl is not None else (record.get("unrealized_pnl_usd") or 0.0))

    trades = int(record.get("trades_count") or 0)
    wins = int(record.get("wins_count") or 0)
    losses = int(record.get("losses_count") or 0)

    if realized_pnl_delta != 0.0 or is_win is not None:
        trades += 1
        if is_win is True or realized_pnl_delta > 0:
            wins += 1
        elif is_win is False or realized_pnl_delta < 0:
            losses += 1

    drawdown_pct = 0.0
    reference_equity = peak_eq if peak_eq > 0 else start_eq
    net_pnl = realized + unrealized
    if reference_equity > 0 and net_pnl < 0:
        drawdown_pct = round((abs(net_pnl) / reference_equity) * 100, 2)

    prev_max_dd = float(record.get("max_drawdown_pct") or 0.0)
    max_dd = max(prev_max_dd, drawdown_pct)

    circuit_triggered = int(record.get("circuit_breaker_triggered") or 0)
    triggered_at = record.get("circuit_breaker_triggered_at")

    if max_dd >= drawdown_limit_pct and circuit_triggered == 0:
        circuit_triggered = 1
        triggered_at = now_iso

    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE daily_risk_records
            SET starting_equity_usd = ?, current_equity_usd = ?, peak_equity_usd = ?,
                realized_pnl_usd = ?, unrealized_pnl_usd = ?, max_drawdown_pct = ?,
                drawdown_limit_pct = ?, circuit_breaker_triggered = ?, circuit_breaker_triggered_at = ?,
                trades_count = ?, wins_count = ?, losses_count = ?, updated_at = ?
            WHERE trading_date = ?
        """, (
            start_eq, curr_eq, peak_eq, realized, unrealized, max_dd,
            drawdown_limit_pct, circuit_triggered, triggered_at,
            trades, wins, losses, now_iso, t_date
        ))
        conn.commit()

    return get_daily_risk_record(trading_date=t_date, dsn=dsn, db_path=db_path)


def is_daily_circuit_breaker_triggered(trading_date: Optional[str] = None, dsn: Optional[str] = None, db_path: Optional[Union[str, Path]] = None) -> bool:
    """Return True if an actual daily 3% drawdown circuit breaker has been tripped (distinct from UNKNOWN state)."""
    rec = get_daily_risk_record(trading_date=trading_date, dsn=dsn, db_path=db_path)
    return bool(rec.get("circuit_breaker_triggered"))


def evaluate_daily_risk_guard(
    trading_date: Optional[str] = None,
    max_staleness_seconds: int = 900,
    verify_pnl_reconciliation: bool = False,
    dsn: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """Evaluates daily risk and circuit-breaker guard; strictly fails closed if equity is missing, zero, invalid, or stale.
    
    Distinguishes an actual 3% circuit-breaker trip from UNKNOWN or STALE risk state.
    
    Returns:
        Dict with keys:
            - can_trade: bool (True ONLY if equity is verified > 0, fresh, valid, and drawdown < limit)
            - risk_state: str ('CLEARED', 'BLOCKED_CIRCUIT_BREAKER', 'BLOCKED_EQUITY_UNKNOWN', 'BLOCKED_RISK_STALE', 'BLOCKED_RISK_DATA_INVALID', 'BLOCKED_PNL_UNRECONCILED')
            - circuit_breaker_triggered: bool (True ONLY for an actual 3% circuit-breaker trip)
            - reason: str
            - starting_equity_usd: float
            - current_equity_usd: float
            - max_drawdown_pct: float
            - drawdown_limit_pct: float
            - trading_date: str
            - updated_at: str
    """
    record = get_daily_risk_record(trading_date=trading_date, dsn=dsn, db_path=db_path)
    t_date = record["trading_date"]
    now_iso = datetime.now(timezone.utc).isoformat()

    start_eq = float(record.get("starting_equity_usd") or 0.0)
    curr_eq = float(record.get("current_equity_usd") or 0.0)
    max_dd = float(record.get("max_drawdown_pct") or 0.0)
    dd_limit = float(record.get("drawdown_limit_pct") or 3.0)
    circuit_triggered = bool(record.get("circuit_breaker_triggered"))
    updated_at_str = record.get("updated_at")

    base_response = {
        "trading_date": t_date,
        "starting_equity_usd": start_eq,
        "current_equity_usd": curr_eq,
        "max_drawdown_pct": max_dd,
        "drawdown_limit_pct": dd_limit,
        "updated_at": updated_at_str or now_iso,
    }

    # 1. Check for actual circuit breaker trip (Drawdown >= 3.0%)
    if circuit_triggered or max_dd >= dd_limit:
        return {
            **base_response,
            "can_trade": False,
            "risk_state": "BLOCKED_CIRCUIT_BREAKER",
            "circuit_breaker_triggered": True,
            "reason": f"Daily 3% drawdown circuit breaker active ({max_dd:.2f}% >= {dd_limit:.2f}% limit)",
        }

    # 2. Check for missing, zero, or negative equity (FAIL CLOSED)
    if start_eq <= 0.0 or curr_eq <= 0.0:
        return {
            **base_response,
            "can_trade": False,
            "risk_state": "BLOCKED_EQUITY_UNKNOWN",
            "circuit_breaker_triggered": False,
            "reason": f"Account equity is zero, missing, or unverified (start: ${start_eq:.2f}, current: ${curr_eq:.2f}); failing closed to protect risk budget",
        }

    # 3. Check for invalid numbers (NaN or Inf)
    import math
    if (
        math.isnan(start_eq) or math.isinf(start_eq) or
        math.isnan(curr_eq) or math.isinf(curr_eq) or
        math.isnan(max_dd) or math.isinf(max_dd)
    ):
        return {
            **base_response,
            "can_trade": False,
            "risk_state": "BLOCKED_RISK_DATA_INVALID",
            "circuit_breaker_triggered": False,
            "reason": "Corrupted or non-numeric risk/equity figures detected; failing closed",
        }

    # 4. Check for stale data (Last updated > max_staleness_seconds)
    if updated_at_str:
        try:
            rec_time = datetime.fromisoformat(updated_at_str)
            elapsed = (datetime.now(timezone.utc) - rec_time).total_seconds()
            if elapsed > max_staleness_seconds:
                return {
                    **base_response,
                    "can_trade": False,
                    "risk_state": "BLOCKED_RISK_STALE",
                    "circuit_breaker_triggered": False,
                    "reason": f"Daily risk tracking data is stale ({elapsed:.0f}s old > {max_staleness_seconds}s limit); failing closed",
                }
        except (ValueError, TypeError):
            return {
                **base_response,
                "can_trade": False,
                "risk_state": "BLOCKED_RISK_STALE",
                "circuit_breaker_triggered": False,
                "reason": "Unparseable daily risk record timestamp; failing closed",
            }

    # 5. Check PnL reconciliation between breaker and closed trades (if requested)
    if verify_pnl_reconciliation:
        recon = reconcile_daily_risk_vs_closed_trades(trading_date=t_date, dsn=dsn, db_path=db_path)
        if not recon["is_reconciled"]:
            return {
                **base_response,
                "can_trade": False,
                "risk_state": "BLOCKED_PNL_UNRECONCILED",
                "circuit_breaker_triggered": False,
                "reason": f"Breaker realized PnL (${recon['breaker_realized_usd']:.2f}) does not match audited closed trades PnL (${recon['journal_realized_usd']:.2f}); discrepancy: ${recon['discrepancy_usd']:.2f}",
                "reconciliation_audit": recon,
            }

    # 6. Safe and verified within safe limits
    return {
        **base_response,
        "can_trade": True,
        "risk_state": "CLEARED",
        "circuit_breaker_triggered": False,
        "reason": "Daily risk verified within safe parameters (Normal operation)",
    }


def reconcile_daily_risk_vs_closed_trades(
    trading_date: Optional[str] = None,
    dsn: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Reconcile daily circuit breaker realized PnL against audited durable closed trades."""
    init_db(db_path=db_path, dsn=dsn)
    rec = get_daily_risk_record(trading_date=trading_date, dsn=dsn, db_path=db_path)
    breaker_realized = float(rec.get("realized_pnl_usd") or 0.0)

    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(SUM(closed_pnl), 0.0) as total_pnl, COALESCE(SUM(fee), 0.0) as total_fees, count(*) as count FROM closed_trades WHERE fed_to_breaker = 1")
        row = cur.fetchone()
        journal_realized = float(row["total_pnl"] if row else 0.0)
        journal_fees = float(row["total_fees"] if row else 0.0)
        fed_count = int(row["count"] if row else 0)

        cur.execute("SELECT count(*) as count FROM closed_trades WHERE fed_to_breaker = 0")
        unfed_count = int(cur.fetchone()["count"])

    discrepancy = round(abs(breaker_realized - journal_realized), 4)
    is_reconciled = (discrepancy <= 0.01) and (unfed_count == 0)

    return {
        "trading_date": rec.get("trading_date"),
        "breaker_realized_usd": round(breaker_realized, 4),
        "journal_realized_usd": round(journal_realized, 4),
        "journal_fees_usd": round(journal_fees, 4),
        "journal_net_usd": round(journal_realized - journal_fees, 4),
        "fed_trades_count": fed_count,
        "unfed_trades_count": unfed_count,
        "discrepancy_usd": discrepancy,
        "is_reconciled": is_reconciled,
    }


def can_execute_new_daily_entries(
    trading_date: Optional[str] = None,
    max_staleness_seconds: int = 900,
    dsn: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None
) -> Tuple[bool, str]:
    """Helper returning (can_trade, risk_state) enforcing strict fail-closed semantics."""
    eval_res = evaluate_daily_risk_guard(
        trading_date=trading_date,
        max_staleness_seconds=max_staleness_seconds,
        dsn=dsn,
        db_path=db_path
    )
    return eval_res["can_trade"], eval_res["risk_state"]



# ---------------------------------------------------------------------------
# DURABLE CLOSED TRADES & PERSISTENT NOTIFICATION OUTBOX (Step 8)
# ---------------------------------------------------------------------------

def save_closed_trade(trade_data: Dict[str, Any], dsn: Optional[str] = None, db_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Persist an exchange-verified closed trade event durably and idempotently."""
    close_id = trade_data.get("close_id")
    if not close_id:
        raise ValueError("close_id is required for persistent closed trade")

    engine = init_db(db_path=db_path, dsn=dsn)
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM closed_trades WHERE close_id = ?", (close_id,))
        existing = cur.fetchone()
        if existing:
            res = dict(existing)
            res["_is_new"] = False
            return res

        if engine == "postgres":
            cur.execute("""
                INSERT INTO closed_trades (
                    close_id, order_id, order_link_id, symbol, side, qty,
                    entry_price, exit_price, closed_pnl, fee, is_partial, fill_count,
                    leverage, exchange_created_at, exchange_updated_at, fed_to_breaker, notified, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (close_id) DO NOTHING
            """, (
                close_id,
                trade_data.get("order_id"),
                trade_data.get("order_link_id"),
                trade_data.get("symbol"),
                trade_data.get("side"),
                str(trade_data.get("qty", "0")),
                float(trade_data.get("entry_price") or 0.0),
                float(trade_data.get("exit_price") or 0.0),
                float(trade_data.get("closed_pnl") or 0.0),
                float(trade_data.get("fee") or 0.0),
                int(trade_data.get("is_partial") or 0),
                int(trade_data.get("fill_count") or 1),
                str(trade_data.get("leverage") or ""),
                trade_data.get("exchange_created_at"),
                trade_data.get("exchange_updated_at"),
                0,
                0,
                now_iso,
            ))
        else:
            cur.execute("""
                INSERT OR IGNORE INTO closed_trades (
                    close_id, order_id, order_link_id, symbol, side, qty,
                    entry_price, exit_price, closed_pnl, fee, is_partial, fill_count,
                    leverage, exchange_created_at, exchange_updated_at, fed_to_breaker, notified, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                close_id,
                trade_data.get("order_id"),
                trade_data.get("order_link_id"),
                trade_data.get("symbol"),
                trade_data.get("side"),
                str(trade_data.get("qty", "0")),
                float(trade_data.get("entry_price") or 0.0),
                float(trade_data.get("exit_price") or 0.0),
                float(trade_data.get("closed_pnl") or 0.0),
                float(trade_data.get("fee") or 0.0),
                int(trade_data.get("is_partial") or 0),
                int(trade_data.get("fill_count") or 1),
                str(trade_data.get("leverage") or ""),
                trade_data.get("exchange_created_at"),
                trade_data.get("exchange_updated_at"),
                0,
                0,
                now_iso,
            ))
        conn.commit()

        cur.execute("SELECT * FROM closed_trades WHERE close_id = ?", (close_id,))
        created = cur.fetchone()
        res = dict(created) if created else {}
        res["_is_new"] = True
        return res


def get_closed_trades(
    symbol: Optional[str] = None,
    limit: int = 50,
    dsn: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """Retrieve durable closed trade records."""
    init_db(db_path=db_path, dsn=dsn)
    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        if symbol:
            cur.execute("SELECT * FROM closed_trades WHERE symbol = ? ORDER BY id DESC LIMIT ?", (symbol, limit))
        else:
            cur.execute("SELECT * FROM closed_trades ORDER BY id DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        return [dict(r) for r in rows]


def feed_closed_trades_to_daily_breaker(
    trading_date: Optional[str] = None,
    dsn: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Feed un-fed durable closed trades into daily risk tracking and circuit breaker atomically."""
    init_db(db_path=db_path, dsn=dsn)
    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM closed_trades WHERE fed_to_breaker = 0 ORDER BY id ASC")
        unfed_rows = [dict(r) for r in cur.fetchall()]

    if not unfed_rows:
        return get_daily_risk_record(trading_date=trading_date, dsn=dsn, db_path=db_path)

    for row in unfed_rows:
        pnl = float(row.get("closed_pnl") or 0.0)
        is_win = pnl >= 0.0
        trade_id = row["id"]

        update_daily_risk_pnl(
            trading_date=trading_date,
            realized_pnl_delta=pnl,
            is_win=is_win,
            dsn=dsn,
            db_path=db_path,
        )

        with get_db_connection(db_path=db_path, dsn=dsn) as conn:
            c = conn.cursor()
            c.execute("UPDATE closed_trades SET fed_to_breaker = 1 WHERE id = ?", (trade_id,))
            conn.commit()

    return get_daily_risk_record(trading_date=trading_date, dsn=dsn, db_path=db_path)


def queue_notification(
    event_key: str,
    event_type: str,
    payload: str,
    max_retries: int = 5,
    dsn: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Enqueue an alert into the persistent notification outbox idempotently."""
    engine = init_db(db_path=db_path, dsn=dsn)
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM notification_outbox WHERE event_key = ?", (event_key,))
        existing = cur.fetchone()
        if existing:
            return dict(existing)

        if engine == "postgres":
            cur.execute("""
                INSERT INTO notification_outbox (
                    event_key, event_type, payload, status, retry_count, max_retries,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, 'PENDING', 0, %s, %s, %s)
                ON CONFLICT (event_key) DO NOTHING
            """, (event_key, event_type, payload, max_retries, now_iso, now_iso))
        else:
            cur.execute("""
                INSERT OR IGNORE INTO notification_outbox (
                    event_key, event_type, payload, status, retry_count, max_retries,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'PENDING', 0, ?, ?, ?)
            """, (event_key, event_type, payload, max_retries, now_iso, now_iso))
        conn.commit()

        cur.execute("SELECT * FROM notification_outbox WHERE event_key = ?", (event_key,))
        created = cur.fetchone()
        return dict(created) if created else {}


def get_pending_notifications(
    limit: int = 50,
    dsn: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """Retrieve pending notifications awaiting dispatch."""
    init_db(db_path=db_path, dsn=dsn)
    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM notification_outbox
            WHERE status IN ('PENDING', 'FAILED') AND retry_count < max_retries
            ORDER BY id ASC LIMIT ?
        """, (limit,))
        return [dict(r) for r in cur.fetchall()]


def mark_notification_sent(
    outbox_id: int,
    dsn: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> None:
    """Mark outbox notification as successfully dispatched."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE notification_outbox
            SET status = 'SENT', sent_at = ?, updated_at = ?
            WHERE id = ?
        """, (now_iso, now_iso, outbox_id))
        conn.commit()


def mark_notification_failed(
    outbox_id: int,
    error_message: str,
    dsn: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> None:
    """Record dispatch failure and increment retry counter."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE notification_outbox
            SET status = 'FAILED', retry_count = retry_count + 1, last_error = ?, updated_at = ?
            WHERE id = ?
        """, (error_message[:500], now_iso, outbox_id))
        conn.commit()


def mark_closed_trade_notified(
    close_id: str,
    dsn: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> None:
    """Mark a closed trade as notified in durable database."""
    init_db(db_path=db_path, dsn=dsn)
    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        cur.execute("UPDATE closed_trades SET notified = 1 WHERE close_id = ?", (close_id,))
        conn.commit()


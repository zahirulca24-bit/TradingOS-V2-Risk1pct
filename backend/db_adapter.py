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
    clean_sql = sql.replace("?", "%s")
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
    """Initialize all journal and pipeline tables/indexes idempotently.
    
    Returns:
        Engine name: 'postgres' or 'sqlite'.
    """
    engine = get_active_engine(db_path=db_path, dsn=dsn)
    schema_sql = POSTGRES_SCHEMA_SQL if engine == "postgres" else SQLITE_SCHEMA_SQL
    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        conn.executescript(schema_sql)
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
            # Update active cycle pointer in pipeline_meta
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

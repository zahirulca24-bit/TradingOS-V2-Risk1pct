"""TradingOS V2 Signal Outcome Tracker & Post-Mortem Engine.
Tracks all PLAN_READY signals across subsequent 15m candles to record:
- Outcome: HIT_TP, HIT_SL, or ACTIVE
- Exit price, exit timestamp, elapsed candles
- Algorithmic Post-Mortem analysis explaining why SL or TP was reached
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import urllib.request

from db_adapter import get_active_engine, get_db_connection


CREATE_SIGNAL_TRACKING_SQL = """
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
"""

SQLITE_CREATE_SIGNAL_TRACKING_SQL = """
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
"""


def init_signal_tracking_table():
    """Ensure tracking table exists in active database engine."""
    engine = get_active_engine()
    sql = CREATE_SIGNAL_TRACKING_SQL if engine == "postgres" else SQLITE_CREATE_SIGNAL_TRACKING_SQL
    with get_db_connection() as conn:
        conn.executescript(sql)
        conn.commit()


def fetch_symbol_candles(symbol: str, interval: str = "15", limit: int = 40) -> List[Dict[str, float]]:
    """Fetch recent closed candles from Bybit public endpoint."""
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 TradingOS/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw = data.get("result", {}).get("list", [])
            candles = []
            for c in reversed(raw):
                candles.append({
                    "time": int(c[0]) // 1000,
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                })
            return candles
    except Exception as exc:
        print(f"WARNING: Could not fetch tracking candles for {symbol}:", exc)
        return []


def register_new_ready_signals(reports: Dict[str, Any]):
    """Insert newly published PLAN_READY signals into tracking table."""
    plans = reports.get("trade_plans", {})
    cycle_id = plans.get("cycle_id", "")
    close_utc = plans.get("candle_close_utc", "")
    ready_plans = [p for p in plans.get("results", []) if p.get("status") == "PLAN_READY"]

    if not ready_plans:
        return

    init_signal_tracking_table()
    engine = get_active_engine()
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db_connection() as conn:
        cur = conn.cursor()
        for p in ready_plans:
            sym = p.get("symbol")
            direction = p.get("direction", "LONG")
            entry = float(p.get("entry_reference", 0))
            sl = float(p.get("sl", 0))
            tp = float(p.get("tp", 0))
            rr = float(p.get("rr", 0))
            pattern = p.get("pattern", "SWEEP_CONFIRMED")
            candle_time = int(p.get("candle_time") or 0)

            if engine == "postgres":
                cur.execute("""
                    INSERT INTO journal_signal_tracking (
                        cycle_id, symbol, direction, entry_reference, sl, tp, rr,
                        pattern, candle_time, candle_close_utc, status, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'ACTIVE', %s)
                    ON CONFLICT (cycle_id, symbol) DO NOTHING
                """, (cycle_id, sym, direction, entry, sl, tp, rr, pattern, candle_time, close_utc, now_iso))
            else:
                cur.execute("""
                    INSERT OR IGNORE INTO journal_signal_tracking (
                        cycle_id, symbol, direction, entry_reference, sl, tp, rr,
                        pattern, candle_time, candle_close_utc, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?)
                """, (cycle_id, sym, direction, entry, sl, tp, rr, pattern, candle_time, close_utc, now_iso))
        conn.commit()


def update_active_signal_outcomes() -> List[Dict[str, Any]]:
    """Evaluates all ACTIVE signals against subsequent market candles.
    Returns list of newly resolved signals (with TP or SL hit).
    """
    init_signal_tracking_table()
    engine = get_active_engine()
    resolved_signals = []

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM journal_signal_tracking WHERE status = 'ACTIVE' ORDER BY candle_time ASC")
        active_rows = cur.fetchall()

        for row in active_rows:
            sym = row["symbol"]
            direction = row["direction"]
            entry = float(row["entry_reference"])
            sl = float(row["sl"])
            tp = float(row["tp"])
            start_time = int(row["candle_time"])
            cycle_id = row["cycle_id"]

            candles = fetch_symbol_candles(sym, interval="15", limit=50)
            after_candles = [c for c in candles if c["time"] > start_time]

            outcome = None
            exit_price = None
            exit_time_str = None
            exit_idx = None
            post_mortem = None

            for idx, c in enumerate(after_candles, 1):
                c_iso = datetime.fromtimestamp(c["time"], tz=timezone.utc).isoformat()
                if direction == "LONG":
                    if c["low"] <= sl:
                        outcome = "HIT_SL"
                        exit_price = sl
                        exit_time_str = c_iso
                        exit_idx = idx
                        # Post-mortem analysis
                        if c["close"] > sl:
                            post_mortem = "Secondary liquidity sweep pierced below SL before closing higher."
                        else:
                            post_mortem = "Strong bearish continuation broke below market support zone."
                        break
                    elif c["high"] >= tp:
                        outcome = "HIT_TP"
                        exit_price = tp
                        exit_time_str = c_iso
                        exit_idx = idx
                        post_mortem = "Target swing resistance liquidity captured successfully."
                        break
                else: # SHORT
                    if c["high"] >= sl:
                        outcome = "HIT_SL"
                        exit_price = sl
                        exit_time_str = c_iso
                        exit_idx = idx
                        if c["close"] < sl:
                            post_mortem = "Secondary liquidity sweep pierced above SL before closing lower."
                        else:
                            post_mortem = "Strong bullish continuation broke above market resistance zone."
                        break
                    elif c["low"] <= tp:
                        outcome = "HIT_TP"
                        exit_price = tp
                        exit_time_str = c_iso
                        exit_idx = idx
                        post_mortem = "Target swing support liquidity captured successfully."
                        break

            if outcome:
                # Update row
                placeholder = "%s" if engine == "postgres" else "?"
                cur.execute(f"""
                    UPDATE journal_signal_tracking
                    SET status = {placeholder}, exit_price = {placeholder}, exit_time = {placeholder},
                        exit_candle_index = {placeholder}, post_mortem = {placeholder}
                    WHERE cycle_id = {placeholder} AND symbol = {placeholder}
                """, (outcome, exit_price, exit_time_str, exit_idx, post_mortem, cycle_id, sym))
                conn.commit()

                resolved_item = dict(row)
                resolved_item.update({
                    "status": outcome,
                    "exit_price": exit_price,
                    "exit_time": exit_time_str,
                    "exit_candle_index": exit_idx,
                    "post_mortem": post_mortem
                })
                resolved_signals.append(resolved_item)

    return resolved_signals


def get_daily_tracking_summary(target_date: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve full tracking summary for a given day (defaults to current UTC day)."""
    init_signal_tracking_table()
    if not target_date:
        target_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with get_db_connection() as conn:
        cur = conn.cursor()
        engine = get_active_engine()
        placeholder = "%s" if engine == "postgres" else "?"
        date_expr = "to_char(candle_close_utc, 'YYYY-MM-DD')" if engine == "postgres" else "substr(candle_close_utc, 1, 10)"
        cur.execute(f"""
            SELECT * FROM journal_signal_tracking 
            WHERE {date_expr} = {placeholder}
            ORDER BY candle_time ASC
        """, (target_date,))
        signals = [dict(r) for r in cur.fetchall()]

        # Query cycle statistics
        cur.execute(f"""
            SELECT count(*) as total_cycles,
                   sum(total_scanned) as total_scanned,
                   sum(directional_count) as total_directional
            FROM journal_cycles
            WHERE {date_expr} = {placeholder}
        """, (target_date,))
        cycle_stats = cur.fetchone() or {"total_cycles": 0, "total_scanned": 0, "total_directional": 0}

    won_count = sum(1 for s in signals if s["status"] == "HIT_TP")
    lost_count = sum(1 for s in signals if s["status"] == "HIT_SL")
    active_count = sum(1 for s in signals if s["status"] == "ACTIVE")

    return {
        "date": target_date,
        "total_cycles": cycle_stats.get("total_cycles") or 0,
        "total_scanned": cycle_stats.get("total_scanned") or 0,
        "total_directional": cycle_stats.get("total_directional") or 0,
        "signals": signals,
        "won_count": won_count,
        "lost_count": lost_count,
        "active_count": active_count,
    }

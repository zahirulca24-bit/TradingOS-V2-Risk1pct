"""Durable, restart-safe SQLite research journal and daily reporting engine.
Strictly DRY_RUN_ONLY, research evidence only. Zero order execution.
"""
import os
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
DB_DIR = ROOT_DIR / "database"
DEFAULT_DB_PATH = DB_DIR / "tradingos_journal.db"
CYCLES_DIR = Path(__file__).resolve().parent / "pipeline_state" / "cycles"

SCHEMA_VERSION = "1.0"

SCHEMA_SQL = """
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
"""


from db_adapter import (
    get_db_connection,
    get_active_engine,
    init_db as init_adapter_db,
    DEFAULT_SQLITE_PATH
)


def init_db(db_path: Optional[Path] = None, auto_backfill: bool = True) -> None:
    """Initialize database tables and indexes safely (PostgreSQL or SQLite)."""
    engine = get_active_engine(db_path=db_path)
    target_path = Path(db_path) if db_path else DEFAULT_DB_PATH
    is_new = not target_path.exists() if engine == "sqlite" else False
    init_adapter_db(db_path=db_path)
    if is_new and auto_backfill and not db_path:
        try:
            backfill_historical_cycles(db_path=target_path)
        except Exception as exc:
            print("WARNING: Initial historical cycle backfill error:", exc)


def ingest_cycle(reports: Dict[str, Any], dry_runs: Optional[List[Dict[str, Any]]] = None, db_path: Optional[Path] = None) -> Tuple[bool, int]:
    """Ingest a validated committed cycle into the journal idempotently.

    Rejects partial or malformed cycle data. Uses transactions and UNIQUE constraints
    to ensure restart-safe, duplicate-free storage.
    """
    from pipeline_store import validate_reports
    validate_reports(reports)

    scanner = reports["scanner"]
    plans = reports["trade_plans"]
    opportunities = reports["opportunities"]

    cycle_id = scanner["cycle_id"]
    candle_close = scanner["candle_close_utc"]
    committed_at = scanner.get("generated_at") or datetime.now(timezone.utc).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    init_db(db_path)

    with get_db_connection(db_path) as conn:
        cur = conn.cursor()
        # 1. Insert cycle header
        cur.execute("""
            INSERT OR IGNORE INTO journal_cycles (
                cycle_id, candle_close_utc, committed_at, status,
                total_scanned, directional_count, neutral_excluded,
                ready_count, wait_count, rejected_count,
                schema_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            cycle_id,
            candle_close,
            committed_at,
            "success",
            scanner.get("total_scanned", len(scanner["results"])),
            scanner.get("directional_count", len(scanner["results"])),
            scanner.get("neutral_excluded", 0),
            plans.get("ready_count", sum(1 for p in plans["results"] if p.get("status") == "PLAN_READY")),
            plans.get("wait_count", sum(1 for p in plans["results"] if p.get("status") == "WAIT")),
            plans.get("rejected_count", sum(1 for p in plans["results"] if p.get("status") == "REJECTED")),
            SCHEMA_VERSION,
            now_iso
        ))

        # Build opp lookup for sweep and direction details
        opp_map = {}
        for opp in opportunities.get("top_10", []) + opportunities.get("remaining", []):
            opp_map[opp["symbol"]] = opp

        # 2. Insert signals
        inserted_signals = 0
        for plan in plans["results"]:
            sym = plan["symbol"]
            trend = plan.get("trend", "UNKNOWN")
            direction = plan.get("direction") or opp_map.get(sym, {}).get("direction")
            if not direction:
                if trend == "BULLISH":
                    direction = "LONG"
                elif trend == "BEARISH":
                    direction = "SHORT"

            status = plan.get("status", "WAIT")
            reason = plan.get("reason", "No reason provided")
            pattern = plan.get("pattern")
            sweep_level = plan.get("sweep_level")
            sweep_time = plan.get("candle_time")

            # Trade execution levels strictly recorded only if genuinely calculated
            is_ready = status == "PLAN_READY"
            entry_ref = plan.get("entry_reference") if is_ready else None
            sl = plan.get("sl") if is_ready else None
            tp = plan.get("tp") if is_ready else None
            rr = plan.get("rr") if is_ready else None

            cur.execute("""
                INSERT OR IGNORE INTO journal_signals (
                    cycle_id, candle_close_utc, symbol, trend, direction,
                    status, reason, pattern, sweep_level, sweep_time,
                    entry_reference, sl, tp, rr, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                cycle_id,
                candle_close,
                sym,
                trend,
                direction,
                status,
                reason,
                pattern,
                sweep_level,
                sweep_time,
                entry_ref,
                sl,
                tp,
                rr,
                now_iso
            ))
            if cur.rowcount > 0:
                inserted_signals += 1

        # 3. Evaluate and insert dry-runs if ready plans exist
        if dry_runs is None:
            ready_plans = [p for p in plans.get("results", []) if p.get("status") == "PLAN_READY"]
            if ready_plans:
                try:
                    from demo_dashboard_api import read_demo_account
                    from demo_risk_engine import size_dry_run, get_cached_instrument_specs
                    account = read_demo_account()
                    if account and account.get("total_equity"):
                        open_positions = None if account.get("positions_error") else account.get("open_positions")
                        pending_entries = None if account.get("orders_error") or account.get("orders_first_page_only") else account.get("pending_entry_orders")
                        auto_dry_runs = []
                        for rp in ready_plans:
                            sym = rp.get("symbol")
                            specs = get_cached_instrument_specs(sym) if sym else None
                            preview = size_dry_run(
                                rp,
                                account.get("total_equity"),
                                open_positions=open_positions,
                                pending_orders=pending_entries,
                                cycle_fresh=True,
                                account_fresh=True,
                                instrument_specs=specs,
                            )
                            auto_dry_runs.append(preview)
                        dry_runs = auto_dry_runs
                except Exception:
                    pass

        if dry_runs:
            for dr in dry_runs:
                cur.execute("""
                    INSERT OR IGNORE INTO journal_dry_runs (
                        cycle_id, symbol, direction, status,
                        reasons, reason_messages, equity_basis_usd, risk_budget_usd,
                        proposed_quantity, qty_step, entry_reference, sl, tp, rr,
                        estimated_fees_slippage_usd, total_projected_risk_usd,
                        occupied_slots, remaining_slots, order_execution, evaluated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    cycle_id,
                    dr["symbol"],
                    dr.get("direction", "UNKNOWN"),
                    dr.get("status", "BLOCKED_DATA"),
                    json.dumps(dr.get("reasons", [])),
                    json.dumps(dr.get("reason_messages", [])),
                    dr.get("equity_basis_usd"),
                    dr.get("risk_budget_usd"),
                    str(dr.get("proposed_quantity")) if dr.get("proposed_quantity") is not None else None,
                    str(dr.get("qty_step")) if dr.get("qty_step") is not None else None,
                    dr.get("entry_reference"),
                    dr.get("sl"),
                    dr.get("tp"),
                    dr.get("rr"),
                    dr.get("estimated_fees_and_slippage_usd"),
                    dr.get("total_conservative_projected_risk_usd"),
                    dr.get("existing_slots"),
                    dr.get("remaining_slots"),
                    "ORDER_EXECUTION_DISABLED",
                    now_iso
                ))

        conn.commit()

    return True, inserted_signals


def record_dry_runs(cycle_id: str, dry_runs: List[Dict[str, Any]], db_path: Optional[Path] = None) -> int:
    """Insert or replace dry-run evaluations for a cycle, deduplicating per (cycle_id, symbol)."""
    if not dry_runs:
        return 0
    init_db(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    inserted = 0
    with get_db_connection(db_path) as conn:
        cur = conn.cursor()
        for dr in dry_runs:
            sym = dr.get("symbol")
            if not sym:
                continue
            cur.execute("""
                INSERT INTO journal_dry_runs (
                    cycle_id, symbol, direction, status,
                    reasons, reason_messages, equity_basis_usd, risk_budget_usd,
                    proposed_quantity, qty_step, entry_reference, sl, tp, rr,
                    estimated_fees_slippage_usd, total_projected_risk_usd,
                    occupied_slots, remaining_slots, order_execution, evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cycle_id, symbol) DO UPDATE SET
                    direction=excluded.direction,
                    status=excluded.status,
                    reasons=excluded.reasons,
                    reason_messages=excluded.reason_messages,
                    equity_basis_usd=excluded.equity_basis_usd,
                    risk_budget_usd=excluded.risk_budget_usd,
                    proposed_quantity=excluded.proposed_quantity,
                    qty_step=excluded.qty_step,
                    entry_reference=excluded.entry_reference,
                    sl=excluded.sl,
                    tp=excluded.tp,
                    rr=excluded.rr,
                    estimated_fees_slippage_usd=excluded.estimated_fees_slippage_usd,
                    total_projected_risk_usd=excluded.total_projected_risk_usd,
                    occupied_slots=excluded.occupied_slots,
                    remaining_slots=excluded.remaining_slots,
                    evaluated_at=excluded.evaluated_at
            """, (
                cycle_id,
                sym,
                dr.get("direction", "UNKNOWN"),
                dr.get("status", "BLOCKED_DATA"),
                json.dumps(dr.get("reasons", [])),
                json.dumps(dr.get("reason_messages", [])),
                dr.get("equity_basis_usd"),
                dr.get("risk_budget_usd"),
                str(dr.get("proposed_quantity")) if dr.get("proposed_quantity") is not None else None,
                str(dr.get("qty_step")) if dr.get("qty_step") is not None else None,
                dr.get("entry_reference"),
                dr.get("sl"),
                dr.get("tp"),
                dr.get("rr"),
                dr.get("estimated_fees_and_slippage_usd"),
                dr.get("total_conservative_projected_risk_usd"),
                dr.get("existing_slots"),
                dr.get("remaining_slots"),
                "ORDER_EXECUTION_DISABLED",
                now_iso
            ))
            inserted += 1
        conn.commit()
    return inserted



def backfill_historical_cycles(cycles_dir: Optional[Path] = None, db_path: Optional[Path] = None) -> Tuple[int, int]:
    """Idempotently backfill existing verified committed cycle snapshots from disk."""
    target_dir = Path(cycles_dir) if cycles_dir else CYCLES_DIR
    if not target_dir.is_dir():
        return 0, 0

    init_db(db_path)
    imported_cycles = 0
    total_signals = 0

    cycle_subdirs = sorted([d for d in target_dir.iterdir() if d.is_dir()], key=lambda d: d.name)
    for c_dir in cycle_subdirs:
        scanner_file = c_dir / "scanner_snapshot.json"
        plans_file = c_dir / "trade_plans_snapshot.json"
        opps_file = c_dir / "opportunities_snapshot.json"

        if not (scanner_file.is_file() and plans_file.is_file() and opps_file.is_file()):
            continue

        try:
            reports = {
                "scanner": json.loads(scanner_file.read_text(encoding="utf-8")),
                "trade_plans": json.loads(plans_file.read_text(encoding="utf-8")),
                "opportunities": json.loads(opps_file.read_text(encoding="utf-8")),
            }
            ok, count = ingest_cycle(reports, db_path=db_path)
            if ok:
                imported_cycles += 1
                total_signals += count
        except Exception:
            # Skip invalid or uncommitted historical directories
            continue

    return imported_cycles, total_signals


def get_daily_reports(target_date: Optional[str] = None, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Generate deterministic UTC-day research reports from persisted journal records."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cur = conn.cursor()

        date_condition = "WHERE substr(candle_close_utc, 1, 10) = ?" if target_date else ""
        date_params = (target_date,) if target_date else ()

        cur.execute(f"""
            SELECT DISTINCT substr(candle_close_utc, 1, 10) as utc_date
            FROM journal_cycles
            {date_condition}
            ORDER BY utc_date DESC
        """, date_params)

        dates = [row["utc_date"] for row in cur.fetchall()]
        reports = []

        for d in dates:
            # 1. Cycles info for this day
            cur.execute("""
                SELECT cycle_id, candle_close_utc, committed_at, total_scanned,
                       directional_count, neutral_excluded, ready_count, wait_count, rejected_count
                FROM journal_cycles
                WHERE substr(candle_close_utc, 1, 10) = ?
                ORDER BY candle_close_utc DESC
            """, (d,))
            cycles_rows = cur.fetchall()
            cycle_count = len(cycles_rows)
            latest_cycle = cycles_rows[0] if cycles_rows else None

            # 2. Signals stats for this day
            cur.execute("""
                SELECT
                    count(*) as total_signals,
                    count(DISTINCT symbol) as unique_symbols,
                    sum(CASE WHEN status = 'PLAN_READY' THEN 1 ELSE 0 END) as ready_count,
                    sum(CASE WHEN status = 'WAIT' THEN 1 ELSE 0 END) as wait_count,
                    sum(CASE WHEN status = 'REJECTED' THEN 1 ELSE 0 END) as rejected_count,
                    sum(CASE WHEN trend = 'BULLISH' OR direction = 'LONG' THEN 1 ELSE 0 END) as bullish_count,
                    sum(CASE WHEN trend = 'BEARISH' OR direction = 'SHORT' THEN 1 ELSE 0 END) as bearish_count
                FROM journal_signals
                WHERE substr(candle_close_utc, 1, 10) = ?
            """, (d,))
            sig_stats = cur.fetchone()

            # 3. Dry-run stats for this day
            cur.execute("""
                SELECT
                    count(*) as total_dry_runs,
                    sum(CASE WHEN status = 'DRY_RUN_VALID' THEN 1 ELSE 0 END) as valid_count,
                    sum(CASE WHEN status LIKE 'BLOCKED_%' THEN 1 ELSE 0 END) as blocked_count
                FROM journal_dry_runs
                WHERE substr(evaluated_at, 1, 10) = ?
            """, (d,))
            dr_stats = cur.fetchone()

            # 4. Top reasons breakdown
            cur.execute("""
                SELECT reason, count(*) as reason_count
                FROM journal_signals
                WHERE substr(candle_close_utc, 1, 10) = ? AND status IN ('WAIT', 'REJECTED')
                GROUP BY reason
                ORDER BY reason_count DESC
                LIMIT 5
            """, (d,))
            top_reasons = [{"reason": row["reason"], "count": row["reason_count"]} for row in cur.fetchall()]

            # 5. Dry-run blocked reasons breakdown
            cur.execute("""
                SELECT status, count(*) as status_count
                FROM journal_dry_runs
                WHERE substr(evaluated_at, 1, 10) = ? AND status != 'DRY_RUN_VALID'
                GROUP BY status
                ORDER BY status_count DESC
            """, (d,))
            blocked_breakdown = [{"status": row["status"], "count": row["status_count"]} for row in cur.fetchall()]

            # Completeness warning: expected 96 cycles per full 24h day (4 cycles/hr * 24hr)
            expected_cycles_full_day = 96
            completeness_note = (
                "Full operational coverage."
                if cycle_count >= 96
                else f"Partial day coverage ({cycle_count} of ~96 cycles logged; scheduler active during observation hours)."
            )

            reports.append({
                "utc_date": d,
                "cycles_committed": cycle_count,
                "latest_cycle_id": latest_cycle["cycle_id"] if latest_cycle else None,
                "latest_candle_close_utc": latest_cycle["candle_close_utc"] if latest_cycle else None,
                "total_signal_observations": sig_stats["total_signals"] or 0,
                "unique_symbols_examined": sig_stats["unique_symbols"] or 0,
                "plan_ready_count": sig_stats["ready_count"] or 0,
                "wait_count": sig_stats["wait_count"] or 0,
                "rejected_count": sig_stats["rejected_count"] or 0,
                "bullish_observations": sig_stats["bullish_count"] or 0,
                "bearish_observations": sig_stats["bearish_count"] or 0,
                "dry_run_evaluations": dr_stats["total_dry_runs"] or 0,
                "dry_run_valid_count": dr_stats["valid_count"] or 0,
                "dry_run_blocked_count": dr_stats["blocked_count"] or 0,
                "top_recurring_reasons": top_reasons,
                "blocked_breakdown": blocked_breakdown,
                "completeness_note": completeness_note,
                # Explicit performance disclosures:
                "realized_pnl": "N/A",
                "executed_trades": "0 known to this read-only journal, not a claim about exchange-wide account activity",
                "win_rate": "N/A",
                "roi": "N/A",
                "disclosure": (
                    "Signal observation counts represent periodic 15m scanner evaluation states, "
                    "not unique trading opportunities. This is a read-only research audit trail; "
                    "no demo or real order execution is initiated or recorded."
                )
            })

        return reports


def sanitize_csv_cell(value: Any) -> str:
    """Neutralize spreadsheet formula injection for values beginning with =, +, -, @ or control characters."""
    if value is None:
        return ""
    text = str(value)
    # RFC 4180 / Spreadsheet safety: if text begins with =, +, -, @, \t, \r, prepend single quote '
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        text = f"'{text}"
    return text

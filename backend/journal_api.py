"""Read-only FastAPI endpoints for the TradingOS V2 Research Journal.
Includes signal logs, dry-run previews, daily reports, and injection-safe CSV exports.
Strictly DRY_RUN_ONLY, research evidence only.
"""
import io
import csv
import json
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Query, Response, HTTPException

from fastapi.responses import StreamingResponse

from journal_db import (
    get_db_connection,
    init_db,
    get_daily_reports,
    sanitize_csv_cell,
    DEFAULT_DB_PATH
)

router = APIRouter(prefix="/journal", tags=["Research Journal"])


def _resolve_arg(val, default):
    if hasattr(val, "default"):
        return val.default
    return default if val is None else val


@router.get("/health")
def journal_health():
    """Return SQLite journal storage health, row counts, and latest committed cycle."""
    init_db()
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) as count FROM journal_cycles")
        cycles_count = cur.fetchone()["count"]

        cur.execute("SELECT count(*) as count FROM journal_signals")
        signals_count = cur.fetchone()["count"]

        cur.execute("SELECT count(*) as count FROM journal_dry_runs")
        dry_runs_count = cur.fetchone()["count"]

        cur.execute("SELECT cycle_id, candle_close_utc, committed_at FROM journal_cycles ORDER BY candle_close_utc DESC LIMIT 1")
        latest = cur.fetchone()

    return {
        "status": "healthy",
        "database_type": "SQLite (WAL mode)",
        "database_path": str(DEFAULT_DB_PATH),
        "total_cycles_logged": cycles_count,
        "total_signals_logged": signals_count,
        "total_dry_runs_logged": dry_runs_count,
        "latest_cycle_id": latest["cycle_id"] if latest else None,
        "latest_candle_close_utc": latest["candle_close_utc"] if latest else None,
        "mode": "DRY_RUN_ONLY",
        "order_execution": "ORDER_EXECUTION_DISABLED"
    }


@router.get("/cycles")
def get_journal_cycles(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: str = Query("desc", pattern="^(asc|desc)$")
):
    """Return paginated list of successfully committed research cycles."""
    limit = _resolve_arg(limit, 50)
    offset = _resolve_arg(offset, 0)
    sort = _resolve_arg(sort, "desc")

    init_db()
    order_clause = "DESC" if str(sort).lower() == "desc" else "ASC"

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) as total FROM journal_cycles")
        total = cur.fetchone()["total"]

        cur.execute(f"""
            SELECT cycle_id, candle_close_utc, committed_at, status,
                   total_scanned, directional_count, neutral_excluded,
                   ready_count, wait_count, rejected_count, schema_version
            FROM journal_cycles
            ORDER BY candle_close_utc {order_clause}
            LIMIT ? OFFSET ?
        """, (limit, offset))
        rows = [dict(r) for r in cur.fetchall()]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "cycles": rows
    }


@router.get("/signals")
def get_journal_signals(
    cycle_id: Optional[str] = None,
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    direction: Optional[str] = None,
    trend: Optional[str] = None,
    date: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: str = Query("desc", pattern="^(asc|desc)$")
):
    """Return paginated signal observations with multi-criteria filtering."""
    cycle_id = _resolve_arg(cycle_id, None)
    symbol = _resolve_arg(symbol, None)
    status = _resolve_arg(status, None)
    direction = _resolve_arg(direction, None)
    trend = _resolve_arg(trend, None)
    date = _resolve_arg(date, None)
    limit = _resolve_arg(limit, 50)
    offset = _resolve_arg(offset, 0)
    sort = _resolve_arg(sort, "desc")

    init_db()
    conditions = []
    params = []

    if cycle_id:
        conditions.append("cycle_id = ?")
        params.append(cycle_id)
    if symbol:
        conditions.append("symbol LIKE ?")
        params.append(f"%{symbol.strip().upper()}%")
    if status and status.upper() != "ALL":
        conditions.append("status = ?")
        params.append(status.upper())
    if direction and direction.upper() != "ALL":
        conditions.append("direction = ?")
        params.append(direction.upper())
    if trend and trend.upper() != "ALL":
        conditions.append("trend = ?")
        params.append(trend.upper())
    if date:
        conditions.append("substr(candle_close_utc, 1, 10) = ?")
        params.append(date.strip())

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    order_clause = "DESC" if str(sort).lower() == "desc" else "ASC"

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT count(*) as total FROM journal_signals {where_clause}", tuple(params))
        total = cur.fetchone()["total"]

        query_params = list(params) + [limit, offset]
        cur.execute(f"""
            SELECT id, cycle_id, candle_close_utc, symbol, trend, direction,
                   status, reason, pattern, sweep_level, sweep_time,
                   entry_reference, sl, tp, rr, created_at
            FROM journal_signals
            {where_clause}
            ORDER BY candle_close_utc {order_clause}, id ASC
            LIMIT ? OFFSET ?
        """, tuple(query_params))
        rows = [dict(r) for r in cur.fetchall()]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "signals": rows
    }


@router.get("/dry-runs")
def get_journal_dry_runs(
    cycle_id: Optional[str] = None,
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    direction: Optional[str] = None,
    date: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: str = Query("desc", pattern="^(asc|desc)$")
):
    """Return paginated dry-run 1% risk position sizing evaluations."""
    cycle_id = _resolve_arg(cycle_id, None)
    symbol = _resolve_arg(symbol, None)
    status = _resolve_arg(status, None)
    direction = _resolve_arg(direction, None)
    date = _resolve_arg(date, None)
    limit = _resolve_arg(limit, 50)
    offset = _resolve_arg(offset, 0)
    sort = _resolve_arg(sort, "desc")

    init_db()
    conditions = []
    params = []


    if cycle_id:
        conditions.append("cycle_id = ?")
        params.append(cycle_id)
    if symbol:
        conditions.append("symbol LIKE ?")
        params.append(f"%{symbol.strip().upper()}%")
    if status and status.upper() != "ALL":
        conditions.append("status = ?")
        params.append(status.upper())
    if direction and direction.upper() != "ALL":
        conditions.append("direction = ?")
        params.append(direction.upper())
    if date:
        conditions.append("substr(evaluated_at, 1, 10) = ?")
        params.append(date.strip())

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    order_clause = "DESC" if sort.lower() == "desc" else "ASC"

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT count(*) as total FROM journal_dry_runs {where_clause}", tuple(params))
        total = cur.fetchone()["total"]

        query_params = list(params) + [limit, offset]
        cur.execute(f"""
            SELECT id, cycle_id, symbol, direction, status,
                   reasons, reason_messages, equity_basis_usd, risk_budget_usd,
                   proposed_quantity, qty_step, entry_reference, sl, tp, rr,
                   estimated_fees_slippage_usd, total_projected_risk_usd,
                   occupied_slots, remaining_slots, order_execution, evaluated_at
            FROM journal_dry_runs
            {where_clause}
            ORDER BY evaluated_at {order_clause}, id ASC
            LIMIT ? OFFSET ?
        """, tuple(query_params))
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            if isinstance(r.get("reasons"), str):
                try:
                    r["reasons"] = json.loads(r["reasons"])
                except Exception:
                    r["reasons"] = [r["reasons"]]
            if isinstance(r.get("reason_messages"), str):
                try:
                    r["reason_messages"] = json.loads(r["reason_messages"])
                except Exception:
                    r["reason_messages"] = [r["reason_messages"]]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "dry_runs": rows
    }



@router.get("/daily")
def get_daily_journal_report(date: Optional[str] = None):
    """Return deterministic UTC-day aggregated research summaries."""
    reports = get_daily_reports(target_date=date)
    return {
        "count": len(reports),
        "target_date": date,
        "reports": reports
    }


@router.get("/export/signals.csv")
def export_signals_csv(
    date: Optional[str] = None,
    cycle_id: Optional[str] = None,
    status: Optional[str] = None
):
    """Export signal observations to RFC 4180 compliant, formula-injection safe CSV."""
    init_db()
    conditions = []
    params = []

    if date:
        conditions.append("substr(candle_close_utc, 1, 10) = ?")
        params.append(date.strip())
    if cycle_id:
        conditions.append("cycle_id = ?")
        params.append(cycle_id.strip())
    if status and status.upper() != "ALL":
        conditions.append("status = ?")
        params.append(status.upper())

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT cycle_id, candle_close_utc, symbol, trend, direction,
                   status, reason, pattern, sweep_level, sweep_time,
                   entry_reference, sl, tp, rr, created_at
            FROM journal_signals
            {where_clause}
            ORDER BY candle_close_utc DESC, symbol ASC
        """, tuple(params))
        rows = cur.fetchall()

    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")

    headers = [
        "Cycle ID", "Candle Close UTC", "Symbol", "1H Trend", "Direction",
        "Plan Status", "Reason", "Pattern", "Sweep Level", "Sweep Time",
        "Entry Reference", "Stop Loss", "Take Profit", "Risk Reward", "Created At UTC"
    ]
    writer.writerow(headers)

    for r in rows:
        writer.writerow([
            sanitize_csv_cell(r["cycle_id"]),
            sanitize_csv_cell(r["candle_close_utc"]),
            sanitize_csv_cell(r["symbol"]),
            sanitize_csv_cell(r["trend"]),
            sanitize_csv_cell(r["direction"]),
            sanitize_csv_cell(r["status"]),
            sanitize_csv_cell(r["reason"]),
            sanitize_csv_cell(r["pattern"]),
            sanitize_csv_cell(r["sweep_level"]),
            sanitize_csv_cell(r["sweep_time"]),
            sanitize_csv_cell(r["entry_reference"]),
            sanitize_csv_cell(r["sl"]),
            sanitize_csv_cell(r["tp"]),
            sanitize_csv_cell(r["rr"]),
            sanitize_csv_cell(r["created_at"]),
        ])

    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    filename = f"tradingos_signals_{today_str}.csv"

    response = Response(content=output.getvalue(), media_type="text/csv")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@router.get("/export/dry-runs.csv")
def export_dry_runs_csv(
    date: Optional[str] = None,
    cycle_id: Optional[str] = None,
    status: Optional[str] = None
):
    """Export dry-run position sizing previews to RFC 4180 compliant, formula-injection safe CSV."""
    init_db()
    conditions = []
    params = []

    if date:
        conditions.append("substr(evaluated_at, 1, 10) = ?")
        params.append(date.strip())
    if cycle_id:
        conditions.append("cycle_id = ?")
        params.append(cycle_id.strip())
    if status and status.upper() != "ALL":
        conditions.append("status = ?")
        params.append(status.upper())

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT cycle_id, symbol, direction, status,
                   reasons, equity_basis_usd, risk_budget_usd, proposed_quantity,
                   qty_step, entry_reference, sl, tp, rr,
                   estimated_fees_slippage_usd, total_projected_risk_usd,
                   occupied_slots, remaining_slots, order_execution, evaluated_at
            FROM journal_dry_runs
            {where_clause}
            ORDER BY evaluated_at DESC, symbol ASC
        """, tuple(params))
        rows = cur.fetchall()

    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")

    headers = [
        "Cycle ID", "Symbol", "Direction", "Dry Run Status",
        "Reasons", "Equity Basis USD", "Risk Budget USD", "Proposed Qty",
        "Qty Step", "Entry Reference", "Stop Loss", "Take Profit", "Risk Reward",
        "Estimated Fees Slippage USD", "Total Projected Risk USD",
        "Occupied Slots", "Remaining Slots", "Order Execution", "Evaluated At UTC"
    ]
    writer.writerow(headers)

    for r in rows:
        writer.writerow([
            sanitize_csv_cell(r["cycle_id"]),
            sanitize_csv_cell(r["symbol"]),
            sanitize_csv_cell(r["direction"]),
            sanitize_csv_cell(r["status"]),
            sanitize_csv_cell(r["reasons"]),
            sanitize_csv_cell(r["equity_basis_usd"]),
            sanitize_csv_cell(r["risk_budget_usd"]),
            sanitize_csv_cell(r["proposed_quantity"]),
            sanitize_csv_cell(r["qty_step"]),
            sanitize_csv_cell(r["entry_reference"]),
            sanitize_csv_cell(r["sl"]),
            sanitize_csv_cell(r["tp"]),
            sanitize_csv_cell(r["rr"]),
            sanitize_csv_cell(r["estimated_fees_slippage_usd"]),
            sanitize_csv_cell(r["total_projected_risk_usd"]),
            sanitize_csv_cell(r["occupied_slots"]),
            sanitize_csv_cell(r["remaining_slots"]),
            sanitize_csv_cell(r["order_execution"]),
            sanitize_csv_cell(r["evaluated_at"]),
        ])

    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    filename = f"tradingos_dry_runs_{today_str}.csv"

    response = Response(content=output.getvalue(), media_type="text/csv")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

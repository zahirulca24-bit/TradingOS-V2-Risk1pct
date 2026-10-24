"""TradingOS V2 Audited Trade Journal and PnL Reconciliation Engine.

Provides exchange-backed, de-duplicated PnL calculation, auditable trade journal aggregation,
proper entry-vs-exit correlation (without bare symbol-only matching), fee accounting,
and daily breaker reconciliation.
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union

from db_adapter import (
    init_db,
    get_demo_executions,
    get_closed_trades,
    reconcile_daily_risk_vs_closed_trades,
)
from closed_pnl_tracker import sync_and_persist_closed_trades


def correlate_executions_and_closed_trades(
    executions: List[Dict[str, Any]],
    closed_trades: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Correlate entry orders with exit closed trades without symbol-only matching.
    
    Guarantees:
    1. Zero double summation of closed PnL.
    2. No false attribution of open trades to previous closed trades.
    3. Proper multi-trade handling for the same symbol (e.g. multiple BTC trades).
    4. Proper partial exit aggregation.
    5. Clean accounting of gross PnL, fees, and net PnL.
    """
    matched_close_ids: Set[str] = set()
    execution_matches: Dict[Any, List[Dict[str, Any]]] = {
        ex.get("id"): [] for ex in executions
    }

    # Stage 1: Exact order_link_id matching (highest authority)
    for ex in executions:
        link_id = ex.get("order_link_id")
        if not link_id:
            continue
        for ct in closed_trades:
            cid = ct.get("close_id")
            if cid not in matched_close_ids and ct.get("order_link_id") == link_id:
                execution_matches[ex.get("id")].append(ct)
                matched_close_ids.add(cid)

    # Stage 2: Exact exchange order ID matching
    for ex in executions:
        entry_oid = ex.get("bybit_order_id")
        if not entry_oid:
            continue
        for ct in closed_trades:
            cid = ct.get("close_id")
            if cid not in matched_close_ids and ct.get("order_id") == entry_oid:
                execution_matches[ex.get("id")].append(ct)
                matched_close_ids.add(cid)

    # Stage 3: Temporal lifecycle matching for un-matched filled/accepted executions
    # (Matches earliest un-matched close for same symbol + correct exit direction on/after entry)
    for ex in executions:
        ex_id = ex.get("id")
        if execution_matches[ex_id]:
            continue
        status = ex.get("status", "").upper()
        if status not in ("FILLED", "ACCEPTED", "SUBMITTED", "PENDING"):
            continue

        ex_sym = ex.get("symbol")
        ex_dir = ex.get("direction", "").upper()
        expected_close_side = "Sell" if ex_dir == "LONG" else ("Buy" if ex_dir == "SHORT" else None)
        ex_time = ex.get("created_at") or ""

        candidates = []
        for ct in closed_trades:
            cid = ct.get("close_id")
            if cid in matched_close_ids:
                continue
            if ct.get("symbol") != ex_sym:
                continue
            if expected_close_side and ct.get("side") and ct.get("side") != expected_close_side:
                continue
            ct_time = ct.get("exchange_created_at") or ct.get("created_at") or ""
            if ex_time and ct_time and ct_time < ex_time:
                # Close happened before this entry existed; cannot be this trade
                continue
            candidates.append(ct)

        if candidates:
            # Sort by creation time ascending to match chronologically earliest
            candidates.sort(key=lambda c: c.get("exchange_created_at") or c.get("created_at") or "")
            matched_ct = candidates[0]
            execution_matches[ex_id].append(matched_ct)
            matched_close_ids.add(matched_ct["close_id"])

    # Stage 4: Build auditable records for executions
    merged: List[Dict[str, Any]] = []

    for ex in executions:
        ex_id = ex.get("id")
        matched = execution_matches[ex_id]

        if matched:
            matched.sort(key=lambda c: c.get("exchange_created_at") or c.get("created_at") or str(c.get("id") or ""))
            total_pnl = sum(float(c.get("closed_pnl") or 0.0) for c in matched)
            total_fee = sum(float(c.get("fee") or 0.0) for c in matched)
            net_pnl = total_pnl - total_fee
            last_exit_price = matched[-1].get("exit_price")
            total_closed_qty = sum(float(c.get("qty") or 0.0) for c in matched)
            entry_qty = float(ex.get("qty") or 0.0)

            is_partial = (total_closed_qty < (entry_qty - 1e-6)) if entry_qty > 0 else False

            if is_partial:
                trade_status = "PARTIALLY_CLOSED"
            elif total_pnl >= 0:
                trade_status = "CLOSED_WON"
            else:
                trade_status = "CLOSED_LOST"

            merged.append({
                "id": ex.get("id"),
                "cycle_id": ex.get("cycle_id"),
                "symbol": ex.get("symbol"),
                "direction": ex.get("direction"),
                "qty": ex.get("qty"),
                "entry_price": ex.get("entry_price"),
                "exit_price": last_exit_price,
                "sl": ex.get("sl"),
                "tp": ex.get("tp"),
                "rr": ex.get("rr"),
                "risk_budget_usd": ex.get("risk_budget_usd"),
                "bybit_order_id": ex.get("bybit_order_id"),
                "order_link_id": ex.get("order_link_id"),
                "status": trade_status,
                "closed_pnl": round(total_pnl, 4),
                "fee": round(total_fee, 4),
                "net_pnl": round(net_pnl, 4),
                "close_count": len(matched),
                "close_ids": [c["close_id"] for c in matched],
                "created_at": ex.get("created_at"),
            })
        else:
            current_status = "OPEN" if ex.get("status") == "FILLED" else ex.get("status", "PENDING")
            merged.append({
                "id": ex.get("id"),
                "cycle_id": ex.get("cycle_id"),
                "symbol": ex.get("symbol"),
                "direction": ex.get("direction"),
                "qty": ex.get("qty"),
                "entry_price": ex.get("entry_price"),
                "exit_price": None,
                "sl": ex.get("sl"),
                "tp": ex.get("tp"),
                "rr": ex.get("rr"),
                "risk_budget_usd": ex.get("risk_budget_usd"),
                "bybit_order_id": ex.get("bybit_order_id"),
                "order_link_id": ex.get("order_link_id"),
                "status": current_status,
                "closed_pnl": None,
                "fee": 0.0,
                "net_pnl": None,
                "close_count": 0,
                "close_ids": [],
                "created_at": ex.get("created_at"),
            })

    # Stage 5: Include any un-matched closed trades (e.g. from exchange sync or manual close)
    for ct in closed_trades:
        cid = ct.get("close_id")
        if cid not in matched_close_ids:
            pnl_val = float(ct.get("closed_pnl") or 0.0)
            fee_val = float(ct.get("fee") or 0.0)
            net_val = pnl_val - fee_val
            direction = "LONG" if ct.get("side") == "Sell" else "SHORT"

            merged.append({
                "id": cid,
                "cycle_id": "BYBIT_SYNC",
                "symbol": ct.get("symbol"),
                "direction": direction,
                "qty": ct.get("qty"),
                "entry_price": ct.get("entry_price"),
                "exit_price": ct.get("exit_price"),
                "sl": None,
                "tp": None,
                "rr": None,
                "risk_budget_usd": None,
                "bybit_order_id": ct.get("order_id"),
                "order_link_id": ct.get("order_link_id"),
                "status": "CLOSED_WON" if pnl_val >= 0 else "CLOSED_LOST",
                "closed_pnl": round(pnl_val, 4),
                "fee": round(fee_val, 4),
                "net_pnl": round(net_val, 4),
                "close_count": 1,
                "close_ids": [cid],
                "created_at": ct.get("exchange_created_at") or ct.get("created_at"),
            })
            matched_close_ids.add(cid)

    # Stage 6: Authoritative Summation Invariant
    # Realized PnL is mathematically derived strictly from the unique set of closed trades
    total_realized_pnl = sum(float(ct.get("closed_pnl") or 0.0) for ct in closed_trades)
    total_fees = sum(float(ct.get("fee") or 0.0) for ct in closed_trades)
    total_net_pnl = total_realized_pnl - total_fees

    wins = sum(1 for ct in closed_trades if float(ct.get("closed_pnl") or 0.0) >= 0)
    losses = sum(1 for ct in closed_trades if float(ct.get("closed_pnl") or 0.0) < 0)
    total_closed = len(closed_trades)
    win_rate = round((wins / total_closed) * 100, 1) if total_closed > 0 else 0.0

    return {
        "status": "success",
        "total_executions": len(merged),
        "total_closed": total_closed,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_realized_pnl": round(total_realized_pnl, 2),
        "gross_realized_pnl": round(total_realized_pnl, 2),
        "total_fees": round(total_fees, 2),
        "total_net_pnl": round(total_net_pnl, 2),
        "executions": merged,
    }


def build_audited_trade_journal(
    limit: int = 100,
    sync_exchange: bool = False,
    session: Optional[Any] = None,
    dsn: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Build fully correlated, audited trade journal from durable database records."""
    init_db(db_path=db_path, dsn=dsn)

    if sync_exchange:
        try:
            sync_and_persist_closed_trades(session=session, limit=limit, dsn=dsn, db_path=db_path)
        except Exception as exc:
            print(f"[TRADE_JOURNAL] Exchange sync skipped/failed: {exc}", flush=True)

    executions = get_demo_executions(limit=limit, dsn=dsn, db_path=db_path)
    closed_trades = get_closed_trades(limit=limit * 2, dsn=dsn, db_path=db_path)

    return correlate_executions_and_closed_trades(executions, closed_trades)


def audit_journal_reconciliation(
    trading_date: Optional[str] = None,
    dsn: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Perform authoritative reconciliation between daily breaker and audited closed trades."""
    return reconcile_daily_risk_vs_closed_trades(trading_date=trading_date, dsn=dsn, db_path=db_path)

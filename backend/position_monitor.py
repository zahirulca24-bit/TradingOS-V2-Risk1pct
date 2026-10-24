"""TradingOS V2 Distributed Position Lifecycle Monitor & Slot Reconciler.

Periodically reconciles exchange open positions, pending orders, SL/TP protection,
and database slot capacity independently of 15m candle scans.
Ensures distributed safety via exclusive position monitor lock (single authority per account).
"""

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pybit.unified_trading import HTTP

from db_adapter import (
    get_active_slot_reservations,
    reconcile_and_release_slot,
    activate_atomic_slot,
    update_order_intent_status,
    get_order_intent,
    get_db_connection,
)
from pipeline_lock import (
    exclusive_position_monitor_lock,
    PositionMonitorLockContentionError,
)
from reconciliation import (
    verify_position_protection,
    audit_all_positions_protection,
    reconcile_order_intent,
    reconcile_all_pending_intents,
)
from telegram_notifier import send_telegram_message


def get_monitor_bybit_session() -> Optional[HTTP]:
    """Initialize Bybit Demo session for position monitoring."""
    key = os.getenv("BYBIT_API_KEY", "").strip()
    secret = os.getenv("BYBIT_API_SECRET", "").strip()
    if not key or not secret:
        return None
    try:
        return HTTP(demo=True, api_key=key, api_secret=secret, recv_window=10000)
    except Exception as exc:
        print(f"[POSITION_MONITOR] Failed to initialize Bybit session: {exc}", flush=True)
        return None


def poll_and_reconcile_positions(
    session: Any = None,
    db_path: Optional[Union[str, Path]] = None,
    dsn: Optional[str] = None,
    alert_unprotected: bool = True,
) -> Dict[str, Any]:
    """Execute one bounded cycle of position monitoring and slot capacity reconciliation.
    
    Guarantees:
    1. Distributed safety: single worker execution per account via exclusive lock.
    2. Fail-closed: maintains conservative slots if exchange API is unreachable.
    3. Emergency alerts if an open position confirmed lacks Stop Loss protection.
    4. Durable slot release ONLY when verified closed, cancelled, or rejected.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        with exclusive_position_monitor_lock(db_path=db_path, dsn=dsn):
            if session is None:
                session = get_monitor_bybit_session()

            if session is None:
                return {
                    "status": "SESSION_UNAVAILABLE",
                    "timestamp": now_iso,
                    "conservative_slots": True,
                    "reason": "Bybit credentials unconfigured or session failed",
                }

            # 1. Fetch live open positions from Bybit
            raw_positions = []
            try:
                pos_resp = session.get_positions(category="linear", settleCoin="USDT")
                if not isinstance(pos_resp, dict) or pos_resp.get("retCode") != 0:
                    ret_code = pos_resp.get("retCode") if isinstance(pos_resp, dict) else -1
                    ret_msg = pos_resp.get("retMsg") if isinstance(pos_resp, dict) else str(pos_resp)
                    return {
                        "status": "EXCHANGE_UNAVAILABLE",
                        "timestamp": now_iso,
                        "conservative_slots": True,
                        "reason": f"get_positions returned error {ret_code}: {ret_msg}",
                    }
                raw_positions = pos_resp.get("result", {}).get("list") or []
            except Exception as exc:
                return {
                    "status": "EXCHANGE_UNAVAILABLE",
                    "timestamp": now_iso,
                    "conservative_slots": True,
                    "reason": f"Exception querying positions: {exc}",
                }

            # Filter active positions (size > 0)
            active_positions = [
                p for p in raw_positions
                if float(p.get("size") or 0.0) > 0
            ]
            open_position_symbols = {p.get("symbol") for p in active_positions if p.get("symbol")}

            # 2. Fetch live open orders from Bybit
            raw_orders = []
            try:
                ord_resp = session.get_open_orders(category="linear", settleCoin="USDT")
                if not isinstance(ord_resp, dict) or ord_resp.get("retCode") != 0:
                    ret_code = ord_resp.get("retCode") if isinstance(ord_resp, dict) else -1
                    ret_msg = ord_resp.get("retMsg") if isinstance(ord_resp, dict) else str(ord_resp)
                    return {
                        "status": "EXCHANGE_ORDERS_UNAVAILABLE",
                        "timestamp": now_iso,
                        "conservative_slots": True,
                        "reason": f"get_open_orders returned error {ret_code}: {ret_msg}",
                    }
                raw_orders = ord_resp.get("result", {}).get("list") or []
            except Exception as exc:
                return {
                    "status": "EXCHANGE_ORDERS_UNAVAILABLE",
                    "timestamp": now_iso,
                    "conservative_slots": True,
                    "reason": f"Exception querying open orders: {exc}",
                }

            open_order_symbols = {o.get("symbol") for o in raw_orders if o.get("symbol")}
            active_exchange_symbols = open_position_symbols | open_order_symbols

            # 3. Audit Stop Loss protection on all open positions
            prot_audit = audit_all_positions_protection(active_positions, session=session)
            if alert_unprotected and prot_audit.get("unprotected_count", 0) > 0:
                for unprot in prot_audit.get("unprotected_positions", []):
                    u_sym = unprot.get("symbol")
                    u_side = unprot.get("side")
                    u_entry = unprot.get("entry_price")
                    alert_msg = (
                        f"🚨 <b>EMERGENCY: UNPROTECTED OPEN POSITION DETECTED</b> 🚨\n\n"
                        f"Pair: #{u_sym} ({u_side})\n"
                        f"Entry: <code>~${u_entry:,.4f}</code>\n"
                        f"Issue: <b>NO verified Stop Loss protection active on exchange!</b>\n"
                        f"Action: New entries are strictly blocked."
                    )
                    send_telegram_message(alert_msg, parse_mode="HTML")

            # 4. Synchronize Database Slots against verified exchange state
            db_slots = get_active_slot_reservations(dsn=dsn, db_path=db_path)
            released_slots = []
            reconciled_slots = []

            for slot in db_slots:
                slot_symbol = slot.get("symbol")
                order_link_id = slot.get("order_link_id")
                slot_status = slot.get("status")

                if slot_symbol in active_exchange_symbols:
                    # Slot has live exposure or pending order on exchange
                    if slot_status in ("RESERVED", "HELD_PENDING_RECONCILIATION") and slot_symbol in open_position_symbols:
                        activate_atomic_slot(order_link_id, dsn=dsn, db_path=db_path)
                        update_order_intent_status(order_link_id, "ACTIVE", dsn=dsn, db_path=db_path)
                        reconciled_slots.append({"order_link_id": order_link_id, "action": "ACTIVATED"})
                    else:
                        reconciled_slots.append({"order_link_id": order_link_id, "action": "RETAINED"})
                else:
                    # Symbol has no active position AND no active open order on exchange
                    # Reconcile exact terminal lifecycle status
                    rec = reconcile_order_intent(order_link_id, session=session, db_path=db_path, dsn=dsn)
                    rec_status = rec.get("status")

                    if rec_status == "FILLED":
                        # Was filled, but no longer in open positions -> POSITION CLOSED!
                        reconcile_and_release_slot(
                            order_link_id,
                            "VERIFIED_CLOSED",
                            reason="Position closed on Bybit (SL/TP hit or manual exit)",
                            dsn=dsn,
                            db_path=db_path,
                        )
                        update_order_intent_status(order_link_id, "VERIFIED_CLOSED", dsn=dsn, db_path=db_path)
                        released_slots.append({"order_link_id": order_link_id, "reason": "VERIFIED_CLOSED"})

                    elif rec_status in ("VERIFIED_CANCELLED", "VERIFIED_REJECTED", "NEVER_SUBMITTED"):
                        # Already released by reconcile_order_intent
                        released_slots.append({"order_link_id": order_link_id, "reason": rec_status})

                    elif rec.get("uncertain"):
                        # Exchange uncertain: keep held
                        reconciled_slots.append({"order_link_id": order_link_id, "action": "HELD_UNCERTAIN"})
                    else:
                        # Any other unexpected state: treat as closed if order history confirms
                        reconcile_and_release_slot(
                            order_link_id,
                            "VERIFIED_CLOSED",
                            reason="Exposure terminated on exchange",
                            dsn=dsn,
                            db_path=db_path,
                        )
                        released_slots.append({"order_link_id": order_link_id, "reason": "VERIFIED_CLOSED"})

            # 5. Reconcile any orphaned/pending order intents
            reconcile_all_pending_intents(session=session, db_path=db_path, dsn=dsn)

            # Query remaining active slots after reconciliation
            remaining_active = get_active_slot_reservations(dsn=dsn, db_path=db_path)

            return {
                "status": "SUCCESS",
                "timestamp": now_iso,
                "conservative_slots": False,
                "open_positions_count": len(active_positions),
                "open_positions": active_positions,
                "open_orders_count": len(raw_orders),
                "protection_audit": prot_audit,
                "reconciled_slots": reconciled_slots,
                "released_slots": released_slots,
                "active_slots_remaining": len(remaining_active),
            }

    except PositionMonitorLockContentionError as lock_err:
        return {
            "status": "BUSY",
            "timestamp": now_iso,
            "conservative_slots": True,
            "reason": str(lock_err),
        }
    except Exception as exc:
        return {
            "status": "ERROR",
            "timestamp": now_iso,
            "conservative_slots": True,
            "reason": f"Unhandled monitor exception: {exc}",
        }


def run_position_monitor_iteration(
    session: Any = None,
    db_path: Optional[Union[str, Path]] = None,
    dsn: Optional[str] = None,
) -> Dict[str, Any]:
    """Helper for scheduled or API invocation of position monitor cycle."""
    return poll_and_reconcile_positions(session=session, db_path=db_path, dsn=dsn)

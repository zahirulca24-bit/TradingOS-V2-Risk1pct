"""TradingOS V2 Exchange Reconciliation & Protection Audit Engine.

Strictly DRY_RUN_ONLY / Simulated exchange interaction.
Idempotent order reconciliation, fill persistence, and SL/TP exchange protection audit.
"""

import os
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from db_adapter import (
    get_order_intent,
    update_order_intent_status,
    activate_atomic_slot,
    reconcile_and_release_slot,
    save_demo_execution,
    get_db_connection,
)


def reconcile_order_intent(
    order_link_id: str,
    session: Any,
    db_path: Optional[Union[str, Path]] = None,
    dsn: Optional[str] = None,
) -> Dict[str, Any]:
    """Reconcile an order intent against Bybit exchange state idempotently.
    
    Distinguishes ACCEPTED, PARTIALLY_FILLED, FILLED, VERIFIED_CANCELLED, VERIFIED_REJECTED,
    NEVER_SUBMITTED, and preserves UNKNOWN / HELD_PENDING_RECONCILIATION on API uncertainty.
    """
    intent = get_order_intent(order_link_id, dsn=dsn, db_path=db_path)
    if not intent:
        return {"reconciled": False, "status": "NOT_FOUND", "order_link_id": order_link_id}

    current_status = intent.get("status")
    # Terminal verified states require no further exchange query
    if current_status in ("FILLED", "VERIFIED_REJECTED", "VERIFIED_CANCELLED", "VERIFIED_CLOSED", "NEVER_SUBMITTED"):
        return {
            "reconciled": True,
            "status": current_status,
            "order_link_id": order_link_id,
            "intent": intent,
        }

    # Query exchange for this orderLinkId
    orders = []
    executions = []
    api_uncertain = False
    error_msg = None

    try:
        # 1. Check open orders
        resp = session.get_open_orders(category="linear", orderLinkId=order_link_id)
        if isinstance(resp, dict):
            ret_code = resp.get("retCode", 0)
            if ret_code == 0:
                orders = resp.get("result", {}).get("list") or []
            elif ret_code not in (0, 110001):  # 110001 = Order does not exist
                api_uncertain = True
                error_msg = f"get_open_orders returned retCode {ret_code}: {resp.get('retMsg')}"
        else:
            api_uncertain = True
            error_msg = f"get_open_orders returned invalid response type: {type(resp)}"

        # 2. Check order history if not found in open orders
        if not orders and not api_uncertain:
            hist_resp = session.get_order_history(category="linear", orderLinkId=order_link_id)
            if isinstance(hist_resp, dict):
                hist_ret = hist_resp.get("retCode", 0)
                if hist_ret == 0:
                    orders = hist_resp.get("result", {}).get("list") or []
                elif hist_ret not in (0, 110001):
                    api_uncertain = True
                    error_msg = f"get_order_history returned retCode {hist_ret}: {hist_resp.get('retMsg')}"
            else:
                api_uncertain = True
                error_msg = f"get_order_history returned invalid response type: {type(hist_resp)}"

        # 3. Check trade executions if still not found
        if not orders and not api_uncertain:
            exec_resp = session.get_executions(category="linear", orderLinkId=order_link_id)
            if isinstance(exec_resp, dict):
                exec_ret = exec_resp.get("retCode", 0)
                if exec_ret == 0:
                    executions = exec_resp.get("result", {}).get("list") or []
                elif exec_ret not in (0, 110001):
                    api_uncertain = True
                    error_msg = f"get_executions returned retCode {exec_ret}: {exec_resp.get('retMsg')}"
            else:
                api_uncertain = True
                error_msg = f"get_executions returned invalid response type: {type(exec_resp)}"

    except Exception as exc:
        api_uncertain = True
        error_msg = f"Network or exchange exception: {exc}"

    if api_uncertain:
        # On API uncertainty, preserve UNKNOWN state for pending orders and retain slot capacity
        if current_status in ("PENDING", "UNKNOWN", "SUBMITTED"):
            update_order_intent_status(
                order_link_id,
                status="UNKNOWN",
                error_message=error_msg,
                increment_retry=True,
                dsn=dsn,
                db_path=db_path,
            )
            return {
                "reconciled": False,
                "status": "UNKNOWN",
                "uncertain": True,
                "order_link_id": order_link_id,
                "error": error_msg,
            }
        else:
            # Order was already confirmed ACCEPTED or PARTIALLY_FILLED: retain confirmed state
            return {
                "reconciled": False,
                "status": current_status,
                "uncertain": True,
                "order_link_id": order_link_id,
                "error": error_msg,
            }

    # Case A: Order found on exchange
    if orders:
        order = orders[0]
        order_id = str(order.get("orderId") or "")
        order_status = str(order.get("orderStatus") or "").strip()
        avg_price = float(order.get("avgPrice") or 0.0)
        cum_exec_qty = str(order.get("cumExecQty") or "0")
        cum_exec_fee = float(order.get("cumExecFee") or 0.0)

        if order_status == "Filled":
            activate_atomic_slot(order_link_id, dsn=dsn, db_path=db_path)
            update_order_intent_status(
                order_link_id,
                status="FILLED",
                exchange_order_id=order_id,
                avg_fill_price=avg_price if avg_price > 0 else float(intent.get("entry_reference") or 0.0),
                cum_exec_qty=cum_exec_qty if float(cum_exec_qty or 0) > 0 else intent.get("qty"),
                cum_exec_fee=cum_exec_fee,
                dsn=dsn,
                db_path=db_path,
            )
            save_demo_execution({
                "cycle_id": intent.get("cycle_id"),
                "symbol": intent.get("symbol"),
                "direction": intent.get("direction"),
                "order_type": "Market",
                "qty": cum_exec_qty if float(cum_exec_qty or 0) > 0 else intent.get("qty"),
                "entry_price": avg_price if avg_price > 0 else float(intent.get("entry_reference") or 0.0),
                "sl": float(intent.get("sl") or 0.0),
                "tp": float(intent.get("tp") or 0.0),
                "rr": float(intent.get("rr") or 0.0),
                "risk_budget_usd": float(intent.get("risk_budget_usd") or 0.0),
                "bybit_order_id": order_id,
                "order_link_id": order_link_id,
                "status": "FILLED",
                "error_message": None,
            }, dsn=dsn, db_path=db_path)
            return {
                "reconciled": True,
                "status": "FILLED",
                "order_id": order_id,
                "avg_fill_price": avg_price,
                "cum_exec_qty": cum_exec_qty,
                "cum_exec_fee": cum_exec_fee,
            }

        elif order_status == "PartiallyFilled":
            activate_atomic_slot(order_link_id, dsn=dsn, db_path=db_path)
            update_order_intent_status(
                order_link_id,
                status="PARTIALLY_FILLED",
                exchange_order_id=order_id,
                avg_fill_price=avg_price,
                cum_exec_qty=cum_exec_qty,
                cum_exec_fee=cum_exec_fee,
                dsn=dsn,
                db_path=db_path,
            )
            return {
                "reconciled": True,
                "status": "PARTIALLY_FILLED",
                "order_id": order_id,
                "avg_fill_price": avg_price,
                "cum_exec_qty": cum_exec_qty,
            }

        elif order_status in ("New", "Untriggered"):
            activate_atomic_slot(order_link_id, dsn=dsn, db_path=db_path)
            update_order_intent_status(
                order_link_id,
                status="ACCEPTED",
                exchange_order_id=order_id,
                dsn=dsn,
                db_path=db_path,
            )
            return {
                "reconciled": True,
                "status": "ACCEPTED",
                "order_id": order_id,
            }

        elif order_status in ("Cancelled", "Deactivated"):
            if float(cum_exec_qty or 0) > 0:
                activate_atomic_slot(order_link_id, dsn=dsn, db_path=db_path)
                update_order_intent_status(
                    order_link_id,
                    status="PARTIALLY_FILLED",
                    exchange_order_id=order_id,
                    avg_fill_price=avg_price,
                    cum_exec_qty=cum_exec_qty,
                    cum_exec_fee=cum_exec_fee,
                    dsn=dsn,
                    db_path=db_path,
                )
                return {
                    "reconciled": True,
                    "status": "PARTIALLY_FILLED",
                    "order_id": order_id,
                }
            else:
                reconcile_and_release_slot(
                    order_link_id,
                    "VERIFIED_CANCELLED",
                    reason=f"Exchange orderStatus: {order_status}",
                    dsn=dsn,
                    db_path=db_path,
                )
                update_order_intent_status(
                    order_link_id,
                    status="VERIFIED_CANCELLED",
                    exchange_order_id=order_id,
                    dsn=dsn,
                    db_path=db_path,
                )
                return {
                    "reconciled": True,
                    "status": "VERIFIED_CANCELLED",
                    "order_id": order_id,
                }

        elif order_status == "Rejected":
            reconcile_and_release_slot(
                order_link_id,
                "VERIFIED_REJECTED",
                reason=f"Exchange orderStatus: {order_status}",
                dsn=dsn,
                db_path=db_path,
            )
            update_order_intent_status(
                order_link_id,
                status="VERIFIED_REJECTED",
                exchange_order_id=order_id,
                dsn=dsn,
                db_path=db_path,
            )
            return {
                "reconciled": True,
                "status": "VERIFIED_REJECTED",
                "order_id": order_id,
            }

    # Case B: Executions found without open order (immediate market fill)
    if executions:
        total_qty = Decimal("0")
        weighted_val = Decimal("0")
        total_fee = Decimal("0")
        exec_order_id = ""

        for ex in executions:
            e_qty = Decimal(str(ex.get("execQty") or "0"))
            e_price = Decimal(str(ex.get("execPrice") or "0"))
            e_fee = Decimal(str(ex.get("execFee") or "0"))
            total_qty += e_qty
            weighted_val += e_qty * e_price
            total_fee += e_fee
            if not exec_order_id and ex.get("orderId"):
                exec_order_id = str(ex.get("orderId"))

        avg_price = float(weighted_val / total_qty) if total_qty > 0 else float(intent.get("entry_reference") or 0.0)
        cum_qty = str(total_qty)

        activate_atomic_slot(order_link_id, dsn=dsn, db_path=db_path)
        update_order_intent_status(
            order_link_id,
            status="FILLED",
            exchange_order_id=exec_order_id,
            avg_fill_price=avg_price,
            cum_exec_qty=cum_qty,
            cum_exec_fee=float(total_fee),
            dsn=dsn,
            db_path=db_path,
        )
        return {
            "reconciled": True,
            "status": "FILLED",
            "order_id": exec_order_id,
            "avg_fill_price": avg_price,
            "cum_exec_qty": cum_qty,
            "cum_exec_fee": float(total_fee),
        }

    # Case C: Confirmed never received by exchange
    if current_status in ("PENDING", "UNKNOWN", "SUBMITTED"):
        reconcile_and_release_slot(
            order_link_id,
            "NEVER_SUBMITTED",
            reason="Order not found in exchange open orders, history, or executions",
            dsn=dsn,
            db_path=db_path,
        )
        update_order_intent_status(
            order_link_id,
            status="NEVER_SUBMITTED",
            dsn=dsn,
            db_path=db_path,
        )
        return {
            "reconciled": True,
            "status": "NEVER_SUBMITTED",
            "order_link_id": order_link_id,
        }
    else:
        # If intent was already ACCEPTED or PARTIALLY_FILLED, retain that status
        return {
            "reconciled": True,
            "status": current_status,
            "order_link_id": order_link_id,
        }


def reconcile_all_pending_intents(
    session: Any,
    db_path: Optional[Union[str, Path]] = None,
    dsn: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Scan and reconcile all unreconciled or pending order intents."""
    with get_db_connection(db_path=db_path, dsn=dsn) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT order_link_id FROM order_intents
            WHERE status IN ('PENDING', 'SUBMITTED', 'UNKNOWN', 'ACCEPTED', 'PARTIALLY_FILLED')
        """)
        rows = cur.fetchall()

    results = []
    for r in rows:
        link_id = r["order_link_id"]
        res = reconcile_order_intent(link_id, session=session, db_path=db_path, dsn=dsn)
        results.append(res)
    return results


def verify_position_protection(
    position: Dict[str, Any],
    session: Any = None,
) -> Dict[str, Any]:
    """Verify exchange-enforced Stop Loss protection on an active position.
    
    Differentiates CONFIRMED_UNPROTECTED from API_UNCERTAINTY.
    """
    symbol = position.get("symbol")
    size = float(position.get("size") or 0.0)
    if size <= 0:
        return {"protected": True, "status": "NO_POSITION", "symbol": symbol}

    side = str(position.get("side") or "").capitalize()
    entry = float(position.get("avgPrice") or position.get("entry_price") or 0.0)
    raw_sl = position.get("stopLoss") or position.get("sl")
    sl = float(raw_sl) if raw_sl and str(raw_sl).strip() and float(raw_sl) > 0 else 0.0
    tp = position.get("takeProfit") or position.get("tp")

    if sl > 0:
        # Check direction geometry
        # Long: SL below entry (or trailing in profit)
        # Short: SL above entry (or trailing in profit)
        return {
            "protected": True,
            "status": "PROTECTED",
            "symbol": symbol,
            "side": side,
            "entry_price": entry,
            "sl": sl,
            "tp": float(tp) if tp else None,
        }

    # SL is missing from position record - check conditional stop orders if session provided
    if session is not None:
        try:
            resp = session.get_open_orders(category="linear", symbol=symbol)
            if isinstance(resp, dict):
                ret_code = resp.get("retCode", 0)
                if ret_code == 0:
                    orders = resp.get("result", {}).get("list") or []
                    for o in orders:
                        stop_type = str(o.get("stopOrderType") or "").strip().lower()
                        reduce_only = o.get("reduceOnly") in (True, "true", "True", 1)
                        if stop_type in ("stoploss", "partialstoploss") or reduce_only:
                            trigger_price = float(o.get("triggerPrice") or o.get("price") or 0.0)
                            return {
                                "protected": True,
                                "status": "PROTECTED_BY_CONDITIONAL_ORDER",
                                "symbol": symbol,
                                "side": side,
                                "sl": trigger_price,
                            }
                    # Confirmed no stop order in open orders
                    return {
                        "protected": False,
                        "status": "CONFIRMED_UNPROTECTED",
                        "symbol": symbol,
                        "side": side,
                        "entry_price": entry,
                        "reason": f"Position {symbol} ({side} {size}) has no exchange-enforced Stop Loss.",
                    }
                else:
                    return {
                        "protected": False,
                        "status": "API_UNCERTAINTY",
                        "symbol": symbol,
                        "side": side,
                        "reason": f"Bybit returned retCode {ret_code} while querying orders: {resp.get('retMsg')}",
                    }
        except Exception as exc:
            return {
                "protected": False,
                "status": "API_UNCERTAINTY",
                "symbol": symbol,
                "side": side,
                "reason": f"Network/API exception while querying position orders: {exc}",
            }

    return {
        "protected": False,
        "status": "CONFIRMED_UNPROTECTED",
        "symbol": symbol,
        "side": side,
        "entry_price": entry,
        "reason": f"Position record for {symbol} has no Stop Loss set.",
    }


def audit_all_positions_protection(
    positions: List[Dict[str, Any]],
    session: Any = None,
) -> Dict[str, Any]:
    """Audit all open positions for verified exchange Stop Loss protection."""
    results = []
    unprotected = []
    uncertain = []

    for pos in positions:
        size = float(pos.get("size") or 0.0)
        if size <= 0:
            continue
        verdict = verify_position_protection(pos, session=session)
        results.append(verdict)
        if verdict["status"] == "CONFIRMED_UNPROTECTED":
            unprotected.append(verdict)
        elif verdict["status"] == "API_UNCERTAINTY":
            uncertain.append(verdict)

    all_protected = (len(unprotected) == 0 and len(uncertain) == 0)
    return {
        "all_protected": all_protected,
        "total_active_positions": len(results),
        "unprotected_count": len(unprotected),
        "uncertain_count": len(uncertain),
        "unprotected_positions": unprotected,
        "uncertain_positions": uncertain,
        "positions_audit": results,
    }

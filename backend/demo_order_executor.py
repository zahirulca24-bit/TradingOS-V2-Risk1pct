"""TradingOS V2 Bybit Demo Order Execution Engine.
Automated execution of PLAN_READY signals on Bybit Demo (linear USDT perpetual).
Strict 1.0% equity risk budget enforcement, SL/TP attached, 3-slot maximum.
Strict idempotency, exchange reconciliation, and no duplicate submissions.
"""

import os
import time
import math
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pybit.unified_trading import HTTP

from demo_dashboard_api import read_demo_account, response_result
from demo_risk_engine import (
    get_cached_instrument_specs,
    dec,
    authorize_execution_order,
    is_demo_execution_enabled,
    quantize_value,
    format_step_string,
)
from db_adapter import (
    save_demo_execution,
    get_demo_executions,
    reserve_atomic_slot,
    activate_atomic_slot,
    reconcile_and_release_slot,
    create_order_intent,
    update_order_intent_status,
    get_order_intent,
)
from reconciliation import reconcile_order_intent
from telegram_notifier import send_telegram_message
from pipeline_contract import MIN_RR


ENV_FILE = Path(__file__).resolve().parent / ".env"


def execute_demo_plans(
    reports: Dict[str, Any],
    session: Any = None,
    account: Optional[Dict[str, Any]] = None,
    db_path: Optional[Union[str, Path]] = None,
    dsn: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Evaluates committed trade plans and submits Bybit Demo Market orders for PLAN_READY setups."""
    if not is_demo_execution_enabled():
        print("[DEMO_EXECUTOR] Order execution is disabled by configuration.", flush=True)
        return []

    plans_report = reports.get("trade_plans", {})
    ready_plans = [
        p for p in plans_report.get("results", [])
        if p.get("status") == "PLAN_READY" and float(p.get("rr") or 0.0) >= MIN_RR
    ]

    if not ready_plans:
        print("[DEMO_EXECUTOR] No PLAN_READY setups to execute in current cycle.", flush=True)
        return []

    cycle_id = reports.get("scanner", {}).get("cycle_id", "UNKNOWN")

    # Fetch live demo account balance and positions if not injected
    if account is None:
        try:
            account = read_demo_account()
        except Exception as exc:
            print(f"[DEMO_EXECUTOR] Failed to read demo account: {exc}", flush=True)
            return []

    if session is None:
        key = os.getenv("BYBIT_API_KEY", "").strip()
        secret = os.getenv("BYBIT_API_SECRET", "").strip()
        if not key or not secret:
            print("[DEMO_EXECUTOR] Missing Bybit API credentials.", flush=True)
            return []
        session = HTTP(demo=True, api_key=key, api_secret=secret)

    executions = []

    for plan in ready_plans:
        symbol = plan.get("symbol")
        direction = plan.get("direction", "LONG").upper()
        entry_ref = plan.get("entry_reference", 0.0)

        # Unique idempotency orderLinkId (strictly alphanumeric with underscores)
        clean_cycle = cycle_id.replace("T", "").replace("Z", "").replace(":", "").replace("-", "")
        order_link_id = f"tos_{clean_cycle[-8:]}_{symbol}"

        # 0. Check if order intent already exists (Strict Idempotency Guard)
        existing_intent = get_order_intent(order_link_id, dsn=dsn, db_path=db_path)
        if existing_intent:
            e_status = existing_intent.get("status")
            if e_status in ("ACCEPTED", "FILLED", "PARTIALLY_FILLED"):
                print(f"[DEMO_EXECUTOR] Intent {order_link_id} already exists as {e_status}. Skipping duplicate.", flush=True)
                rec = reconcile_order_intent(order_link_id, session=session, db_path=db_path, dsn=dsn)
                exec_record = {
                    "cycle_id": cycle_id,
                    "symbol": symbol,
                    "direction": direction,
                    "order_type": "Market",
                    "qty": existing_intent.get("qty"),
                    "entry_price": float(rec.get("avg_fill_price") or existing_intent.get("entry_reference") or 0.0),
                    "sl": float(existing_intent.get("sl") or 0.0),
                    "tp": float(existing_intent.get("tp") or 0.0),
                    "rr": float(existing_intent.get("rr") or 0.0),
                    "risk_budget_usd": float(existing_intent.get("risk_budget_usd") or 0.0),
                    "bybit_order_id": rec.get("order_id") or existing_intent.get("exchange_order_id"),
                    "order_link_id": order_link_id,
                    "status": rec.get("status", e_status),
                    "error_message": "Duplicate skipped via idempotency check",
                }
                executions.append(exec_record)
                continue
            elif e_status in ("UNKNOWN", "PENDING"):
                # Prior crash or timeout - reconcile against exchange before any retry!
                rec = reconcile_order_intent(order_link_id, session=session, db_path=db_path, dsn=dsn)
                if rec.get("status") in ("ACCEPTED", "FILLED", "PARTIALLY_FILLED"):
                    print(f"[DEMO_EXECUTOR] Intent {order_link_id} reconciled as {rec.get('status')}. Skipping duplicate.", flush=True)
                    exec_record = {
                        "cycle_id": cycle_id,
                        "symbol": symbol,
                        "direction": direction,
                        "order_type": "Market",
                        "qty": existing_intent.get("qty"),
                        "entry_price": float(rec.get("avg_fill_price") or existing_intent.get("entry_reference") or 0.0),
                        "sl": float(existing_intent.get("sl") or 0.0),
                        "tp": float(existing_intent.get("tp") or 0.0),
                        "rr": float(existing_intent.get("rr") or 0.0),
                        "risk_budget_usd": float(existing_intent.get("risk_budget_usd") or 0.0),
                        "bybit_order_id": rec.get("order_id") or existing_intent.get("exchange_order_id"),
                        "order_link_id": order_link_id,
                        "status": rec.get("status"),
                        "error_message": "Recovered from prior unknown state via exchange reconciliation",
                    }
                    executions.append(exec_record)
                    continue

        # 1. Authorize execution order through the authoritative gate
        auth = authorize_execution_order(
            plan,
            account,
            specs=None,
            db_path=db_path,
            dsn=dsn,
        )

        if not auth.get("authorized"):
            reasons = auth.get("reasons", [])
            print(f"[DEMO_EXECUTOR] Execution blocked for {symbol}: {reasons}", flush=True)
            exec_record = {
                "cycle_id": cycle_id,
                "symbol": symbol,
                "direction": direction,
                "order_type": "Market",
                "qty": "0",
                "entry_price": float(entry_ref),
                "sl": float(plan.get("sl", 0.0)),
                "tp": float(plan.get("tp", 0.0)),
                "rr": float(plan.get("rr", 0.0)),
                "risk_budget_usd": float(auth.get("risk_budget_usd") or 0.0),
                "bybit_order_id": None,
                "order_link_id": order_link_id,
                "status": "BLOCKED",
                "error_message": "; ".join(reasons),
            }
            save_demo_execution(exec_record, dsn=dsn, db_path=db_path)
            executions.append(exec_record)
            continue

        qty_str = str(auth.get("quantity"))
        sl_str = str(auth.get("quantized_sl"))
        tp_str = str(auth.get("quantized_tp"))
        risk_budget_usd = float(auth.get("risk_budget_usd") or 0.0)

        # 2. Reserve atomic capacity slot BEFORE exchange submission
        slot_id = reserve_atomic_slot(
            symbol,
            order_link_id,
            ttl_seconds=60,
            max_slots=3,
            dsn=dsn,
            db_path=db_path,
        )
        if slot_id is None:
            print(f"[DEMO_EXECUTOR] Slot reservation failed for {symbol}: capacity reached.", flush=True)
            exec_record = {
                "cycle_id": cycle_id,
                "symbol": symbol,
                "direction": direction,
                "order_type": "Market",
                "qty": qty_str,
                "entry_price": float(entry_ref),
                "sl": float(sl_str),
                "tp": float(tp_str),
                "rr": float(plan.get("rr", 0.0)),
                "risk_budget_usd": risk_budget_usd,
                "bybit_order_id": None,
                "order_link_id": order_link_id,
                "status": "BLOCKED",
                "error_message": "Slot reservation failed: maximum capacity occupied",
            }
            save_demo_execution(exec_record, dsn=dsn, db_path=db_path)
            executions.append(exec_record)
            continue

        # 3. Create persistent order intent BEFORE exchange submission
        intent_data = {
            "order_link_id": order_link_id,
            "cycle_id": cycle_id,
            "symbol": symbol,
            "direction": direction,
            "qty": qty_str,
            "entry_reference": float(entry_ref),
            "sl": float(sl_str),
            "tp": float(tp_str),
            "rr": float(plan.get("rr", 0.0)),
            "risk_budget_usd": risk_budget_usd,
            "status": "PENDING",
        }
        create_order_intent(intent_data, dsn=dsn, db_path=db_path)

        # 4. Submit Bybit V5 Market Order
        side = "Buy" if direction == "LONG" else "Sell"
        order_params = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": qty_str,
            "takeProfit": tp_str,
            "stopLoss": sl_str,
            "tpOrderType": "Market",
            "slOrderType": "Market",
            "tpslMode": "Full",
            "timeInForce": "GTC",
            "positionIdx": 0,
            "orderLinkId": order_link_id,
        }

        try:
            resp = session.place_order(**order_params)
            ret_code = resp.get("retCode", -1)
            ret_msg = resp.get("retMsg", "Unknown response")
            result_data = resp.get("result", {})
            bybit_order_id = result_data.get("orderId", "")

            if ret_code == 0:
                activate_atomic_slot(order_link_id, dsn=dsn, db_path=db_path)
                update_order_intent_status(
                    order_link_id,
                    "ACCEPTED",
                    exchange_order_id=bybit_order_id,
                    dsn=dsn,
                    db_path=db_path,
                )

                # Query exchange state to capture immediate fill if available
                rec = reconcile_order_intent(order_link_id, session=session, db_path=db_path, dsn=dsn)
                final_status = rec.get("status") if rec.get("reconciled") and rec.get("status") in ("FILLED", "ACCEPTED") else "ACCEPTED"
                fill_price = float(rec.get("avg_fill_price") or entry_ref)

                exec_record = {
                    "cycle_id": cycle_id,
                    "symbol": symbol,
                    "direction": direction,
                    "order_type": "Market",
                    "qty": qty_str,
                    "entry_price": fill_price,
                    "sl": float(sl_str),
                    "tp": float(tp_str),
                    "rr": float(plan.get("rr", 0.0)),
                    "risk_budget_usd": risk_budget_usd,
                    "bybit_order_id": bybit_order_id,
                    "order_link_id": order_link_id,
                    "status": final_status,
                    "error_message": None,
                }
                save_demo_execution(exec_record, dsn=dsn, db_path=db_path)
                executions.append(exec_record)

                icon = "🟢" if direction == "LONG" else "🔴"
                tg_msg = (
                    f"🚀 <b>BYBIT DEMO ORDER {final_status}</b> 🚀\n\n"
                    f"🪙 <b>Pair:</b> #{symbol} ({direction} {icon})\n"
                    f"⚡ <b>Order Type:</b> Market Order (15m Candle Reversal)\n"
                    f"📦 <b>Quantity:</b> <code>{qty_str}</code>\n"
                    f"📍 <b>Entry Price:</b> <code>~${fill_price:,.4f}</code>\n"
                    f"🛑 <b>Stop Loss:</b> <code>${float(sl_str):,.4f}</code>\n"
                    f"🎯 <b>Take Profit:</b> <code>${float(tp_str):,.4f}</code>\n"
                    f"⚖️ <b>Risk / Reward:</b> <code>1 : {float(plan.get('rr', 0.0)):.2f}</code>\n"
                    f"🛡️ <b>Risk Budget:</b> <code>${risk_budget_usd:.2f} USD (1.0% Equity)</code>\n"
                    f"🆔 <b>Bybit Order ID:</b> <code>{bybit_order_id}</code>\n\n"
                    f"🌐 <b>Account:</b> Bybit Demo Account (Verified)"
                )
                send_telegram_message(tg_msg, parse_mode="HTML")
            else:
                reconcile_and_release_slot(
                    order_link_id,
                    "VERIFIED_REJECTED",
                    reason=f"{ret_code}: {ret_msg}",
                    dsn=dsn,
                    db_path=db_path,
                )
                update_order_intent_status(
                    order_link_id,
                    "VERIFIED_REJECTED",
                    error_message=f"{ret_code}: {ret_msg}",
                    dsn=dsn,
                    db_path=db_path,
                )
                exec_record = {
                    "cycle_id": cycle_id,
                    "symbol": symbol,
                    "direction": direction,
                    "order_type": "Market",
                    "qty": qty_str,
                    "entry_price": float(entry_ref),
                    "sl": float(sl_str),
                    "tp": float(tp_str),
                    "rr": float(plan.get("rr", 0.0)),
                    "risk_budget_usd": risk_budget_usd,
                    "bybit_order_id": bybit_order_id,
                    "order_link_id": order_link_id,
                    "status": "REJECTED",
                    "error_message": f"{ret_code}: {ret_msg}",
                }
                save_demo_execution(exec_record, dsn=dsn, db_path=db_path)
                executions.append(exec_record)

                tg_fail = (
                    f"❌ <b>BYBIT DEMO ORDER REJECTED</b> ❌\n\n"
                    f"Pair: #{symbol} ({direction})\n"
                    f"Reason: <code>{ret_msg} (code {ret_code})</code>"
                )
                send_telegram_message(tg_fail, parse_mode="HTML")

        except Exception as exc:
            # On network exception/timeout: Attempt immediate reconciliation before setting UNKNOWN
            rec = None
            try:
                rec = reconcile_order_intent(order_link_id, session=session, db_path=db_path, dsn=dsn)
            except Exception:
                pass

            if rec and rec.get("status") in ("ACCEPTED", "FILLED", "PARTIALLY_FILLED"):
                exec_record = {
                    "cycle_id": cycle_id,
                    "symbol": symbol,
                    "direction": direction,
                    "order_type": "Market",
                    "qty": qty_str,
                    "entry_price": float(rec.get("avg_fill_price") or entry_ref),
                    "sl": float(sl_str),
                    "tp": float(tp_str),
                    "rr": float(plan.get("rr", 0.0)),
                    "risk_budget_usd": risk_budget_usd,
                    "bybit_order_id": rec.get("order_id"),
                    "order_link_id": order_link_id,
                    "status": rec.get("status"),
                    "error_message": f"Recovered via reconciliation after timeout: {str(exc)[:100]}",
                }
                save_demo_execution(exec_record, dsn=dsn, db_path=db_path)
                executions.append(exec_record)
            elif rec and rec.get("status") == "NEVER_SUBMITTED":
                exec_record = {
                    "cycle_id": cycle_id,
                    "symbol": symbol,
                    "direction": direction,
                    "order_type": "Market",
                    "qty": qty_str,
                    "entry_price": float(entry_ref),
                    "sl": float(sl_str),
                    "tp": float(tp_str),
                    "rr": float(plan.get("rr", 0.0)),
                    "risk_budget_usd": risk_budget_usd,
                    "bybit_order_id": None,
                    "order_link_id": order_link_id,
                    "status": "NEVER_SUBMITTED",
                    "error_message": f"Confirmed never submitted after error: {str(exc)[:100]}",
                }
                save_demo_execution(exec_record, dsn=dsn, db_path=db_path)
                executions.append(exec_record)
            else:
                update_order_intent_status(
                    order_link_id,
                    "UNKNOWN",
                    error_message=str(exc)[:250],
                    increment_retry=True,
                    dsn=dsn,
                    db_path=db_path,
                )
                exec_record = {
                    "cycle_id": cycle_id,
                    "symbol": symbol,
                    "direction": direction,
                    "order_type": "Market",
                    "qty": qty_str,
                    "entry_price": float(entry_ref),
                    "sl": float(sl_str),
                    "tp": float(tp_str),
                    "rr": float(plan.get("rr", 0.0)),
                    "risk_budget_usd": risk_budget_usd,
                    "bybit_order_id": None,
                    "order_link_id": order_link_id,
                    "status": "UNKNOWN",
                    "error_message": f"Network/API exception (slot retained): {str(exc)[:250]}",
                }
                save_demo_execution(exec_record, dsn=dsn, db_path=db_path)
                executions.append(exec_record)

    return executions


def place_single_demo_order(
    symbol: str,
    direction: str,
    entry_reference: Optional[float] = None,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
    rr: Optional[float] = None,
    source: str = "MANUAL_TERMINAL",
    session: Any = None,
    account: Optional[Dict[str, Any]] = None,
    db_path: Optional[Union[str, Path]] = None,
    dsn: Optional[str] = None,
) -> Dict[str, Any]:
    """Places a single 1% risk market order on Bybit Demo via the authoritative risk gate."""
    symbol = symbol.strip().upper()
    direction = direction.strip().upper()
    if direction not in ("LONG", "SHORT"):
        raise ValueError("Direction must be LONG or SHORT")

    if account is None:
        account = read_demo_account()

    specs = get_cached_instrument_specs(symbol)
    if not specs:
        raise RuntimeError(f"Could not load instrument specs for {symbol} from Bybit")

    if not entry_reference or entry_reference <= 0:
        from universe_candles import fetch_klines
        klines = fetch_klines(symbol, "15", limit=1)
        if not klines:
            raise RuntimeError(f"Cannot fetch current market price for {symbol}")
        entry_reference = float(klines[-1]["close"])

    d_entry = Decimal(str(entry_reference))
    if not sl or sl <= 0:
        sl_pct = Decimal("0.012")
        d_sl = d_entry * (Decimal("1") - sl_pct) if direction == "LONG" else d_entry * (Decimal("1") + sl_pct)
        sl = float(d_sl)
    else:
        d_sl = Decimal(str(sl))

    if not tp or tp <= 0:
        tp_pct = Decimal("0.036")
        d_tp = d_entry * (Decimal("1") + tp_pct) if direction == "LONG" else d_entry * (Decimal("1") - tp_pct)
        tp = float(d_tp)
    else:
        d_tp = Decimal(str(tp))

    calc_rr = rr if (rr and rr > 0) else round(float(abs(d_tp - d_entry) / abs(d_entry - d_sl)), 2)

    plan = {
        "symbol": symbol,
        "direction": direction,
        "entry_reference": entry_reference,
        "sl": sl,
        "tp": tp,
        "rr": calc_rr,
    }

    # Authorize through authoritative execution gate
    auth = authorize_execution_order(
        plan,
        account,
        specs=specs,
        db_path=db_path,
        dsn=dsn,
    )
    if not auth.get("authorized"):
        reasons_str = "; ".join(auth.get("reasons", []))
        raise RuntimeError(f"Order authorization failed [{auth.get('status')}]: {reasons_str}")

    qty_str = str(auth.get("quantity"))
    sl_str = str(auth.get("quantized_sl"))
    tp_str = str(auth.get("quantized_tp"))
    risk_budget_usd = float(auth.get("risk_budget_usd") or 0.0)

    ts_now = int(time.time())
    order_link_id = f"term_{ts_now}_{symbol}"
    cycle_id = f"MANUAL_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    # Check existing order intent idempotency
    existing_intent = get_order_intent(order_link_id, dsn=dsn, db_path=db_path)
    if existing_intent and existing_intent.get("status") in ("ACCEPTED", "FILLED", "PARTIALLY_FILLED"):
        raise RuntimeError(f"Order {order_link_id} has already been submitted (status: {existing_intent.get('status')})")

    # Reserve atomic capacity slot BEFORE submission
    slot_id = reserve_atomic_slot(
        symbol,
        order_link_id,
        ttl_seconds=60,
        max_slots=3,
        dsn=dsn,
        db_path=db_path,
    )
    if slot_id is None:
        raise RuntimeError("Risk capacity reached: 3 slots already occupied or reserved")

    # Create persistent order intent BEFORE submission
    intent_data = {
        "order_link_id": order_link_id,
        "cycle_id": cycle_id,
        "symbol": symbol,
        "direction": direction,
        "qty": qty_str,
        "entry_reference": entry_reference,
        "sl": float(sl_str),
        "tp": float(tp_str),
        "rr": calc_rr,
        "risk_budget_usd": risk_budget_usd,
        "status": "PENDING",
    }
    create_order_intent(intent_data, dsn=dsn, db_path=db_path)

    side = "Buy" if direction == "LONG" else "Sell"
    if session is None:
        key = os.getenv("BYBIT_API_KEY", "").strip()
        secret = os.getenv("BYBIT_API_SECRET", "").strip()
        session = HTTP(demo=True, api_key=key, api_secret=secret)

    order_params = {
        "category": "linear",
        "symbol": symbol,
        "side": side,
        "orderType": "Market",
        "qty": qty_str,
        "takeProfit": tp_str,
        "stopLoss": sl_str,
        "tpOrderType": "Market",
        "slOrderType": "Market",
        "tpslMode": "Full",
        "timeInForce": "GTC",
        "positionIdx": 0,
        "orderLinkId": order_link_id,
    }

    try:
        resp = session.place_order(**order_params)
        ret_code = resp.get("retCode", -1)
        ret_msg = resp.get("retMsg", "Unknown response")
        result_data = resp.get("result", {})
        bybit_order_id = result_data.get("orderId", "")

        if ret_code == 0:
            activate_atomic_slot(order_link_id, dsn=dsn, db_path=db_path)
            update_order_intent_status(
                order_link_id,
                "ACCEPTED",
                exchange_order_id=bybit_order_id,
                dsn=dsn,
                db_path=db_path,
            )

            # Reconcile fill details if immediately matched
            rec = reconcile_order_intent(order_link_id, session=session, db_path=db_path, dsn=dsn)
            final_status = rec.get("status") if rec.get("reconciled") and rec.get("status") in ("FILLED", "ACCEPTED") else "ACCEPTED"
            fill_price = float(rec.get("avg_fill_price") or d_entry)

            exec_record = {
                "cycle_id": cycle_id,
                "symbol": symbol,
                "direction": direction,
                "order_type": "Market",
                "qty": qty_str,
                "entry_price": fill_price,
                "sl": float(sl_str),
                "tp": float(tp_str),
                "rr": calc_rr,
                "risk_budget_usd": risk_budget_usd,
                "bybit_order_id": bybit_order_id,
                "order_link_id": order_link_id,
                "status": final_status,
                "error_message": None,
            }
            save_demo_execution(exec_record, dsn=dsn, db_path=db_path)

            icon = "🟢" if direction == "LONG" else "🔴"
            tg_msg = (
                f"⚡ <b>TERMINAL 1-CLICK DEMO ORDER {final_status}</b> ⚡\n\n"
                f"🪙 <b>Pair:</b> #{symbol} ({direction} {icon})\n"
                f"⚡ <b>Order Type:</b> Market Order (Interactive Terminal)\n"
                f"📦 <b>Quantity:</b> <code>{qty_str}</code>\n"
                f"📍 <b>Entry Price:</b> <code>~${fill_price:,.4f}</code>\n"
                f"🛑 <b>Stop Loss:</b> <code>${float(sl_str):,.4f}</code>\n"
                f"🎯 <b>Take Profit:</b> <code>${float(tp_str):,.4f}</code>\n"
                f"⚖️ <b>Risk / Reward:</b> <code>1 : {calc_rr:.2f}</code>\n"
                f"🛡️ <b>Risk Budget:</b> <code>${risk_budget_usd:.2f} USD (1.0% Equity)</code>\n"
                f"🆔 <b>Bybit Order ID:</b> <code>{bybit_order_id}</code>\n\n"
                f"🌐 <b>Account:</b> Bybit Demo Account"
            )
            send_telegram_message(tg_msg, parse_mode="HTML")

            return {
                "status": "success",
                "symbol": symbol,
                "direction": direction,
                "order_id": bybit_order_id,
                "order_link_id": order_link_id,
                "qty": qty_str,
                "entry": fill_price,
                "sl": float(sl_str),
                "tp": float(tp_str),
                "rr": calc_rr,
                "risk_usd": risk_budget_usd,
                "message": f"Successfully placed {direction} market order for {qty_str} {symbol} on Bybit Demo!",
            }
        else:
            reconcile_and_release_slot(order_link_id, "VERIFIED_REJECTED", reason=f"{ret_code}: {ret_msg}", dsn=dsn, db_path=db_path)
            update_order_intent_status(order_link_id, "VERIFIED_REJECTED", error_message=f"{ret_code}: {ret_msg}", dsn=dsn, db_path=db_path)
            exec_record = {
                "cycle_id": cycle_id,
                "symbol": symbol,
                "direction": direction,
                "order_type": "Market",
                "qty": qty_str,
                "entry_price": float(d_entry),
                "sl": float(sl_str),
                "tp": float(tp_str),
                "rr": calc_rr,
                "risk_budget_usd": risk_budget_usd,
                "bybit_order_id": bybit_order_id,
                "order_link_id": order_link_id,
                "status": "REJECTED",
                "error_message": f"{ret_code}: {ret_msg}",
            }
            save_demo_execution(exec_record, dsn=dsn, db_path=db_path)
            raise RuntimeError(f"Bybit rejected order: {ret_msg} (code {ret_code})")

    except Exception as exc:
        if "reconcile_and_release_slot" not in str(exc) and "Bybit rejected order" not in str(exc):
            rec = None
            try:
                rec = reconcile_order_intent(order_link_id, session=session, db_path=db_path, dsn=dsn)
            except Exception:
                pass

            if rec and rec.get("status") in ("ACCEPTED", "FILLED", "PARTIALLY_FILLED"):
                return {
                    "status": "success",
                    "symbol": symbol,
                    "direction": direction,
                    "order_id": rec.get("order_id"),
                    "order_link_id": order_link_id,
                    "qty": qty_str,
                    "entry": float(rec.get("avg_fill_price") or d_entry),
                    "sl": float(sl_str),
                    "tp": float(tp_str),
                    "rr": calc_rr,
                    "risk_usd": risk_budget_usd,
                    "message": "Order recovered via reconciliation after timeout",
                }
            elif rec and rec.get("status") == "NEVER_SUBMITTED":
                pass
            else:
                update_order_intent_status(
                    order_link_id,
                    "UNKNOWN",
                    error_message=str(exc)[:250],
                    increment_retry=True,
                    dsn=dsn,
                    db_path=db_path,
                )
                exec_record = {
                    "cycle_id": cycle_id,
                    "symbol": symbol,
                    "direction": direction,
                    "order_type": "Market",
                    "qty": qty_str,
                    "entry_price": float(d_entry),
                    "sl": float(sl_str),
                    "tp": float(tp_str),
                    "rr": calc_rr,
                    "risk_budget_usd": risk_budget_usd,
                    "bybit_order_id": None,
                    "order_link_id": order_link_id,
                    "status": "UNKNOWN",
                    "error_message": f"Network/API exception (slot retained): {str(exc)[:250]}",
                }
                save_demo_execution(exec_record, dsn=dsn, db_path=db_path)
        raise

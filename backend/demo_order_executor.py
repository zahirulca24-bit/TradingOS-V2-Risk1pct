"""TradingOS V2 Bybit Demo Order Execution Engine.
Automated execution of PLAN_READY signals on Bybit Demo (linear USDT perpetual).
Strict 1.0% equity risk budget enforcement, SL/TP attached, 3-slot maximum.
"""

import os
import time
import math
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pybit.unified_trading import HTTP

from demo_dashboard_api import read_demo_account, response_result
from demo_risk_engine import get_cached_instrument_specs, dec
from db_adapter import save_demo_execution, get_demo_executions
from telegram_notifier import send_telegram_message


ENV_FILE = Path(__file__).resolve().parent / ".env"


def is_demo_execution_enabled() -> bool:
    """Check if automated execution on Bybit Demo is enabled."""
    val = os.getenv("BYBIT_DEMO_EXECUTION_ENABLED", "true").strip().lower()
    return val in ("true", "1", "yes", "on", "active")


def quantize_value(val: Decimal, step: Decimal, rounding=ROUND_DOWN) -> Decimal:
    """Quantize a decimal value to exact step size."""
    step_units = (val / step).to_integral_value(rounding=rounding)
    return step_units * step


def format_step_string(val: Decimal, step: Decimal) -> str:
    """Format decimal to match step string decimals without scientific notation."""
    step_str = str(step)
    if "." in step_str:
        decimals = len(step_str.split(".")[1].rstrip("0"))
        # handle cases like 0.0001
        decimals = max(decimals, len(step_str.split(".")[1]))
        fmt = f"{{:.{decimals}f}}"
        return fmt.format(float(val))
    return str(int(val))


def execute_demo_plans(reports: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Evaluates committed trade plans and submits Bybit Demo Market orders for PLAN_READY setups."""
    if not is_demo_execution_enabled():
        print("[DEMO_EXECUTOR] Order execution is disabled by configuration.", flush=True)
        return []

    plans_report = reports.get("trade_plans", {})
    ready_plans = [
        p for p in plans_report.get("results", [])
        if p.get("status") == "PLAN_READY" and p.get("rr", 0) >= 2.0
    ]

    if not ready_plans:
        print("[DEMO_EXECUTOR] No PLAN_READY setups to execute in current cycle.", flush=True)
        return []

    cycle_id = reports.get("scanner", {}).get("cycle_id", "UNKNOWN")

    # Fetch live demo account balance and positions
    try:
        account = read_demo_account()
    except Exception as exc:
        print(f"[DEMO_EXECUTOR] Failed to read demo account: {exc}", flush=True)
        return []

    total_equity = account.get("total_equity") or 0.0
    available_balance = account.get("available_balance") or 0.0
    occupied_slots = account.get("occupied_slots") or 0
    remaining_slots = account.get("remaining_slots") or 0

    if total_equity <= 0:
        print("[DEMO_EXECUTOR] Aborting execution: invalid demo equity.", flush=True)
        return []

    if remaining_slots <= 0 or occupied_slots >= 3:
        msg = (
            f"⚠️ <b>TRADINGOS DEMO EXECUTION SKIPPED</b> ⚠️\n\n"
            f"Candidate: #{ready_plans[0].get('symbol')} is ready, but all <b>3 risk slots are occupied</b>.\n"
            f"Active positions/entries: {occupied_slots}/3.\n"
            f"Order blocked to protect 3% total risk limit."
        )
        send_telegram_message(msg, parse_mode="HTML")
        return []

    # Initialize Pybit HTTP session for Bybit Demo
    key = os.getenv("BYBIT_API_KEY", "").strip()
    secret = os.getenv("BYBIT_API_SECRET", "").strip()
    if not key or not secret:
        print("[DEMO_EXECUTOR] Missing Bybit API credentials.", flush=True)
        return []

    session = HTTP(demo=True, api_key=key, api_secret=secret)

    # 1.0% Equity Risk Budget
    risk_budget_usd = round(total_equity * 0.01, 2)
    executions = []

    for plan in ready_plans:
        if remaining_slots <= 0:
            break

        symbol = plan.get("symbol")
        direction = plan.get("direction", "LONG").upper()
        entry_ref = plan.get("entry_reference", 0.0)
        sl_price = plan.get("sl", 0.0)
        tp_price = plan.get("tp", 0.0)
        rr = plan.get("rr", 0.0)

        # Unique idempotency orderLinkId
        clean_cycle = cycle_id.replace("T", "").replace("Z", "")
        order_link_id = f"tos_{clean_cycle[-8:]}_{symbol}"

        # Fetch instrument specs
        specs = get_cached_instrument_specs(symbol)
        if not specs:
            print(f"[DEMO_EXECUTOR] Could not fetch specs for {symbol}, skipping.", flush=True)
            continue

        try:
            qty_step = Decimal(str(specs["qty_step"]))
            min_qty = Decimal(str(specs["min_qty"]))
            max_qty = Decimal(str(specs["max_qty"]))
            min_notional = Decimal(str(specs.get("min_notional") or "5"))
            tick_size = Decimal(str(specs["tick_size"]))

            d_entry = Decimal(str(entry_ref))
            d_sl = Decimal(str(sl_price))
            d_tp = Decimal(str(tp_price))
            d_budget = Decimal(str(risk_budget_usd))

            price_loss_per_unit = abs(d_entry - d_sl)
            # 0.06% fee + 0.05% slippage per leg = 0.0011 total unit friction
            cost_per_unit = (d_entry + d_sl) * Decimal("0.0011")
            unit_loss = price_loss_per_unit + cost_per_unit

            if unit_loss <= 0:
                print(f"[DEMO_EXECUTOR] Invalid unit loss for {symbol}, skipping.", flush=True)
                continue

            raw_qty = d_budget / unit_loss
            quantized_qty = quantize_value(raw_qty, qty_step, rounding=ROUND_DOWN)

            if quantized_qty < min_qty or quantized_qty > max_qty:
                print(f"[DEMO_EXECUTOR] Qty {quantized_qty} outside limits [{min_qty}, {max_qty}] for {symbol}.", flush=True)
                continue

            notional = quantized_qty * d_entry
            if notional < min_notional:
                print(f"[DEMO_EXECUTOR] Notional {notional} below min {min_notional} for {symbol}.", flush=True)
                continue

            # Quantize SL and TP to tick size
            quantized_sl = quantize_value(d_sl, tick_size, rounding=ROUND_HALF_UP)
            quantized_tp = quantize_value(d_tp, tick_size, rounding=ROUND_HALF_UP)

            qty_str = format_step_string(quantized_qty, qty_step)
            sl_str = format_step_string(quantized_sl, tick_size)
            tp_str = format_step_string(quantized_tp, tick_size)

            side = "Buy" if direction == "LONG" else "Sell"

            # Execute Bybit V5 Market Order
            print(f"[DEMO_EXECUTOR] Placing {side} {qty_str} {symbol} with SL={sl_str}, TP={tp_str}...", flush=True)
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

            resp = session.place_order(**order_params)
            ret_code = resp.get("retCode", -1)
            ret_msg = resp.get("retMsg", "Unknown response")
            result_data = resp.get("result", {})
            bybit_order_id = result_data.get("orderId", "")

            exec_record = {
                "cycle_id": cycle_id,
                "symbol": symbol,
                "direction": direction,
                "order_type": "Market",
                "qty": qty_str,
                "entry_price": float(d_entry),
                "sl": float(quantized_sl),
                "tp": float(quantized_tp),
                "rr": rr,
                "risk_budget_usd": risk_budget_usd,
                "bybit_order_id": bybit_order_id,
                "order_link_id": order_link_id,
                "status": "FILLED" if ret_code == 0 else "FAILED",
                "error_message": None if ret_code == 0 else f"{ret_code}: {ret_msg}",
            }
            save_demo_execution(exec_record)
            executions.append(exec_record)

            if ret_code == 0:
                remaining_slots -= 1
                occupied_slots += 1
                # Dispatch Telegram Order Alert
                icon = "🟢" if direction == "LONG" else "🔴"
                tg_msg = (
                    f"🚀 <b>BYBIT DEMO ORDER EXECUTED</b> 🚀\n\n"
                    f"🪙 <b>Pair:</b> #{symbol} ({direction} {icon})\n"
                    f"⚡ <b>Order Type:</b> Market Order (15m Candle Reversal)\n"
                    f"📦 <b>Quantity:</b> <code>{qty_str}</code>\n"
                    f"📍 <b>Entry Price:</b> <code>~${entry_ref:,.4f}</code>\n"
                    f"🛑 <b>Stop Loss:</b> <code>${float(quantized_sl):,.4f}</code>\n"
                    f"🎯 <b>Take Profit:</b> <code>${float(quantized_tp):,.4f}</code>\n"
                    f"⚖️ <b>Risk / Reward:</b> <code>1 : {rr:.2f}</code>\n"
                    f"🛡️ <b>Risk Budget:</b> <code>${risk_budget_usd:.2f} USD (1.0% Equity)</code>\n"
                    f"🆔 <b>Bybit Order ID:</b> <code>{bybit_order_id}</code>\n\n"
                    f"🌐 <b>Account:</b> Bybit Demo Account (Verified)"
                )
                send_telegram_message(tg_msg, parse_mode="HTML")
            else:
                print(f"[DEMO_EXECUTOR] Order rejected by Bybit: {ret_msg}", flush=True)
                tg_fail = (
                    f"❌ <b>BYBIT DEMO ORDER REJECTED</b> ❌\n\n"
                    f"Pair: #{symbol} ({direction})\n"
                    f"Reason: <code>{ret_msg} (code {ret_code})</code>"
                )
                send_telegram_message(tg_fail, parse_mode="HTML")

        except Exception as exc:
            print(f"[DEMO_EXECUTOR] Execution exception for {symbol}: {exc}", flush=True)
            save_demo_execution({
                "cycle_id": cycle_id,
                "symbol": symbol,
                "direction": direction,
                "order_type": "Market",
                "qty": "0",
                "entry_price": float(entry_ref),
                "sl": float(sl_price),
                "tp": float(tp_price),
                "rr": rr,
                "risk_budget_usd": risk_budget_usd,
                "bybit_order_id": None,
                "order_link_id": order_link_id,
                "status": "FAILED",
                "error_message": str(exc)[:250],
            })

    return executions


def place_single_demo_order(
    symbol: str,
    direction: str,
    entry_reference: Optional[float] = None,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
    rr: Optional[float] = None,
    source: str = "MANUAL_TERMINAL"
) -> Dict[str, Any]:
    """Places a single 1% risk market order on Bybit Demo with attached SL and TP."""
    symbol = symbol.strip().upper()
    direction = direction.strip().upper()
    if direction not in ("LONG", "SHORT"):
        raise ValueError("Direction must be LONG or SHORT")

    account = read_demo_account()
    total_equity = account.get("total_equity") or 0.0
    occupied_slots = account.get("occupied_slots") or 0
    remaining_slots = account.get("remaining_slots") or 0

    if total_equity <= 0:
        raise RuntimeError("Invalid demo account equity or Bybit disconnected")

    if remaining_slots <= 0 or occupied_slots >= 3:
        raise RuntimeError(f"Risk capacity reached: {occupied_slots}/3 slots already occupied")

    specs = get_cached_instrument_specs(symbol)
    if not specs:
        raise RuntimeError(f"Could not load instrument specs for {symbol} from Bybit")

    qty_step = Decimal(str(specs["qty_step"]))
    min_qty = Decimal(str(specs["min_qty"]))
    max_qty = Decimal(str(specs["max_qty"]))
    min_notional = Decimal(str(specs.get("min_notional") or "5"))
    tick_size = Decimal(str(specs["tick_size"]))

    risk_budget_usd = round(total_equity * 0.01, 2)
    d_budget = Decimal(str(risk_budget_usd))

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
    else:
        d_sl = Decimal(str(sl))

    if not tp or tp <= 0:
        tp_pct = Decimal("0.036")
        d_tp = d_entry * (Decimal("1") + tp_pct) if direction == "LONG" else d_entry * (Decimal("1") - tp_pct)
    else:
        d_tp = Decimal(str(tp))

    price_loss_per_unit = abs(d_entry - d_sl)
    if price_loss_per_unit <= 0:
        raise ValueError("Stop loss cannot equal entry price")

    cost_per_unit = (d_entry + d_sl) * Decimal("0.0011")
    unit_loss = price_loss_per_unit + cost_per_unit

    raw_qty = d_budget / unit_loss
    quantized_qty = quantize_value(raw_qty, qty_step, rounding=ROUND_DOWN)

    if quantized_qty < min_qty or quantized_qty > max_qty:
        raise ValueError(f"Calculated quantity {quantized_qty} is outside Bybit limits [{min_qty}, {max_qty}]")

    notional = quantized_qty * d_entry
    if notional < min_notional:
        raise ValueError(f"Position notional ${float(notional):.2f} is below Bybit minimum ${float(min_notional):.2f}")

    quantized_sl = quantize_value(d_sl, tick_size, rounding=ROUND_HALF_UP)
    quantized_tp = quantize_value(d_tp, tick_size, rounding=ROUND_HALF_UP)

    qty_str = format_step_string(quantized_qty, qty_step)
    sl_str = format_step_string(quantized_sl, tick_size)
    tp_str = format_step_string(quantized_tp, tick_size)

    side = "Buy" if direction == "LONG" else "Sell"
    ts_now = int(time.time())
    order_link_id = f"term_{ts_now}_{symbol}"

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

    resp = session.place_order(**order_params)
    ret_code = resp.get("retCode", -1)
    ret_msg = resp.get("retMsg", "Unknown response")
    result_data = resp.get("result", {})
    bybit_order_id = result_data.get("orderId", "")

    calc_rr = rr if (rr and rr > 0) else round(float(abs(d_tp - d_entry) / abs(d_entry - d_sl)), 2)

    exec_record = {
        "cycle_id": f"MANUAL_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "symbol": symbol,
        "direction": direction,
        "order_type": "Market",
        "qty": qty_str,
        "entry_price": float(d_entry),
        "sl": float(quantized_sl),
        "tp": float(quantized_tp),
        "rr": calc_rr,
        "risk_budget_usd": risk_budget_usd,
        "bybit_order_id": bybit_order_id,
        "order_link_id": order_link_id,
        "status": "FILLED" if ret_code == 0 else "FAILED",
        "error_message": None if ret_code == 0 else f"{ret_code}: {ret_msg}",
    }
    save_demo_execution(exec_record)

    if ret_code != 0:
        raise RuntimeError(f"Bybit rejected order: {ret_msg} (code {ret_code})")

    icon = "🟢" if direction == "LONG" else "🔴"
    tg_msg = (
        f"⚡ <b>TERMINAL 1-CLICK DEMO ORDER EXECUTED</b> ⚡\n\n"
        f"🪙 <b>Pair:</b> #{symbol} ({direction} {icon})\n"
        f"⚡ <b>Order Type:</b> Market Order (Interactive Terminal)\n"
        f"📦 <b>Quantity:</b> <code>{qty_str}</code>\n"
        f"📍 <b>Entry Price:</b> <code>~${float(d_entry):,.4f}</code>\n"
        f"🛑 <b>Stop Loss:</b> <code>${float(quantized_sl):,.4f}</code>\n"
        f"🎯 <b>Take Profit:</b> <code>${float(quantized_tp):,.4f}</code>\n"
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
        "entry": float(d_entry),
        "sl": float(quantized_sl),
        "tp": float(quantized_tp),
        "rr": calc_rr,
        "risk_usd": risk_budget_usd,
        "message": f"Successfully placed {direction} market order for {qty_str} {symbol} on Bybit Demo!",
    }


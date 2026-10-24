"""Read-only Bybit DEMO risk evaluation and dry-run position sizing.
Strictly DRY_RUN_ONLY. Absolutely no order placement, amendment, cancellation, or execution methods.

The calculations are dry-run previews; live order sizing requires exchange specification,
current executable quote, fees, leverage/margin and race-safe open order checks.
"""
import os
import time
import math
import urllib.request
import json
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union, Dict, Any, List
from pipeline_contract import MIN_RR as CANONICAL_MIN_RR

RISK_FRACTION = Decimal('0.01')
MAX_OPEN_AND_PENDING = 3
MIN_RR = Decimal(str(CANONICAL_MIN_RR))
MAX_PLAN_AGE_SECONDS = 900
MAX_ACCOUNT_AGE_SECONDS = 120
DEFAULT_FEE_RATE = Decimal('0.0006')       # 0.06% per leg (0.12% round trip)
DEFAULT_SLIPPAGE_RATE = Decimal('0.0005')  # 0.05% per leg (0.10% round trip)

EXECUTION_WARNING = (
    "Research dry-run preview only. Absolutely no order placement. "
    "Modeled loss includes estimated 0.06% taker fees and 0.05% adverse slippage per leg. "
    "Actual realized loss may exceed modeled loss during extreme market volatility, weekend/overnight gaps, "
    "liquidity vacuums, or worse-than-modeled slippage."
)

# In-memory instrument filter cache with 300s TTL
_INSTRUMENT_CACHE: dict[str, tuple[float, dict]] = {}


def dec(value, name):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f'{name} must be numeric') from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f'{name} must be positive and finite')
    return result


def classify_order(order: dict) -> tuple[str, str]:
    """Classify an order into 'EXIT', 'ENTRY', or 'AMBIGUOUS'.

    Returns (category, reason) where category is in ('EXIT', 'ENTRY', 'AMBIGUOUS').
    Treats unknown or corrupt metadata as AMBIGUOUS, never silently assuming exit.
    """
    if not isinstance(order, dict):
        return ('AMBIGUOUS', 'Order record is not a dictionary')

    # Basic symbol sanity check
    sym = order.get("symbol")
    if not sym or not isinstance(sym, str) or not sym.strip():
        return ('AMBIGUOUS', 'Order missing valid symbol')

    # 1. Reliable Exit Indicators
    reduce_only = order.get("reduceOnly")
    if reduce_only in (True, "true", "True", "1", 1):
        return ('EXIT', 'reduceOnly flag is active')
    elif reduce_only not in (False, "false", "False", "0", 0, None, ""):
        return ('AMBIGUOUS', f'Unrecognized reduceOnly value: {reduce_only!r}')

    close_on_trigger = order.get("closeOnTrigger")
    if close_on_trigger in (True, "true", "True", "1", 1):
        return ('EXIT', 'closeOnTrigger flag is active')
    elif close_on_trigger not in (False, "false", "False", "0", 0, None, ""):
        return ('AMBIGUOUS', f'Unrecognized closeOnTrigger value: {close_on_trigger!r}')

    stop_type = str(order.get("stopOrderType") or "").strip().lower()
    if stop_type in ("stoploss", "takeprofit", "trailingstop", "tpslorder", "partialstoploss", "partialtakeprofit"):
        return ('EXIT', f'stopOrderType is {stop_type}')

    order_type = str(order.get("orderType") or "").strip().lower()
    if order_type in ("takeprofit", "stoploss"):
        return ('EXIT', f'orderType is {order_type}')

    # Inactive order statuses
    order_status = str(order.get("orderStatus") or "").strip().lower()
    if order_status in ("cancelled", "filled", "rejected", "deactivated"):
        return ('EXIT', f'orderStatus is inactive ({order_status})')

    # Otherwise, it represents an active commitment to enter or add to exposure
    return ('ENTRY', 'Active non-reduce order commitment')


def is_entry_order(order: dict) -> bool:
    """Helper returning True if order is confirmed ENTRY, False if confirmed EXIT.
    Raises ValueError if order is AMBIGUOUS.
    """
    category, reason = classify_order(order)
    if category == 'AMBIGUOUS':
        raise ValueError(f"Ambiguous order metadata: {reason}")
    return category == 'ENTRY'


def count_occupied_slots(open_positions, pending_orders):
    """Conservative exposure commitment slot counting.

    Policy: Maximum 3 combined active position legs and distinct pending entry order commitments.

    Rules:
    1. Position legs: Identifies each distinct active position leg (size > 0).
       Hedge-mode legs on the same symbol (positionIdx 1 = Buy, positionIdx 2 = Sell) are counted
       as independent real exposure commitments. Deduplicates identical (symbol, positionIdx).
    2. Pending entry orders: Identifies all active pending orders that could create or add exposure.
       Filters out reliably confirmed exit-only orders (reduceOnly, closeOnTrigger, TP/SL).
       Deduplicates multiple API records sharing the same immutable orderId.
       Distinct entry orders with different orderIds (even on the same symbol) are counted separately.
    3. Safety Fail-Closed: If any order metadata is ambiguous or unverified, or if positions/orders
       are None (e.g. pagination failed), returns None (triggers POSITION_OR_ORDER_COUNT_UNKNOWN / BLOCKED_DATA).
    4. Numeric counts: If integers are passed (e.g. from validated API upstream), returns open_positions + pending_orders.
    """
    if open_positions is None or pending_orders is None:
        return None

    if isinstance(open_positions, int) and isinstance(pending_orders, int):
        if open_positions < 0 or pending_orders < 0:
            return None
        return open_positions + pending_orders

    # 1. Parse active position legs
    active_legs_keys = set()
    if isinstance(open_positions, (list, tuple, set)):
        for idx_pos, p in enumerate(open_positions):
            if isinstance(p, dict):
                size = p.get('size')
                try:
                    if size is not None and float(size) > 0:
                        sym = str(p.get('symbol') or '').strip().upper()
                        if not sym:
                            return None  # Malformed position data -> fail closed
                        pos_idx = str(p.get('positionIdx', '0')).strip()
                        # Key identity: (symbol, positionIdx)
                        active_legs_keys.add((sym, pos_idx))
                except (ValueError, TypeError):
                    return None  # Unparseable size -> fail closed
            elif isinstance(p, str):
                active_legs_keys.add((p.strip().upper(), f"str_{idx_pos}"))
            else:
                return None
    elif isinstance(open_positions, int):
        if open_positions < 0:
            return None
        active_legs_count = open_positions
    else:
        return None

    # 2. Parse distinct pending entry orders
    entry_order_ids = set()
    anon_entry_count = 0
    if isinstance(pending_orders, (list, tuple, set)):
        for idx_ord, o in enumerate(pending_orders):
            if not isinstance(o, dict):
                return None  # Non-dict order object -> fail closed

            category, _ = classify_order(o)
            if category == 'AMBIGUOUS':
                return None  # Ambiguous order metadata -> fail closed
            elif category == 'EXIT':
                continue  # Confirmed exit-only order, excluded from entry exposure
            elif category == 'ENTRY':
                oid = o.get('orderId')
                if oid:
                    # Deduplicate duplicate API records for the same immutable order ID
                    entry_order_ids.add(str(oid))
                else:
                    # If order has no orderId, count each distinct dict item
                    anon_entry_count += 1
    elif isinstance(pending_orders, int):
        if pending_orders < 0:
            return None
        entry_orders_count = pending_orders
    else:
        return None

    legs_count = len(active_legs_keys) if not isinstance(open_positions, int) else open_positions
    orders_count = (len(entry_order_ids) + anon_entry_count) if not isinstance(pending_orders, int) else pending_orders

    return legs_count + orders_count


def is_recent_cycle(manifest, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        close = datetime.fromisoformat(manifest['candle_close_utc'].replace('Z', '+00:00'))
        if close.tzinfo is None:
            return False
        age = (now - close).total_seconds()
        return -60 <= age <= MAX_PLAN_AGE_SECONDS
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def is_recent_account(account_or_updated_at, now=None, max_age_seconds=MAX_ACCOUNT_AGE_SECONDS):
    now = now or datetime.now(timezone.utc)
    try:
        if isinstance(account_or_updated_at, dict):
            updated_at = account_or_updated_at.get("updated_at")
        else:
            updated_at = account_or_updated_at
        if not updated_at:
            return False
        dt = datetime.fromisoformat(str(updated_at).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            return False
        age = (now - dt).total_seconds()
        return -60 <= age <= max_age_seconds
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def get_cached_instrument_specs(symbol: str) -> dict | None:
    """Fetch public Bybit Linear perpetual instrument specs with TTL cache."""
    now = time.monotonic()
    if symbol in _INSTRUMENT_CACHE:
        cached_time, cached_specs = _INSTRUMENT_CACHE[symbol]
        if now - cached_time < 300:
            return cached_specs

    try:
        url = f"https://api.bybit.com/v5/market/instruments-info?category=linear&symbol={symbol}"
        req = urllib.request.Request(url, headers={"User-Agent": "TradingOS-V2/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("retCode") != 0:
            return None
        items = data.get("result", {}).get("list", [])
        if not items:
            return None
        info = items[0]
        lot_filter = info.get("lotSizeFilter", {})
        price_filter = info.get("priceFilter", {})
        specs = {
            "qty_step": lot_filter.get("qtyStep"),
            "min_qty": lot_filter.get("minOrderQty"),
            "max_qty": lot_filter.get("maxOrderQty"),
            "min_notional": lot_filter.get("minNotionalValue") or "5",
            "tick_size": price_filter.get("tickSize"),
            "status": info.get("status")
        }
        if specs["status"] == "Trading" and specs["qty_step"] and specs["min_qty"] and specs["max_qty"]:
            _INSTRUMENT_CACHE[symbol] = (now, specs)
            return specs
    except Exception:
        pass
    return None


REASON_MESSAGES = {
    'MAX_OPEN_AND_PENDING_REACHED': 'Capacity limit reached: 3 combined positions and pending entry orders currently active.',
    'POSITION_OR_ORDER_COUNT_UNKNOWN': 'Account exposure capacity is unverified: position or order counts are unknown, ambiguous, or pagination incomplete.',
    'INVALID_POSITION_OR_ORDER_COUNT': 'Position or order count is invalid.',
    'PLAN_NOT_READY': 'Candidate plan is not ready (WAIT/REJECTED status).',
    'STALE_OR_UNVERIFIED_CYCLE': 'Committed scanner cycle is stale (> 15 minutes) or unverified.',
    'STALE_DEMO_ACCOUNT': 'Demo account equity snapshot is stale (> 120 seconds).',
    'LATEST_SCAN_FAILED': 'Latest scanner attempt failed; plans are research-only references.',
    'INVALID_DIRECTION': 'Trade direction must be LONG or SHORT.',
    'INVALID_PRICE_DIRECTION': 'Price levels conflict with direction (LONG requires SL < Entry < TP; SHORT requires TP < Entry < SL).',
    'RR_BELOW_TWO': 'Risk/reward ratio is below the required minimum of 2.0.',
    'PRICE_RR_BELOW_TWO': 'Price geometry risk/reward is below the required minimum of 2.0.',
    'INVALID_PLAN_OR_EQUITY': 'Trade plan price levels or demo account equity is missing, negative, or invalid.',
    'EXCHANGE_SPECS_MARGIN_FEES_OR_SLIPPAGE_UNVERIFIED': 'Instrument exchange specifications, available margin, or cost parameters unverified.',
    'INVALID_EXCHANGE_SPECS_OR_COSTS': 'Instrument limits, fee rates, or slippage models are non-positive or invalid.',
    'QTY_OUTSIDE_EXCHANGE_LIMITS': 'Calculated position size is outside exchange minimum or maximum quantity limits.',
    'BELOW_MIN_NOTIONAL': 'Calculated position value is below exchange minimum notional threshold.',
    'INSUFFICIENT_UNLEVERAGED_AVAILABLE_BALANCE': 'Position notional exceeds available unleveraged account balance.',
    'ESTIMATED_LOSS_EXCEEDS_BUDGET': 'Conservative estimated loss exceeds exact 1% risk budget.',
}


def size_dry_run(plan, equity, *, open_positions, pending_orders, cycle_fresh,
                 qty_step=None, min_qty=None, max_qty=None, min_notional=None,
                 available_balance=None, fee_rate=None, slippage_rate=None,
                 account_fresh=True, latest_scan_failed=False):
    """Calculate decimal-safe 1% dry-run position size and return comprehensive risk preview.

    Always returns a non-executable preview with explicit block reasons.
    Even a DRY_RUN_VALID result here must NEVER authorize sending an order.
    """
    fee_rate = fee_rate if fee_rate is not None else DEFAULT_FEE_RATE
    slippage_rate = slippage_rate if slippage_rate is not None else DEFAULT_SLIPPAGE_RATE

    response = {
        'mode': 'DRY_RUN_ONLY',
        'trading_enabled': False,
        'real_trading_enabled': False,
        'order_execution': 'ORDER_EXECUTION_DISABLED',
        'risk_percent': 1.0,
        'max_open_and_pending': MAX_OPEN_AND_PENDING,
        'status': 'BLOCKED_DATA',
        'reasons': [],
        'reason_messages': [],
        'quantity': None,
        'proposed_quantity': None,
        'qty_step': str(qty_step) if qty_step is not None else None,
        'risk_budget_usd': None,
        'estimated_max_loss_usd': None,
        'estimated_position_notional_usd': None,
        'estimated_stop_loss_amount_usd': None,
        'estimated_fees_and_slippage_usd': None,
        'total_conservative_projected_risk_usd': None,
        'risk_utilization_pct': None,
        'margin_required_usd': None,
        'symbol': plan.get('symbol') if isinstance(plan, dict) else None,
        'direction': plan.get('direction') if isinstance(plan, dict) else None,
        'entry_reference': plan.get('entry_reference') if isinstance(plan, dict) else None,
        'sl': plan.get('sl') if isinstance(plan, dict) else None,
        'tp': plan.get('tp') if isinstance(plan, dict) else None,
        'rr': plan.get('rr') if isinstance(plan, dict) else None,
        'existing_slots': None,
        'remaining_slots': None,
        'execution_warning': EXECUTION_WARNING,
    }
    reasons = response['reasons']

    # 1. Plan Readiness Check
    if not isinstance(plan, dict):
        reasons.append('INVALID_PLAN_OR_EQUITY')
        response['status'] = 'BLOCKED_DATA'
        response['reason_messages'] = [REASON_MESSAGES.get(r, r) for r in reasons]
        return response

    plan_status = plan.get('status')
    if plan_status == 'WAIT':
        response['status'] = 'WAIT'
        reasons.append('PLAN_NOT_READY')
        response['reason_messages'] = [REASON_MESSAGES['PLAN_NOT_READY']]
        return response
    elif plan_status == 'REJECTED':
        response['status'] = 'REJECTED'
        reasons.append('PLAN_NOT_READY')
        response['reason_messages'] = [REASON_MESSAGES['PLAN_NOT_READY']]
        return response
    elif plan_status != 'PLAN_READY':
        response['status'] = 'BLOCKED_DATA'
        reasons.append('PLAN_NOT_READY')
        response['reason_messages'] = [REASON_MESSAGES['PLAN_NOT_READY']]
        return response

    # 2. Staleness & Scan Attempt Checks
    if not cycle_fresh:
        reasons.append('STALE_OR_UNVERIFIED_CYCLE')
    if not account_fresh:
        reasons.append('STALE_DEMO_ACCOUNT')
    if latest_scan_failed:
        reasons.append('LATEST_SCAN_FAILED')

    # 3. Capacity & Slots
    occupied = count_occupied_slots(open_positions, pending_orders)
    if occupied is None:
        reasons.append('POSITION_OR_ORDER_COUNT_UNKNOWN')
    elif not isinstance(occupied, int) or occupied < 0:
        reasons.append('INVALID_POSITION_OR_ORDER_COUNT')
    else:
        response['existing_slots'] = occupied
        response['remaining_slots'] = max(0, MAX_OPEN_AND_PENDING - occupied)
        if occupied >= MAX_OPEN_AND_PENDING:
            reasons.append('MAX_OPEN_AND_PENDING_REACHED')

    # 4. Direction
    direction = plan.get('direction')
    if direction not in ('LONG', 'SHORT'):
        reasons.append('INVALID_DIRECTION')

    # 5. Price Geometry & RR
    entry, sl, tp, rr, eq = None, None, None, None, None
    try:
        entry = dec(plan.get('entry_reference'), 'entry')
        sl = dec(plan.get('sl'), 'stop loss')
        tp = dec(plan.get('tp'), 'take profit')
        rr = dec(plan.get('rr'), 'RR')
        eq = dec(equity, 'equity')
        if eq > Decimal('100000000000'):
            raise ValueError('unrealistic equity')

        if not ((direction == 'LONG' and sl < entry < tp) or
                (direction == 'SHORT' and tp < entry < sl)):
            reasons.append('INVALID_PRICE_DIRECTION')
        if rr < MIN_RR:
            reasons.append('RR_BELOW_TWO')

        raw_rr = (tp - entry) / (entry - sl) if direction == 'LONG' and entry != sl else (entry - tp) / (sl - entry) if direction == 'SHORT' and sl != entry else Decimal('0')
        if raw_rr < MIN_RR:
            reasons.append('PRICE_RR_BELOW_TWO')

        budget = eq * RISK_FRACTION
        response['risk_budget_usd'] = float(budget)
    except (ValueError, ArithmeticError):
        reasons.append('INVALID_PLAN_OR_EQUITY')

    # If already blocked by data or capacity or staleness, determine status and exit early
    if any(r in ('INVALID_PLAN_OR_EQUITY', 'POSITION_OR_ORDER_COUNT_UNKNOWN', 'INVALID_POSITION_OR_ORDER_COUNT') for r in reasons):
        response['status'] = 'BLOCKED_DATA'
        response['reason_messages'] = [REASON_MESSAGES.get(r, r) for r in reasons]
        return response
    if any(r in ('STALE_OR_UNVERIFIED_CYCLE', 'STALE_DEMO_ACCOUNT', 'LATEST_SCAN_FAILED') for r in reasons):
        response['status'] = 'BLOCKED_STALE'
        response['reason_messages'] = [REASON_MESSAGES.get(r, r) for r in reasons]
        return response
    if 'MAX_OPEN_AND_PENDING_REACHED' in reasons:
        response['status'] = 'BLOCKED_CAPACITY'
        response['reason_messages'] = [REASON_MESSAGES.get(r, r) for r in reasons]
        return response

    # 6. Instrument specs & costs
    required = (qty_step, min_qty, max_qty, min_notional, available_balance, fee_rate, slippage_rate)
    if any(v is None for v in required):
        reasons.append('EXCHANGE_SPECS_MARGIN_FEES_OR_SLIPPAGE_UNVERIFIED')
        response['status'] = 'BLOCKED_DATA'
        response['reason_messages'] = [REASON_MESSAGES.get(r, r) for r in reasons]
        return response

    try:
        step = dec(qty_step, 'qty step')
        minimum = dec(min_qty, 'min qty')
        maximum = dec(max_qty, 'max qty')
        notional_min = dec(min_notional, 'min notional')
        balance = dec(available_balance, 'available balance')
        fee = Decimal(str(fee_rate))
        slip = Decimal(str(slippage_rate))

        # Both fee and slip must be strictly positive and finite
        if not all(v.is_finite() and v > 0 for v in (fee, slip)) or fee + slip >= Decimal('0.1'):
            raise ValueError('invalid fees/slippage')

        # Conservative loss estimate: price gap + entry fees/slip + exit fees/slip
        price_loss_per_unit = abs(entry - sl)
        cost_per_unit = (entry + sl) * (fee + slip)
        unit_loss = price_loss_per_unit + cost_per_unit
        if unit_loss <= 0:
            raise ValueError('unit loss must be positive')

        raw_units = budget / unit_loss
        step_units = (raw_units / step).to_integral_value(rounding=ROUND_DOWN)
        quantity = step_units * step

        # Check limits
        if quantity <= 0 or quantity < minimum or quantity > maximum:
            reasons.append('QTY_OUTSIDE_EXCHANGE_LIMITS')

        notional = quantity * entry
        if notional < notional_min:
            reasons.append('BELOW_MIN_NOTIONAL')

        # Unleveraged available balance check
        if notional > balance:
            reasons.append('INSUFFICIENT_UNLEVERAGED_AVAILABLE_BALANCE')

        # Recalculate conservative projected loss with exact rounded quantity
        sl_loss = quantity * price_loss_per_unit
        fee_slip_cost = quantity * cost_per_unit
        total_risk = sl_loss + fee_slip_cost

        if total_risk > budget:
            reasons.append('ESTIMATED_LOSS_EXCEEDS_BUDGET')

        # Populate calculations in response
        response['quantity'] = str(quantity)
        response['proposed_quantity'] = str(quantity)
        response['estimated_position_notional_usd'] = float(notional)
        response['estimated_stop_loss_amount_usd'] = float(sl_loss)
        response['estimated_fees_and_slippage_usd'] = float(fee_slip_cost)
        response['total_conservative_projected_risk_usd'] = float(total_risk)
        response['estimated_max_loss_usd'] = float(total_risk)
        if eq and eq > 0:
            response['risk_utilization_pct'] = round(float((total_risk / eq) * Decimal('100')), 4)
        response['margin_required_usd'] = float(notional)

    except (ValueError, ArithmeticError, InvalidOperation):
        reasons.append('INVALID_EXCHANGE_SPECS_OR_COSTS')

    # Final status determination
    if not reasons:
        response['status'] = 'DRY_RUN_VALID'
    elif 'INSUFFICIENT_UNLEVERAGED_AVAILABLE_BALANCE' in reasons or 'MAX_OPEN_AND_PENDING_REACHED' in reasons:
        response['status'] = 'BLOCKED_CAPACITY'
    elif any(r in ('INVALID_PLAN_OR_EQUITY', 'EXCHANGE_SPECS_MARGIN_FEES_OR_SLIPPAGE_UNVERIFIED', 'INVALID_EXCHANGE_SPECS_OR_COSTS') for r in reasons):
        response['status'] = 'BLOCKED_DATA'
    else:
        response['status'] = 'BLOCKED_RISK'

    response['reason_messages'] = [REASON_MESSAGES.get(r, r) for r in reasons]
    return response


def is_demo_execution_enabled() -> bool:
    """Check if automated execution on Bybit Demo is enabled."""
    val = os.getenv("BYBIT_DEMO_EXECUTION_ENABLED", "false").strip().lower()
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
        decimals = max(decimals, len(step_str.split(".")[1]))
        fmt = f"{{:.{decimals}f}}"
        return fmt.format(float(val))
    return str(int(val))


def authorize_execution_order(
    plan: Dict[str, Any],
    account: Dict[str, Any],
    specs: Optional[Dict[str, Any]] = None,
    *,
    cycle_fresh: bool = True,
    account_fresh: bool = True,
    latest_scan_failed: bool = False,
    db_path: Optional[Union[str, Path]] = None,
    dsn: Optional[str] = None,
) -> Dict[str, Any]:
    """Authoritative execution authorization gate.
    
    Evaluates:
    1. Persistent kill switch (fail-closed if active)
    2. Execution enabled setting
    3. Daily risk & circuit breaker (fail-closed if equity unknown/stale/tripped)
    4. Account validity (fresh verified equity > 0)
    5. Combined 3-slot capacity (positions + orders + unreconciled DB reservations)
    6. Mathematical sizing and risk policy (RR >= 2.0, <= 1% risk budget)
    7. Exchange specs, min notional, step sizes, and available margin
    8. Quantized SL/TP tick-size alignment
    
    Returns structured authorization result. If authorized is False, order MUST NOT be submitted.
    """
    from db_adapter import (
        get_kill_switch_status,
        evaluate_daily_risk_guard,
        get_active_slot_reservations,
    )

    # 1. Kill switch check (strict priority)
    kill_switch = get_kill_switch_status(dsn=dsn, db_path=db_path)
    if kill_switch.get("enabled", False) or kill_switch.get("active", False):
        reason = kill_switch.get("reason") or "KILL_SWITCH_ACTIVE"
        return {
            "authorized": False,
            "status": "BLOCKED_KILL_SWITCH",
            "reasons": [f"Execution kill switch is active: {reason}"],
            "quantity": None,
            "quantized_sl": None,
            "quantized_tp": None,
            "risk_budget_usd": None,
            "estimated_risk_usd": None,
            "details": {"kill_switch": kill_switch},
        }

    # 2. Execution enabled setting
    if not is_demo_execution_enabled():
        return {
            "authorized": False,
            "status": "BLOCKED_EXECUTION_DISABLED",
            "reasons": ["Order execution is disabled by environment configuration."],
            "quantity": None,
            "quantized_sl": None,
            "quantized_tp": None,
            "risk_budget_usd": None,
            "estimated_risk_usd": None,
            "details": {},
        }

    # 3. Account equity and status verification
    if not isinstance(account, dict):
        return {
            "authorized": False,
            "status": "BLOCKED_DATA",
            "reasons": ["Account data is missing or invalid."],
            "quantity": None,
            "quantized_sl": None,
            "quantized_tp": None,
            "risk_budget_usd": None,
            "estimated_risk_usd": None,
            "details": {},
        }

    equity = account.get("total_equity")
    if equity is None or not isinstance(equity, (int, float)) or equity <= 0:
        return {
            "authorized": False,
            "status": "BLOCKED_DATA",
            "reasons": ["Account equity is missing, zero, or unverified."],
            "quantity": None,
            "quantized_sl": None,
            "quantized_tp": None,
            "risk_budget_usd": None,
            "estimated_risk_usd": None,
            "details": {"equity": equity},
        }

    if account.get("positions_error") or account.get("orders_error"):
        return {
            "authorized": False,
            "status": "BLOCKED_DATA",
            "reasons": ["Account open positions or orders are degraded or unverified."],
            "quantity": None,
            "quantized_sl": None,
            "quantized_tp": None,
            "risk_budget_usd": None,
            "estimated_risk_usd": None,
            "details": {"account": account},
        }

    # 4. Daily risk & circuit breaker guard (fail-closed, with PnL reconciliation audit)
    daily_guard = evaluate_daily_risk_guard(verify_pnl_reconciliation=True, dsn=dsn, db_path=db_path)
    if not daily_guard.get("can_trade", False):
        risk_state = daily_guard.get("risk_state", "BLOCKED_RISK")
        reason = daily_guard.get("reason", "Daily risk guard blocked trading")
        return {
            "authorized": False,
            "status": "BLOCKED_DAILY_RISK",
            "reasons": [f"Daily risk guard blocked: {risk_state} ({reason})"],
            "quantity": None,
            "quantized_sl": None,
            "quantized_tp": None,
            "risk_budget_usd": None,
            "estimated_risk_usd": None,
            "details": {"daily_guard": daily_guard},
        }

    # 5. Combined 3-slot capacity enforcement & position protection audit
    symbol = plan.get("symbol")
    open_positions = account.get("open_positions_list") or []
    if not open_positions and account.get("open_positions"):
        open_pos_count = account.get("open_positions", 0)
    else:
        open_pos_count = len(open_positions)

    # Fail closed if any existing open position lacks verified Stop Loss protection
    if open_positions:
        from reconciliation import audit_all_positions_protection
        prot_audit = audit_all_positions_protection(open_positions)
        if not prot_audit["all_protected"]:
            if prot_audit["uncertain_count"] > 0:
                return {
                    "authorized": False,
                    "status": "BLOCKED_API_UNCERTAINTY",
                    "reasons": ["Open position protection verification failed due to API uncertainty."],
                    "quantity": None,
                    "quantized_sl": None,
                    "quantized_tp": None,
                    "risk_budget_usd": None,
                    "estimated_risk_usd": None,
                    "details": {"protection_audit": prot_audit},
                }
            if prot_audit["unprotected_count"] > 0:
                unprot_syms = [p["symbol"] for p in prot_audit["unprotected_positions"]]
                return {
                    "authorized": False,
                    "status": "BLOCKED_UNPROTECTED_EXPOSURE",
                    "reasons": [f"Open position on {', '.join(unprot_syms)} has no verified Stop Loss protection."],
                    "quantity": None,
                    "quantized_sl": None,
                    "quantized_tp": None,
                    "risk_budget_usd": None,
                    "estimated_risk_usd": None,
                    "details": {"protection_audit": prot_audit},
                }

    pending_orders = account.get("pending_orders") or 0
    if isinstance(pending_orders, list):
        pending_order_count = len(pending_orders)
    else:
        pending_order_count = int(pending_orders)

    # Check active database slot reservations
    db_slots = get_active_slot_reservations(dsn=dsn, db_path=db_path)
    unreconciled_db = [
        r for r in db_slots
        if r.get("status") in ("RESERVED", "ACTIVE", "HELD_PENDING_RECONCILIATION")
    ]
    open_syms = {p.get("symbol") for p in open_positions if isinstance(p, dict)}
    active_untracked_db_slots = [r for r in unreconciled_db if r.get("symbol") not in open_syms]
    total_occupied_slots = open_pos_count + pending_order_count + len(active_untracked_db_slots)

    if total_occupied_slots >= MAX_OPEN_AND_PENDING:
        return {
            "authorized": False,
            "status": "BLOCKED_CAPACITY",
            "reasons": [f"Combined 3-slot capacity reached: {total_occupied_slots}/{MAX_OPEN_AND_PENDING} occupied."],
            "quantity": None,
            "quantized_sl": None,
            "quantized_tp": None,
            "risk_budget_usd": None,
            "estimated_risk_usd": None,
            "details": {
                "open_positions": open_pos_count,
                "pending_orders": pending_order_count,
                "db_reservations": len(active_untracked_db_slots),
                "total_occupied_slots": total_occupied_slots,
                "max_slots": MAX_OPEN_AND_PENDING,
            },
        }

    # Prevent duplicate pending reservation for same symbol
    if any(r.get("symbol") == symbol for r in unreconciled_db):
        return {
            "authorized": False,
            "status": "BLOCKED_DUPLICATE",
            "reasons": [f"Symbol {symbol} already has an active or unreconciled slot reservation."],
            "quantity": None,
            "quantized_sl": None,
            "quantized_tp": None,
            "risk_budget_usd": None,
            "estimated_risk_usd": None,
            "details": {"unreconciled_reservations": [r for r in unreconciled_db if r.get("symbol") == symbol]},
        }

    # 6. Instrument specifications
    symbol = plan.get("symbol")
    if specs is None:
        specs = get_cached_instrument_specs(symbol)
    if not specs or not specs.get("qty_step") or not specs.get("min_qty"):
        return {
            "authorized": False,
            "status": "BLOCKED_DATA",
            "reasons": [f"Instrument specs for {symbol} are missing or unverified."],
            "quantity": None,
            "quantized_sl": None,
            "quantized_tp": None,
            "risk_budget_usd": None,
            "estimated_risk_usd": None,
            "details": {},
        }

    # 7. Sizing and risk policy validation via size_dry_run
    available_balance = account.get("available_balance") or 0.0
    dry_run = size_dry_run(
        plan,
        equity,
        open_positions=open_positions,
        pending_orders=account.get("pending_orders_list") or [],
        cycle_fresh=cycle_fresh,
        account_fresh=account_fresh,
        latest_scan_failed=latest_scan_failed,
        qty_step=specs.get("qty_step"),
        min_qty=specs.get("min_qty"),
        max_qty=specs.get("max_qty"),
        min_notional=specs.get("min_notional") or "5",
        available_balance=available_balance,
        fee_rate=specs.get("fee_rate", DEFAULT_FEE_RATE),
        slippage_rate=specs.get("slippage_rate", DEFAULT_SLIPPAGE_RATE),
    )

    if dry_run.get("status") != "DRY_RUN_VALID":
        return {
            "authorized": False,
            "status": dry_run.get("status", "BLOCKED_RISK"),
            "reasons": dry_run.get("reason_messages", []),
            "quantity": None,
            "quantized_sl": None,
            "quantized_tp": None,
            "risk_budget_usd": dry_run.get("risk_budget_usd"),
            "estimated_risk_usd": dry_run.get("total_conservative_projected_risk_usd"),
            "details": dry_run,
        }

    # 8. Align SL and TP to tick size
    tick_size = Decimal(str(specs.get("tick_size") or "0.01"))
    d_sl = Decimal(str(plan.get("sl")))
    d_tp = Decimal(str(plan.get("tp")))
    quantized_sl = quantize_value(d_sl, tick_size, rounding=ROUND_HALF_UP)
    quantized_tp = quantize_value(d_tp, tick_size, rounding=ROUND_HALF_UP)

    return {
        "authorized": True,
        "status": "AUTHORIZED",
        "reasons": [],
        "quantity": dry_run.get("quantity"),
        "quantized_sl": format_step_string(quantized_sl, tick_size),
        "quantized_tp": format_step_string(quantized_tp, tick_size),
        "risk_budget_usd": dry_run.get("risk_budget_usd"),
        "estimated_risk_usd": dry_run.get("total_conservative_projected_risk_usd"),
        "details": dry_run,
    }

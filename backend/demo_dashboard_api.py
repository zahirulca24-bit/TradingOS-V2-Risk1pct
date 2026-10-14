import os
import time
import math
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pybit.unified_trading import HTTP

from demo_risk_engine import count_occupied_slots, classify_order

router = APIRouter(prefix="/demo", tags=["Demo Dashboard"])

ENV_FILE = Path(__file__).resolve().parent / ".env"

CACHE_SECONDS = 15
_cache = {"time": 0.0, "data": None}
_cache_lock = Lock()


def response_result(response):
    if not isinstance(response, dict) or response.get("retCode") != 0:
        raise RuntimeError(f"Bybit returned an unsuccessful response: {response.get('retMsg') if isinstance(response, dict) else 'non-dict'}")
    return response.get("result") or {}


def number_or_none(value):
    try:
        if value is None or value == "":
            return None
        val = float(value)
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    except (TypeError, ValueError):
        return None


def read_demo_account():
    load_dotenv(ENV_FILE, override=True)

    demo = os.getenv("BYBIT_DEMO", "").strip().lower()
    key = os.getenv("BYBIT_API_KEY", "").strip()
    secret = os.getenv("BYBIT_API_SECRET", "").strip()

    if demo not in ("true", "1", "yes", "on"):
        raise HTTPException(
            status_code=503,
            detail="Demo mode is not explicitly enabled",
        )

    if not key or not secret:
        raise HTTPException(
            status_code=503,
            detail="Demo credentials are not configured",
        )

    # Explicit DEMO connection; strictly read-only, no trade execution methods.
    session = HTTP(
        testnet=False,
        demo=True,
        api_key=key,
        api_secret=secret,
        recv_window=10000,
    )

    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        wallet_result = response_result(
            session.get_wallet_balance(accountType="UNIFIED")
        )
        accounts = wallet_result.get("list") or []

        if not accounts:
            raise RuntimeError("No wallet account returned")

        account = accounts[0]

    except Exception as exc:
        # Never expose authentication response or secret information.
        raise HTTPException(
            status_code=503,
            detail="Demo wallet connection failed",
        ) from exc

    raw_equity = number_or_none(account.get("totalEquity"))
    raw_balance = number_or_none(account.get("totalAvailableBalance"))

    equity_valid = (
        raw_equity is not None
        and raw_equity > 0
        and raw_equity < 100_000_000_000
    )

    result = {
        "connected": True,
        "environment": "BYBIT_DEMO",
        "account_type": account.get("accountType", "UNIFIED"),
        "total_equity": raw_equity if equity_valid else None,
        "available_balance": raw_balance,
        "equity_unit": "USD",
        "risk_percent": 1.0,
        "risk_budget_usd": round(raw_equity * 0.01, 4) if equity_valid else None,
        "max_open_and_pending": 3,
        "open_positions": None,
        "open_positions_list": [],
        "pending_orders": None,
        "pending_entry_orders": None,
        "occupied_slots": None,
        "remaining_slots": None,
        "positions_error": False,
        "orders_error": False,
        "orders_first_page_only": False,
        "trading_enabled": False,
        "real_trading_enabled": False,
        "execution_status": "DISABLED / NO ORDERS",
        "risk_health": "UNAVAILABLE",
        "updated_at": now_iso,
    }

    # 1. Paginating Positions
    positions = []
    try:
        cursor = ""
        seen_cursors = set()
        while True:
            params = {"category": "linear", "settleCoin": "USDT", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            pos_resp = response_result(session.get_positions(**params))
            items = pos_resp.get("list") or []
            positions.extend(items)
            next_cursor = pos_resp.get("nextPageCursor") or ""
            if not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise RuntimeError("Repeated positions pagination cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        # Deduplicate active positions by (symbol, positionIdx)
        dedup_positions = {}
        for p in positions:
            size_val = number_or_none(p.get("size")) or 0
            if size_val > 0:
                sym = str(p.get("symbol") or "").strip().upper()
                pos_idx = str(p.get("positionIdx", "0")).strip()
                if sym:
                    key = (sym, pos_idx)
                    dedup_positions[key] = {
                        "symbol": sym,
                        "position_idx": pos_idx,
                        "size": size_val,
                        "side": p.get("side"),
                        "entry_price": number_or_none(p.get("avgPrice")),
                    }
        active_positions_list = list(dedup_positions.values())
        result["open_positions"] = len(active_positions_list)
        result["open_positions_list"] = active_positions_list

    except Exception:
        result["positions_error"] = True
        result["open_positions"] = None

    # 2. Paginating Orders
    orders = []
    has_ambiguity = False
    try:
        cursor = ""
        seen_cursors = set()
        while True:
            params = {"category": "linear", "settleCoin": "USDT", "limit": 50}
            if cursor:
                params["cursor"] = cursor
            ord_resp = response_result(session.get_open_orders(**params))
            items = ord_resp.get("list") or []
            orders.extend(items)
            next_cursor = ord_resp.get("nextPageCursor") or ""
            if not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise RuntimeError("Repeated orders pagination cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        result["pending_orders"] = len(orders)

        # Deduplicate entry orders by orderId; treat ambiguous metadata as unsafe
        dedup_entry_orders = {}
        anon_entries = 0
        for o in orders:
            cat, reason = classify_order(o)
            if cat == 'AMBIGUOUS':
                has_ambiguity = True
                break
            elif cat == 'ENTRY':
                oid = o.get("orderId")
                if oid:
                    dedup_entry_orders[str(oid)] = o
                else:
                    anon_entries += 1

        if has_ambiguity:
            result["orders_error"] = True
            result["pending_entry_orders"] = None
        else:
            result["pending_entry_orders"] = len(dedup_entry_orders) + anon_entries

    except Exception:
        result["orders_error"] = True
        result["pending_orders"] = None
        result["pending_entry_orders"] = None

    # 3. Slot calculations and Health Evaluation
    if not result["positions_error"] and not result["orders_error"] and not has_ambiguity:
        pos_cnt = result["open_positions"] if result["open_positions"] is not None else 0
        ent_cnt = result["pending_entry_orders"] if result["pending_entry_orders"] is not None else 0
        occupied = pos_cnt + ent_cnt
        result["occupied_slots"] = occupied
        result["remaining_slots"] = max(0, 3 - occupied)
    else:
        result["occupied_slots"] = None
        result["remaining_slots"] = None

    if not equity_valid:
        result["risk_health"] = "UNAVAILABLE"
    elif result["positions_error"] or result["orders_error"] or has_ambiguity:
        result["risk_health"] = "DEGRADED"
    else:
        result["risk_health"] = "VALID"

    return result


@router.get("/account")
def get_demo_account():
    """Return cached read-only demo account information."""
    with _cache_lock:
        if (
            _cache["data"] is not None
            and time.monotonic() - _cache["time"] < CACHE_SECONDS
        ):
            return _cache["data"]

        data = read_demo_account()
        _cache["data"] = data
        _cache["time"] = time.monotonic()
        return data

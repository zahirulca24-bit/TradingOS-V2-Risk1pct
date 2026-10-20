"""TradingOS V2 Bybit Closed PnL Tracking and Notification Engine.
Queries Bybit Demo closed positions, calculates realized profit/loss,
dispatches Telegram exit outcome alerts, and provides trade records to the UI.
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pybit.unified_trading import HTTP
from telegram_notifier import send_telegram_message

ENV_FILE = Path(__file__).resolve().parent / ".env"

# In-memory notified cache to prevent duplicate exit messages across polling loops
_NOTIFIED_CLOSED_ORDERS = set()


def get_bybit_session() -> Optional[HTTP]:
    key = os.getenv("BYBIT_API_KEY", "").strip()
    secret = os.getenv("BYBIT_API_SECRET", "").strip()
    if not key or not secret:
        return None
    try:
        return HTTP(demo=True, api_key=key, api_secret=secret, recv_window=10000)
    except Exception as exc:
        print(f"[CLOSED_PNL] Failed to initialize Bybit session: {exc}", flush=True)
        return None


def fetch_bybit_closed_pnl(limit: int = 50) -> List[Dict[str, Any]]:
    """Fetch closed position PnL records directly from Bybit Demo."""
    session = get_bybit_session()
    if not session:
        return []

    try:
        resp = session.get_closed_pnl(category="linear", limit=min(limit, 100))
        if not isinstance(resp, dict) or resp.get("retCode") != 0:
            print(f"[CLOSED_PNL] Bybit API error: {resp.get('retMsg') if isinstance(resp, dict) else resp}", flush=True)
            return []

        raw_list = resp.get("result", {}).get("list", [])
        records = []
        for item in raw_list:
            closed_pnl = float(item.get("closedPnl") or 0.0)
            avg_entry = float(item.get("avgEntryPrice") or 0.0)
            avg_exit = float(item.get("avgExitPrice") or 0.0)
            qty = item.get("qty") or "0"
            created_time_ms = int(item.get("createdTime") or 0)
            
            created_iso = (
                datetime.fromtimestamp(created_time_ms / 1000, tz=timezone.utc).isoformat()
                if created_time_ms > 0
                else datetime.now(timezone.utc).isoformat()
            )

            records.append({
                "order_id": item.get("orderId"),
                "symbol": item.get("symbol"),
                "side": item.get("side"),
                "qty": qty,
                "entry_price": avg_entry,
                "exit_price": avg_exit,
                "closed_pnl": closed_pnl,
                "pnl_percentage": round((closed_pnl / (avg_entry * float(qty) + 1e-9)) * 100, 2) if avg_entry > 0 else 0.0,
                "fill_count": item.get("fillCount"),
                "leverage": item.get("leverage"),
                "created_at": created_iso,
            })
        return records
    except Exception as exc:
        print(f"[CLOSED_PNL] Exception fetching closed PnL: {exc}", flush=True)
        return []


def check_and_notify_closed_trades() -> List[Dict[str, Any]]:
    """Check Bybit for newly closed positions and dispatch Telegram outcome alert."""
    records = fetch_bybit_closed_pnl(limit=10)
    newly_notified = []

    for r in records:
        oid = r.get("order_id")
        if not oid or oid in _NOTIFIED_CLOSED_ORDERS:
            continue

        _NOTIFIED_CLOSED_ORDERS.add(oid)
        newly_notified.append(r)

        # Dispatch Telegram Trade Outcome message
        sym = r.get("symbol", "")
        side = r.get("side", "")
        pnl = r.get("closed_pnl", 0.0)
        entry = r.get("entry_price", 0.0)
        exit_p = r.get("exit_price", 0.0)
        qty = r.get("qty", "")

        is_win = pnl >= 0
        status_icon = "🟢 WON (PROFIT)" if is_win else "🔴 LOST (STOP LOSS)"
        pnl_formatted = f"+${pnl:,.2f}" if is_win else f"-${abs(pnl):,.2f}"

        msg = (
            f"🏁 <b>BYBIT DEMO TRADE CLOSED</b> 🏁\n\n"
            f"🪙 <b>Pair:</b> #{sym} ({side})\n"
            f"📊 <b>Outcome:</b> <b>{status_icon}</b>\n"
            f"💰 <b>Realized PnL:</b> <code>{pnl_formatted} USD</code>\n"
            f"📍 <b>Avg Entry:</b> <code>${entry:,.4f}</code>\n"
            f"🏁 <b>Avg Exit:</b> <code>${exit_p:,.4f}</code>\n"
            f"📦 <b>Closed Qty:</b> <code>{qty}</code>\n"
            f"🆔 <b>Order ID:</b> <code>{oid}</code>\n\n"
            f"🌐 <b>Account:</b> Bybit Demo Account"
        )
        send_telegram_message(msg, parse_mode="HTML")

    return newly_notified

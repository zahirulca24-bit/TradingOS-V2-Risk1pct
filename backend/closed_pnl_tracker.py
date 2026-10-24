"""TradingOS V2 Bybit Closed PnL Tracking and Notification Engine.

Queries Bybit Demo closed positions, computes realized profit/loss,
persists closed trades durably in database, feeds records to daily risk breaker,
and queues/dispatches notifications via persistent notification outbox.
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from pybit.unified_trading import HTTP
from telegram_notifier import send_telegram_message
from db_adapter import (
    save_closed_trade,
    get_closed_trades,
    queue_notification,
    get_pending_notifications,
    mark_notification_sent,
    mark_notification_failed,
    mark_closed_trade_notified,
    feed_closed_trades_to_daily_breaker,
)

ENV_FILE = Path(__file__).resolve().parent / ".env"


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


def generate_close_id(item: Dict[str, Any]) -> str:
    """Generate a deterministic, exchange-verified composite close identifier."""
    if item.get("close_id"):
        return str(item["close_id"])
    
    order_id = str(item.get("orderId") or item.get("order_id") or "")
    exec_id = str(item.get("execId") or item.get("id") or item.get("exec_id") or "")
    if exec_id and order_id:
        return f"{order_id}_{exec_id}"

    created_time = item.get("createdTime") or item.get("updatedTime") or item.get("exchange_created_at") or 0
    closed_pnl = item.get("closedPnl") if item.get("closedPnl") is not None else item.get("closed_pnl", 0.0)
    qty = item.get("qty", "0")
    return f"{order_id}_{created_time}_{closed_pnl}_{qty}"


def fetch_bybit_closed_pnl(
    limit: int = 50,
    session: Optional[HTTP] = None,
) -> List[Dict[str, Any]]:
    """Fetch closed position PnL records directly from Bybit Demo with stable composite IDs."""
    sess = session or get_bybit_session()
    if not sess:
        return []

    try:
        resp = sess.get_closed_pnl(category="linear", limit=min(limit, 100))
        if not isinstance(resp, dict) or resp.get("retCode") != 0:
            print(f"[CLOSED_PNL] Bybit API error: {resp.get('retMsg') if isinstance(resp, dict) else resp}", flush=True)
            return []

        raw_list = resp.get("result", {}).get("list", [])
        records = []
        for item in raw_list:
            closed_pnl = float(item.get("closedPnl") or 0.0)
            avg_entry = float(item.get("avgEntryPrice") or 0.0)
            avg_exit = float(item.get("avgExitPrice") or 0.0)
            qty = str(item.get("qty") or "0")
            fill_count = int(item.get("fillCount") or 1)
            fee = float(item.get("cumExecFee") or item.get("fee") or item.get("execFee") or 0.0)
            created_time_ms = int(item.get("createdTime") or 0)
            updated_time_ms = int(item.get("updatedTime") or created_time_ms)
            
            created_iso = (
                datetime.fromtimestamp(created_time_ms / 1000, tz=timezone.utc).isoformat()
                if created_time_ms > 0
                else datetime.now(timezone.utc).isoformat()
            )
            updated_iso = (
                datetime.fromtimestamp(updated_time_ms / 1000, tz=timezone.utc).isoformat()
                if updated_time_ms > 0
                else created_iso
            )

            is_partial = 1 if (item.get("isPartial") or item.get("is_partial") or fill_count > 1) else 0
            close_id = generate_close_id(item)

            records.append({
                "close_id": close_id,
                "order_id": item.get("orderId") or item.get("order_id"),
                "order_link_id": item.get("orderLinkId") or item.get("order_link_id"),
                "symbol": item.get("symbol"),
                "side": item.get("side"),
                "qty": qty,
                "entry_price": avg_entry,
                "exit_price": avg_exit,
                "closed_pnl": closed_pnl,
                "fee": fee,
                "is_partial": is_partial,
                "pnl_percentage": round((closed_pnl / (avg_entry * float(qty) + 1e-9)) * 100, 2) if avg_entry > 0 else 0.0,
                "fill_count": fill_count,
                "leverage": str(item.get("leverage") or ""),
                "exchange_created_at": created_iso,
                "exchange_updated_at": updated_iso,
                "created_at": created_iso,
            })
        return records
    except Exception as exc:
        print(f"[CLOSED_PNL] Exception fetching closed PnL: {exc}", flush=True)
        return []


def format_closed_trade_alert(trade: Dict[str, Any]) -> str:
    """Build formatted HTML Telegram message for a closed trade event."""
    sym = trade.get("symbol", "")
    side = trade.get("side", "")
    pnl = float(trade.get("closed_pnl") or 0.0)
    entry = float(trade.get("entry_price") or 0.0)
    exit_p = float(trade.get("exit_price") or 0.0)
    qty = trade.get("qty", "")
    oid = trade.get("order_id", "")
    close_id = trade.get("close_id", "")
    fee = float(trade.get("fee") or 0.0)
    is_partial = bool(trade.get("is_partial"))

    is_win = pnl >= 0
    status_icon = "🟢 WON (PROFIT)" if is_win else "🔴 LOST (STOP LOSS)"
    pnl_formatted = f"+${pnl:,.2f}" if is_win else f"-${abs(pnl):,.2f}"

    lines = [
        "🏁 <b>BYBIT DEMO TRADE CLOSED</b> 🏁",
        "",
        f"🪙 <b>Pair:</b> #{sym} ({side})",
        f"📊 <b>Outcome:</b> <b>{status_icon}</b>",
        f"💰 <b>Realized PnL:</b> <code>{pnl_formatted} USD</code>",
    ]
    if fee > 0:
        lines.append(f"💸 <b>Fee:</b> <code>${fee:,.4f} USD</code>")
    lines.extend([
        f"📍 <b>Avg Entry:</b> <code>${entry:,.4f}</code>",
        f"🏁 <b>Avg Exit:</b> <code>${exit_p:,.4f}</code>",
        f"📦 <b>Closed Qty:</b> <code>{qty}</code>",
    ])
    if is_partial:
        lines.append("⚡ <b>Execution:</b> Partial Close")
    lines.extend([
        f"🆔 <b>Order ID:</b> <code>{oid}</code>",
        f"🔑 <b>Close ID:</b> <code>{close_id}</code>",
        "",
        "🌐 <b>Account:</b> Bybit Demo Account"
    ])
    return "\n".join(lines)


def sync_and_persist_closed_trades(
    session: Optional[HTTP] = None,
    limit: int = 50,
    dsn: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """Query Bybit, durably persist closed trades, enqueue outbox alerts, and feed daily breaker."""
    records = fetch_bybit_closed_pnl(limit=limit, session=session)
    synced = []

    for r in records:
        # Save closed trade durably (raises if database failure occurs)
        saved = save_closed_trade(r, dsn=dsn, db_path=db_path)
        synced.append(saved)

        # If not previously notified, queue into durable notification outbox
        if saved.get("_is_new") or saved.get("notified") == 0:
            event_key = f"closed_trade_{r['close_id']}"
            msg_payload = format_closed_trade_alert(r)
            queue_notification(
                event_key=event_key,
                event_type="TRADE_CLOSED",
                payload=msg_payload,
                max_retries=5,
                dsn=dsn,
                db_path=db_path,
            )

    # Feed un-fed closed trade realized PnL into the daily risk breaker
    feed_closed_trades_to_daily_breaker(dsn=dsn, db_path=db_path)

    return synced


def dispatch_pending_notifications(
    send_fn: Optional[Callable[..., Any]] = None,
    max_batch: int = 20,
    dsn: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> int:
    """Dispatch pending notifications from outbox and update durable delivery state."""
    dispatcher = send_fn or send_telegram_message
    pending = get_pending_notifications(limit=max_batch, dsn=dsn, db_path=db_path)
    dispatched_count = 0

    for item in pending:
        outbox_id = item["id"]
        payload = item["payload"]
        event_key = item.get("event_key", "")

        try:
            try:
                result = dispatcher(payload, parse_mode="HTML")
            except TypeError:
                result = dispatcher(payload)

            if result is False:
                mark_notification_failed(outbox_id, "Notification dispatcher returned False", dsn=dsn, db_path=db_path)
            else:
                mark_notification_sent(outbox_id, dsn=dsn, db_path=db_path)
                if event_key.startswith("closed_trade_"):
                    close_id = event_key.replace("closed_trade_", "", 1)
                    mark_closed_trade_notified(close_id, dsn=dsn, db_path=db_path)
                dispatched_count += 1
        except Exception as exc:
            mark_notification_failed(outbox_id, str(exc), dsn=dsn, db_path=db_path)

    return dispatched_count


def check_and_notify_closed_trades(
    session: Optional[HTTP] = None,
    limit: int = 50,
    send_fn: Optional[Callable[..., Any]] = None,
    dsn: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """Poll Bybit for newly closed positions, persist them durably, and dispatch alerts."""
    synced = sync_and_persist_closed_trades(session=session, limit=limit, dsn=dsn, db_path=db_path)
    dispatch_pending_notifications(send_fn=send_fn, dsn=dsn, db_path=db_path)
    return synced

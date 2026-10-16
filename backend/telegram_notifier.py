"""TradingOS V2 Telegram Notification Dispatcher.
Strictly read-only / research evidence delivery. Zero order execution.
Sends:
1. Trade Signal Ready alerts (when 15m sweep + pin bar/engulfing + R:R >= 2 qualifies).
2. 15-minute Scan Cycle summary.
3. Scanner Failure alerts (when market validation or scanner pipeline fails).
"""

import json
import os
from pathlib import Path
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _get_credentials():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        # Fallback to backend/.env if running locally
        try:
            from dotenv import dotenv_values
            env_path = Path(__file__).resolve().parent / ".env"
            if env_path.is_file():
                vals = dotenv_values(env_path)
                token = token or vals.get("TELEGRAM_BOT_TOKEN")
                chat_id = chat_id or vals.get("TELEGRAM_CHAT_ID")
        except Exception:
            pass
    return token, chat_id


def send_telegram_message(text: str, parse_mode: str = "Markdown") -> bool:
    """Send a telegram message safely without raising exceptions to callers."""
    token, chat_id = _get_credentials()
    if not token or not chat_id:
        return False

    payload = {
        "chat_id": str(chat_id).strip(),
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    data = urllib.parse.urlencode(payload).encode("utf-8")
    url = f"https://api.telegram.org/bot{token.strip()}/sendMessage"
    req = urllib.request.Request(url, data=data, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            return bool(res.get("ok"))
    except Exception as exc:
        print("WARNING: Telegram message delivery failed:", type(exc).__name__, exc, flush=True)
        return False


def notify_cycle_published(reports: Dict[str, Any]):
    """Dispatches 15m scan summary and any ready trade setup alerts."""
    scanner = reports.get("scanner", {})
    plans = reports.get("trade_plans", {})
    ranked = reports.get("opportunities", {})

    cycle_id = scanner.get("cycle_id", "Unknown")
    close_utc = scanner.get("candle_close_utc", "")
    total_scanned = scanner.get("universe_examined") or scanner.get("total_scanned", 0)
    directional = len(scanner.get("results", []))
    ready_count = plans.get("ready_count", 0)

    # 1. Dispatch Signal Ready alerts first (High Priority)
    if ready_count > 0:
        for plan in plans.get("results", []):
            if plan.get("status") == "PLAN_READY":
                sym = plan.get("symbol", "")
                direction = plan.get("direction", "LONG")
                trend = plan.get("trend", "")
                pattern = plan.get("pattern", "SWEEP_CONFIRMED")
                entry = plan.get("entry_reference", 0)
                sl = plan.get("sl", 0)
                tp = plan.get("tp", 0)
                rr = plan.get("rr", 0)

                icon = "🟢" if direction == "LONG" else "🔴"

                signal_msg = (
                    f"🎯 *TRADINGOS V2 — TRADE SIGNAL READY* 🎯\n\n"
                    f"🪙 *Pair:* #{sym} ({direction} {icon})\n"
                    f"📈 *1H Trend:* {trend}\n"
                    f"🔍 *Pattern:* {pattern}\n"
                    f"⏱️ *Timeframe:* 15m Candle Close ({close_utc})\n\n"
                    f"📍 *Entry Reference:* `${entry:,.4f}`\n"
                    f"🛑 *Stop Loss:* `${sl:,.4f}`\n"
                    f"🎯 *Take Profit:* `${tp:,.4f}`\n"
                    f"⚖️ *Risk / Reward:* `1 : {rr:.2f}`\n\n"
                    f"🛡️ *Risk Engine:* 1% Max Account Risk Budget\n"
                    f"⚠️ *Notice:* Read-Only Research Signal (No execution)"
                )
                send_telegram_message(signal_msg)

    # 2. Dispatch 15-Minute Scan Summary
    top_items = ranked.get("top_10", [])[:4]
    top_text = ""
    for i, itm in enumerate(top_items, 1):
        s = itm.get("symbol", "")
        d = itm.get("direction", "")
        stat = itm.get("status", "")
        top_text += f"{i}. `#{s}` ({d}) — {stat}\n"

    summary_msg = (
        f"📊 *TradingOS 15m Scan Update* 📊\n\n"
        f"⏱️ *Cycle Close:* `{cycle_id}`\n"
        f"🔍 *Examined:* {total_scanned} Coins | *Directional:* {directional}\n"
        f"🎯 *Trade Ready Signals:* {ready_count}\n\n"
        f"*🏆 Top Monitored Assets:*\n"
        f"{top_text or 'No active directional assets.'}\n"
        f"🔗 *Dashboard:* https://trading-os-v2-risk1pct.vercel.app"
    )
    send_telegram_message(summary_msg)


def notify_scan_failed(reason: str):
    """Notifies when a scan cycle fails or is aborted."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    # Clean up error string to avoid overly long messages
    short_reason = str(reason).strip()[:300]
    msg = (
        f"⚠️ *TradingOS Alert — Scan Cycle Failed* ⚠️\n\n"
        f"⏱️ *Time:* `{now_iso}`\n"
        f"❌ *Reason:* `{short_reason}`\n\n"
        f"ℹ️ *Action:* Will retry on the next 15-minute candle close."
    )
    send_telegram_message(msg)

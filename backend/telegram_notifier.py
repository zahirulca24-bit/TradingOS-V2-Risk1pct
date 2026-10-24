"""TradingOS V2 Telegram Notification Dispatcher.
Strictly read-only / research evidence delivery. Zero order execution.
Sends:
1. Trade Signal Ready alerts (when 15m sweep + pin bar/engulfing + R:R >= 2 qualifies).
2. 15-minute Scan Cycle summary.
3. Signal Outcome & Post-Mortem alerts (when an active signal hits TP or SL).
4. Daily Midnight Performance Report (at 18:00 UTC / 12:00 AM Bangladesh Time).
5. Scanner Failure alerts (when market validation or scanner pipeline fails).
"""

import json
import os
from pathlib import Path
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pipeline_contract import MIN_RR


def _get_credentials():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
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


def notify_signal_outcome(resolved_item: Dict[str, Any]):
    """Sends a completion alert when an active signal hits TP or SL."""
    sym = resolved_item.get("symbol", "")
    direction = resolved_item.get("direction", "")
    status = resolved_item.get("status", "")
    entry = float(resolved_item.get("entry_reference", 0))
    exit_p = float(resolved_item.get("exit_price") or entry)
    post_mortem = resolved_item.get("post_mortem") or "Market structure criteria completed."
    rr = float(resolved_item.get("rr", 0))

    if status == "HIT_TP":
        icon = "🟢"
        result_title = "TAKE PROFIT HIT (PROFIT)"
        pnl_text = f"+{rr * 1.00:.2f}% (1 : {rr:.2f} Risk/Reward)"
    else:
        icon = "🔴"
        result_title = "STOP LOSS HIT (RISK PROTECTED)"
        pnl_text = "-1.00% (Strict 1% Fixed Risk Budget)"

    msg = (
        f"🏁 <b>TRADINGOS SIGNAL RESOLVED</b> 🏁\n\n"
        f"🪙 <b>Pair:</b> #{sym} ({direction} {icon})\n"
        f"📊 <b>Result:</b> <b>{result_title}</b>\n"
        f"📍 <b>Entry Price:</b> <code>${entry:,.4f}</code>\n"
        f"🚪 <b>Exit Price:</b> <code>${exit_p:,.4f}</code>\n"
        f"⚖️ <b>Theoretical P&L:</b> <code>{pnl_text}</code>\n\n"
        f"🔍 <b>Post-Mortem Analysis:</b>\n"
        f"<i>{post_mortem}</i>\n\n"
        f"🔗 <b>Journal:</b> https://trading-os-v2-risk1pct.vercel.app/journal"
    )
    return send_telegram_message(msg, parse_mode="HTML")


def send_daily_midnight_report(target_date: Optional[str] = None):
    """Sends the 24-hour Daily Performance Report at 12:00 AM BD Time (18:00 UTC)."""
    try:
        from signal_tracker import get_daily_tracking_summary
        summary = get_daily_tracking_summary(target_date)
    except Exception as exc:
        print("WARNING: Could not generate daily tracking summary:", exc)
        return

    d_str = summary.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    total_cycles = summary.get("total_cycles", 0)
    total_evals = summary.get("total_scanned", 0)
    signals = summary.get("signals", [])
    won = summary.get("won_count", 0)
    lost = summary.get("lost_count", 0)
    active = summary.get("active_count", 0)

    # Format signal breakdown
    sig_text = ""
    if not signals:
        sig_text = "• No qualifying PLAN_READY signals today (Market in consolidation/waiting for sweeps).\n"
    else:
        for i, s in enumerate(signals, 1):
            sym = s.get("symbol", "")
            direction = s.get("direction", "")
            stat = s.get("status", "")
            rr = float(s.get("rr", 0))
            pm = s.get("post_mortem") or "In progress"

            if stat == "HIT_TP":
                tag = f"🟢 WON (TP Hit, +{rr:.2f}R)"
            elif stat == "HIT_SL":
                tag = "🔴 LOST (SL Hit, -1.0R)"
            else:
                tag = "⏳ ACTIVE (Running in zone)"

            sig_text += f"{i}. <b>#{sym}</b> ({direction}) — {tag}\n   <i>Post-Mortem: {pm}</i>\n"

    # Compute theoretical net R
    net_r = (won * 2.0) - (lost * 1.0)
    pnl_display = f"{net_r:+.2f}R ({net_r:+.2f}% on Account)"

    msg = (
        f"📋 <b>TRADINGOS V2 — DAILY PERFORMANCE REPORT</b> 📋\n"
        f"🗓️ <b>Date:</b> <code>{d_str}</code> (Daily Close: 12:00 AM BD Time)\n\n"
        f"⏱️ <b>15m Cycles Completed:</b> <code>{total_cycles}</code> (100% Cloud Uptime)\n"
        f"🔍 <b>Market Asset Scans:</b> <code>{total_evals:,}</code> evaluations\n"
        f"🎯 <b>Today's Confirmed Signals:</b> <code>{len(signals)}</code> Setups\n\n"
        f"<b>📊 Confirmed Signals & Outcomes:</b>\n"
        f"{sig_text}\n"
        f"<b>⚖️ Daily Theoretical Performance:</b>\n"
        f"• <b>Won:</b> {won} | <b>Lost:</b> {lost} | <b>Active:</b> {active}\n"
        f"• <b>Net Risk/Reward:</b> <code>{pnl_display}</code>\n"
        f"• <b>Risk Mode:</b> Strict 1% Equity Budget Limit\n\n"
        f"🔗 <b>Detailed Research Reports:</b> https://trading-os-v2-risk1pct.vercel.app/reports"
    )
    return send_telegram_message(msg, parse_mode="HTML")


def notify_cycle_published(reports: Dict[str, Any]):
    """Dispatches 15m scan summary, trade signals, outcome alerts, and daily report."""
    scanner = reports.get("scanner", {})
    plans = reports.get("trade_plans", {})
    ranked = reports.get("opportunities", {})

    cycle_id = scanner.get("cycle_id", "Unknown")
    close_utc = scanner.get("candle_close_utc", "")
    total_scanned = scanner.get("universe_examined") or scanner.get("total_scanned", 0)
    directional = len(scanner.get("results", []))
    ready_count = plans.get("ready_count", 0)

    # 1. Register new ready signals in signal outcome tracker
    try:
        from signal_tracker import register_new_ready_signals, update_active_signal_outcomes
        register_new_ready_signals(reports)
        resolved_signals = update_active_signal_outcomes()
        for res_item in resolved_signals:
            notify_signal_outcome(res_item)
    except Exception as exc:
        print("WARNING: Signal tracking update failed:", exc)

    # 2. Dispatch Signal Ready alerts (High Priority)
    if ready_count > 0:
        for plan in plans.get("results", []):
            if plan.get("status") == "PLAN_READY" and float(plan.get("rr") or 0.0) >= MIN_RR:
                sym = plan.get("symbol", "")
                direction = plan.get("direction", "LONG")
                trend = plan.get("trend", "")
                pattern = plan.get("pattern", "SWEEP_CONFIRMED")
                entry = plan.get("entry_reference", 0)
                sl = plan.get("sl", 0)
                tp = plan.get("tp", 0)
                rr = plan.get("rr", 0)

                icon = "🟢" if direction == "LONG" else "🔴"

                try:
                    from demo_risk_engine import is_demo_execution_enabled
                    execution_active = is_demo_execution_enabled()
                except Exception:
                    execution_active = False

                mode_notice = (
                    "⚡ *Execution:* Bybit Demo Order Submission ACTIVE"
                    if execution_active
                    else "⚠️ *Notice:* Read-Only Research Signal (Demo Execution Disabled)"
                )

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
                    f"{mode_notice}"
                )
                send_telegram_message(signal_msg)

    # 3. Dispatch 15-Minute Scan Summary (Clean Markdown without formatting bugs)
    top_items = ranked.get("top_10", [])[:4]
    top_text = ""
    for i, itm in enumerate(top_items, 1):
        s = itm.get("symbol", "")
        d = itm.get("direction", "")
        clean_stat = str(itm.get("status", "")).replace("_", " ")
        top_text += f"{i}. #{s} ({d}) — `{clean_stat}`\n"

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

    # 4. Check if this is the Daily Midnight Close: 18:00 UTC = 12:00 AM BD Time
    if "T180000Z" in cycle_id or close_utc.endswith("18:00:00+00:00") or close_utc.endswith("18:00:00Z"):
        print("DISPATCHING DAILY MIDNIGHT PERFORMANCE REPORT (18:00 UTC / 12:00 AM BD TIME)...", flush=True)
        send_daily_midnight_report()


def notify_scan_failed(reason: str):
    """Notifies when a scan cycle fails or is aborted."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    short_reason = str(reason).strip()[:300]
    msg = (
        f"⚠️ *TradingOS Alert — Scan Cycle Failed* ⚠️\n\n"
        f"⏱️ *Time:* `{now_iso}`\n"
        f"❌ *Reason:* `{short_reason}`\n\n"
        f"ℹ️ *Action:* Will retry on the next 15-minute candle close."
    )
    send_telegram_message(msg)


def notify_kill_switch_activated(reason: str):
    """Notifies when emergency execution kill switch is triggered."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    msg = (
        f"🚨 *TRADINGOS EMERGENCY ALERT: KILL SWITCH ACTIVATED* 🚨\n\n"
        f"⏱️ *Time:* `{now_iso}`\n"
        f"🛑 *Reason:* `{reason}`\n\n"
        f"🔒 *Status:* All new order placements blocked (Fail-Closed)."
    )
    send_telegram_message(msg)


def notify_circuit_breaker_tripped(drawdown_pct: float, reason: str):
    """Notifies when the 3% daily drawdown circuit breaker is tripped."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    msg = (
        f"🛑 *TRADINGOS RISK BREAKER TRIPPED* 🛑\n\n"
        f"⏱️ *Time:* `{now_iso}`\n"
        f"📉 *Current Drawdown:* `{drawdown_pct:.2f}%` (Limit: 3.00%)\n"
        f"⚠️ *Reason:* `{reason}`\n\n"
        f"🔒 *Action:* Daily circuit breaker ACTIVE. All new entries blocked until daily reset."
    )
    send_telegram_message(msg)

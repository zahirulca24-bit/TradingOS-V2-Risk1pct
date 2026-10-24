"""TradingOS Phase 2: one candle-aligned, read-only, crypto-only scan cycle.

No pybit, private Bybit credentials, order placement or Telegram calls.
"""

import argparse
import time
from datetime import datetime, timezone

from crypto_universe import get_crypto_tickers
from liquidity_sweep import detect_liquidity_sweep
from market_structure import detect_trend
from opportunity_ranker import classify_opportunity, select_zones, valid_candles, MAX_TOP, ZONE_WIDTH_PCT, NEAR_ZONE_PCT, WATCH_ZONE_PCT
from pipeline_contract import last_closed_boundary
from pipeline_store import MANIFEST, publish, read_committed
from trade_plan import build_trade_plan
from universe_candles import fetch_closed_candles

TARGET_DIRECTIONAL = 50
MAX_EXAMINED = 140
WAIT_AFTER_CLOSE_SECONDS = 20
CANDLE_SECONDS = 900

MAX_CONSECUTIVE_API_FAILURES = 5
MAX_ISOLATED_FAILURES = 15
MIN_EXAMINED_COVERAGE = 15


class DataValidationError(RuntimeError):
    """Raised when candle data for an isolated symbol fails integrity or freshness validation."""
    pass


class StaleCandleError(DataValidationError):
    """Raised when 15m or 1H candle close timestamp belongs to wrong/stale cycle."""
    pass


class InsufficientHistoryError(DataValidationError):
    """Raised when not enough closed candles are available for a symbol."""
    pass


class InvalidOHLCError(DataValidationError):
    """Raised when candle prices violate OHLC integrity."""
    pass


class SystemicScannerError(RuntimeError):
    """Raised when widespread or consecutive API/network failures abort the entire cycle."""
    pass


def cycle_identity(now_seconds):
    boundary = last_closed_boundary(now_seconds, CANDLE_SECONDS)
    if now_seconds < boundary + WAIT_AFTER_CLOSE_SECONDS:
        raise RuntimeError("Wait 20 seconds after the 15m UTC candle close")
    moment = datetime.fromtimestamp(boundary, tz=timezone.utc)
    return boundary, moment.strftime("%Y%m%dT%H%M%SZ"), moment.isoformat()


def validated_pair(symbol, boundary):
    candles_1h = valid_candles(fetch_closed_candles(symbol, "60"))
    candles_15m = valid_candles(fetch_closed_candles(symbol, "15"))
    if len(candles_1h) < 10 or len(candles_15m) < 10:
        raise InsufficientHistoryError(f"Not enough closed 15m/1H candles for {symbol}")
    if candles_15m[-1]["time"] + CANDLE_SECONDS != boundary:
        raise StaleCandleError(
            f"15m candle is missing or belongs to wrong cycle for {symbol} "
            f"(got {candles_15m[-1]['time'] + CANDLE_SECONDS}, expected {boundary})"
        )
    expected_hour_close = last_closed_boundary(boundary, 3600)
    if candles_1h[-1]["time"] + 3600 != expected_hour_close:
        raise StaleCandleError(
            f"1H candle is missing or belongs to wrong cycle for {symbol} "
            f"(got {candles_1h[-1]['time'] + 3600}, expected {expected_hour_close})"
        )
    return candles_1h, candles_15m


def evaluate_symbol(symbol, hourly, fifteen):
    trend = detect_trend(hourly)["trend"]
    if trend not in ("BULLISH", "BEARISH"):
        return None
    sweep = detect_liquidity_sweep(fifteen, trend)
    plan = build_trade_plan(hourly, fifteen)
    if plan.get("trend") != trend:
        raise ValueError("Trend disagreement between scanner and trade plan")
    if plan["status"] == "EXCLUDED":
        raise ValueError("Unexpected neutral plan for directional symbol")

    scan_row = {
        "symbol": symbol,
        "trend": trend,
        "status": "CANDIDATE" if sweep else "WAIT",
        "direction": sweep["direction"] if sweep else None,
        "level": sweep["level"] if sweep else None,
        "close": sweep["close"] if sweep else None,
        "candle_time": sweep["time"] if sweep else None,
    }
    plan_row = {"symbol": symbol, "trend": trend, **plan}
    latest = fifteen[-1]
    support, resistance = select_zones(fifteen, latest["close"])
    analysis = classify_opportunity(trend, latest["close"], support, resistance)
    if analysis is None:
        raise ValueError("Ranking unexpectedly excluded directional symbol")
    opportunity_row = {
        "symbol": symbol,
        "trend": trend,
        "price": latest["close"],
        "candle_time": latest["time"],
        "support": support,
        "resistance": resistance,
        **analysis,
        "trade_plan_status": plan["status"],
        "trade_plan_reason": plan["reason"],
        "entry_reference": plan.get("entry_reference"),
        "sl": plan.get("sl"),
        "tp": plan.get("tp"),
        "rr": plan.get("rr"),
        "pattern": plan.get("pattern"),
    }
    return scan_row, plan_row, opportunity_row


def sort_opportunities(rows):
    return sorted(rows, key=lambda row: (
        row["priority"],
        float("inf") if row.get("distance_pct") is None else row["distance_pct"],
        row["symbol"],
    ))


def build_reports(symbol_data, *, boundary, cycle_id, generated_at, examined, neutral, skipped_symbols=None):
    """Pure build: same input candles -> matching 3 reports."""
    scanner_rows = []
    plan_rows = []
    ranked_rows = []
    for symbol, hourly, fifteen in symbol_data:
        evaluated = evaluate_symbol(symbol, hourly, fifteen)
        if evaluated is None:
            raise ValueError("Neutral asset should have been excluded from active rows")
        scan, plan, ranked = evaluated
        scanner_rows.append(scan)
        plan_rows.append(plan)
        ranked_rows.append(ranked)

    ranked_rows = sort_opportunities(ranked_rows)
    common = {
        "cycle_id": cycle_id,
        "candle_close_utc": datetime.fromtimestamp(boundary, timezone.utc).isoformat(),
        "generated_at": generated_at,
        "status": "success",
        "failures": [],
        "trading_enabled": False,
    }
    ready = sum(row["status"] == "PLAN_READY" for row in plan_rows)
    waiting = sum(row["status"] == "WAIT" for row in plan_rows)
    rejected = sum(row["status"] == "REJECTED" for row in plan_rows)
    directional = len(scanner_rows)
    reports = {
        "scanner": {
            **common,
            "total_scanned": directional,  # Legacy UI expects displayed active rows.
            "universe_examined": examined,
            "directional_count": directional,
            "neutral_excluded": neutral,
            "candidate_count": sum(row["status"] == "CANDIDATE" for row in scanner_rows),
            "skipped_count": len(skipped_symbols or []),
            "results": scanner_rows,
        },
        "trade_plans": {
            **common,
            "source_symbols": examined,
            "directional_attempted": directional,
            "evaluated": directional,
            "neutral_or_other_excluded": neutral + len(skipped_symbols or []),
            "ready_count": ready,
            "wait_count": waiting,
            "rejected_count": rejected,
            "excluded_after_refresh": 0,
            "results": plan_rows,
        },
        "opportunities": {
            **common,
            "source_symbols": directional,
            "universe_examined": examined,
            "directional_count": directional,
            "neutral_excluded_after_refresh": neutral + len(skipped_symbols or []),
            "top_count": min(MAX_TOP, directional),
            "top_10": ranked_rows[:MAX_TOP],
            "remaining": ranked_rows[MAX_TOP:],
            "rules": {
                "zone_width_pct": ZONE_WIDTH_PCT,
                "near_zone_pct": NEAR_ZONE_PCT,
                "watch_zone_pct": WATCH_ZONE_PCT,
                "range_extension_threshold_pct": 50,
                "entry_confirmation": "Liquidity Sweep + Pin Bar OR Engulfing",
            },
        },
    }
    return reports


def run_once(*, now=None, quiet=False):
    now_seconds = time.time() if now is None else float(now)
    boundary, cycle_id, cycle_close = cycle_identity(now_seconds)
    existing = read_committed()
    if existing is not None:
        current_close = existing["manifest"]["candle_close_utc"]
        old_boundary = int(datetime.fromisoformat(current_close).timestamp())
        if old_boundary >= boundary:
            if not quiet:
                print("ALREADY COMMITTED:", existing["manifest"]["cycle_id"])
            return "already_current"

    tickers = get_crypto_tickers()
    chosen = []
    neutral = 0
    examined = 0
    skipped = []
    consecutive_api_failures = 0
    required_min_coverage = MIN_EXAMINED_COVERAGE if len(tickers) >= MIN_EXAMINED_COVERAGE else 1

    print("PHASE 2 SCAN START", cycle_id, "close", cycle_close, flush=True)
    print("Eligible crypto-only tickers:", len(tickers), "Maximum examined:", MAX_EXAMINED, flush=True)
    for coin in tickers[:MAX_EXAMINED]:
        # Do not mix the next 15m close into a slow-running cycle.
        if time.time() >= boundary + CANDLE_SECONDS:
            raise SystemicScannerError("Cycle overran next 15m close; no snapshots committed; cycle not committed")
        symbol = coin["symbol"]
        examined += 1
        trend = None
        try:
            hourly, fifteen = validated_pair(symbol, boundary)
            trend_info = detect_trend(hourly)
            trend = trend_info["trend"] if isinstance(trend_info, dict) else trend_info
            if trend in ("BULLISH", "BEARISH"):
                chosen.append((symbol, hourly, fifteen))
            else:
                neutral += 1
            # Successful validation resets consecutive API failure counter
            consecutive_api_failures = 0
            if not quiet:
                print(f"[{examined}] {symbol:15} {str(trend):8} directional={len(chosen)}", flush=True)
        except (DataValidationError, ValueError) as exc:
            # Isolated bad symbol data: stale candles, bad OHLC, or insufficient history
            cat = type(exc).__name__
            reason = str(exc)
            skipped.append({"symbol": symbol, "reason": reason, "category": cat})
            if not quiet:
                print(f"[SKIP:DATA] {symbol:15} {cat}: {reason}", flush=True)
            if len(skipped) > MAX_ISOLATED_FAILURES:
                raise SystemicScannerError(
                    f"Widespread data validation failure: {len(skipped)} symbols failed validation "
                    f"(limit {MAX_ISOLATED_FAILURES}); cycle not committed"
                )
        except Exception as exc:
            # API / network / systemic failure
            consecutive_api_failures += 1
            reason = str(exc)
            skipped.append({"symbol": symbol, "reason": reason, "category": "API_ERROR"})
            if not quiet:
                print(f"[SKIP:API]  {symbol:15} API error ({consecutive_api_failures}/{MAX_CONSECUTIVE_API_FAILURES} consecutive): {reason}", flush=True)
            if consecutive_api_failures >= MAX_CONSECUTIVE_API_FAILURES:
                raise SystemicScannerError(
                    f"Systemic Bybit API failure: {consecutive_api_failures} consecutive symbols failed API calls "
                    f"(last error: {reason}); cycle not committed"
                )
            if len(skipped) > MAX_ISOLATED_FAILURES:
                raise SystemicScannerError(
                    f"Widespread failure rate: {len(skipped)} total symbols failed (limit {MAX_ISOLATED_FAILURES}); "
                    "cycle not committed"
                )

        if len(chosen) >= TARGET_DIRECTIONAL:
            break
        time.sleep(0.10)

    valid_coverage = len(chosen) + neutral
    if valid_coverage < required_min_coverage:
        raise SystemicScannerError(
            f"Insufficient valid coverage: only {valid_coverage} valid symbols evaluated "
            f"(minimum {required_min_coverage} required); cycle not committed"
        )
    if not chosen:
        raise SystemicScannerError("No directional assets; refusing to publish empty cycle; cycle not committed")
    if time.time() >= boundary + CANDLE_SECONDS:
        raise SystemicScannerError("Cycle expired before publication; no snapshots committed; cycle not committed")

    reports = build_reports(
        chosen,
        boundary=boundary,
        cycle_id=cycle_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        examined=examined,
        neutral=neutral,
        skipped_symbols=skipped,
    )
    published_id = publish(reports)
    if not published_id or published_id != cycle_id:
        raise RuntimeError(f"Cycle publication verification failed: expected {cycle_id}, got {published_id}")
    print("PHASE 2 COMMITTED", published_id, flush=True)
    print("Examined:", examined, "Directional:", len(chosen), "Neutral:", neutral, "Skipped:", len(skipped), flush=True)
    print("Top 10:", reports["opportunities"]["top_count"], "Ready:", reports["trade_plans"]["ready_count"], flush=True)
    print("NO ORDERS PLACED", flush=True)
    return "success"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one closed-candle scan (read-only)")
    args = parser.parse_args()
    if not args.once:
        parser.error("For scheduled runs use scanner_scheduler.py; for a manual run pass --once")
    from pipeline_lock import exclusive_pipeline_lock, PipelineLockContentionError
    from pipeline_store import STATE_DIR, atomic_json
    try:
        with exclusive_pipeline_lock():
            try:
                status = run_once()
                attempt = {"status": status, "at": datetime.now(timezone.utc).isoformat()}
                atomic_json(STATE_DIR / "last_attempt.json", attempt)
                try:
                    from db_adapter import save_pipeline_last_attempt
                    save_pipeline_last_attempt(attempt)
                except Exception:
                    pass
            except Exception as exc:
                attempt = {"status": "failed", "at": datetime.now(timezone.utc).isoformat(), "reason": str(exc)[:500]}
                atomic_json(STATE_DIR / "last_attempt.json", attempt)
                try:
                    from db_adapter import save_pipeline_last_attempt
                    save_pipeline_last_attempt(attempt)
                except Exception:
                    pass
                print("ERROR:", exc)
                raise SystemExit(1)
    except PipelineLockContentionError as lock_err:
        print("SKIPPED (BUSY):", lock_err)
        raise SystemExit(0)

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from universe_candles import fetch_closed_candles
from market_structure import detect_trend

BASE = Path(__file__).resolve().parent
INPUT = BASE / "trade_plans_snapshot.json"
OUTPUT = BASE / "opportunities_snapshot.json"

LEFT = 2
RIGHT = 2
ZONE_WIDTH_PCT = 0.15
NEAR_ZONE_PCT = 0.50
WATCH_ZONE_PCT = 1.50
MAX_TOP = 10


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def valid_candles(candles):
    result = []

    for candle in candles:
        try:
            c = {
                "time": int(candle["time"]),
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
            }

            if not all(math.isfinite(v) for v in (
                c["open"], c["high"], c["low"], c["close"]
            )):
                continue

            if (
                c["high"] < max(c["open"], c["close"])
                or c["low"] > min(c["open"], c["close"])
                or c["low"] <= 0
            ):
                continue

            result.append(c)
        except (KeyError, ValueError, TypeError):
            continue

    result.sort(key=lambda c: c["time"])

    # Keep one candle per opening timestamp.
    deduped = {c["time"]: c for c in result}
    return [deduped[t] for t in sorted(deduped)]


def confirmed_swings(candles):
    highs = []
    lows = []

    # A pivot is confirmed only after two RIGHT candles.
    # Do not use the most recent signal candle as a pivot.
    for i in range(LEFT, len(candles) - RIGHT):
        candle = candles[i]

        left = candles[i - LEFT:i]
        right = candles[i + 1:i + RIGHT + 1]

        if (
            all(candle["high"] > c["high"] for c in left)
            and all(candle["high"] > c["high"] for c in right)
        ):
            highs.append({
                "price": candle["high"],
                "time": candle["time"],
                "confirmed_at": right[-1]["time"] + 15 * 60,
            })

        if (
            all(candle["low"] < c["low"] for c in left)
            and all(candle["low"] < c["low"] for c in right)
        ):
            lows.append({
                "price": candle["low"],
                "time": candle["time"],
                "confirmed_at": right[-1]["time"] + 15 * 60,
            })

    return highs, lows


def build_zone(price, kind):
    half = price * (ZONE_WIDTH_PCT / 100)

    return {
        "type": kind,
        "level": price,
        "lower": price - half,
        "upper": price + half,
    }


def distance_to_zone(price, zone):
    if zone is None:
        return None

    if zone["lower"] <= price <= zone["upper"]:
        return 0.0

    if price < zone["lower"]:
        return (zone["lower"] - price) / price * 100

    return (price - zone["upper"]) / price * 100


def select_zones(candles, price):
    # Confirmed levels must be available before the last candle.
    highs, lows = confirmed_swings(candles[:-1])

    supports = [
        s for s in lows if s["price"] <= price
    ]
    resistances = [
        s for s in highs if s["price"] >= price
    ]

    nearest_support = (
        max(supports, key=lambda s: s["price"])
        if supports else None
    )
    nearest_resistance = (
        min(resistances, key=lambda s: s["price"])
        if resistances else None
    )

    support_zone = (
        build_zone(nearest_support["price"], "SUPPORT")
        if nearest_support else None
    )

    resistance_zone = (
        build_zone(nearest_resistance["price"], "RESISTANCE")
        if nearest_resistance else None
    )

    return support_zone, resistance_zone


def classify_opportunity(trend, price, support, resistance):
    if trend == "BULLISH":
        direction = "LONG"
        target_zone = support
        opposite_zone = resistance

    elif trend == "BEARISH":
        direction = "SHORT"
        target_zone = resistance
        opposite_zone = support

    else:
        return None

    if target_zone is None:
        return {
            "direction": direction,
            "priority": 4,
            "status": "NO_VALID_ZONE",
            "distance_pct": None,
            "reason": "No confirmed zone on the trend side",
        }

    distance = distance_to_zone(price, target_zone)

    if distance == 0:
        priority = 1
        status = "ENTRY_WATCH"
        reason = (
            "Price is inside the relevant zone; "
            "sweep and candle pattern still required"
        )
    elif distance <= NEAR_ZONE_PCT:
        priority = 2
        status = "APPROACHING"
        reason = "Price is near the relevant zone"
    elif distance <= WATCH_ZONE_PCT:
        priority = 3
        status = "WATCH"
        reason = "Price is approaching but still away from zone"
    else:
        priority = 4
        status = "NOT_READY"
        reason = "Price is too far from the relevant zone"

    # Context only, not a guaranteed TP or an entry signal.
    range_progress_pct = None

    if (
        support is not None
        and resistance is not None
        and resistance["level"] > support["level"]
    ):
        span = resistance["level"] - support["level"]

        range_progress_pct = max(
            0.0,
            min(
                100.0,
                (price - support["level"]) / span * 100,
            ),
        )

        # Avoid chasing LONG after a large move up,
        # or SHORT after a large move down.
        if trend == "BULLISH" and range_progress_pct >= 50:
            priority = max(priority, 4)
            status = "MOVE_EXTENDED"
            reason = (
                "Bullish price has traveled at least "
                "50% of the support-resistance range"
            )

        if trend == "BEARISH" and range_progress_pct <= 50:
            priority = max(priority, 4)
            status = "MOVE_EXTENDED"
            reason = (
                "Bearish price has traveled at least "
                "50% of the support-resistance range"
            )

    return {
        "direction": direction,
        "priority": priority,
        "status": status,
        "distance_pct": round(distance, 4),
        "range_progress_pct": (
            round(range_progress_pct, 2)
            if range_progress_pct is not None
            else None
        ),
        "reason": reason,
    }


def rank_one(symbol):
    candles_1h = valid_candles(
        fetch_closed_candles(symbol, "60")
    )
    candles_15m = valid_candles(
        fetch_closed_candles(symbol, "15")
    )

    if len(candles_1h) < 10 or len(candles_15m) < 10:
        raise ValueError("Insufficient closed candles")

    trend = detect_trend(candles_1h)["trend"]

    if trend not in ("BULLISH", "BEARISH"):
        return None

    price = candles_15m[-1]["close"]

    support, resistance = select_zones(
        candles_15m, price
    )

    analysis = classify_opportunity(
        trend, price, support, resistance
    )

    if analysis is None:
        return None

    return {
        "symbol": symbol,
        "trend": trend,
        "price": price,
        "candle_time": candles_15m[-1]["time"],
        "support": support,
        "resistance": resistance,
        **analysis,
    }


def main():
    if not INPUT.exists():
        raise SystemExit(
            "ERROR: trade_plans_snapshot.json not found"
        )

    report = json.loads(
        INPUT.read_text(encoding="utf-8-sig")
    )

    source = report.get("results", [])

    if not isinstance(source, list):
        raise SystemExit("ERROR: Invalid trade plan results")

    # The previous report may be older.
    # We recheck the current 1H trend for every symbol.
    symbols = list(dict.fromkeys(
        row["symbol"]
        for row in source
        if isinstance(row, dict)
        and isinstance(row.get("symbol"), str)
        and row.get("trend") in ("BULLISH", "BEARISH")
    ))

    if not symbols:
        raise SystemExit("ERROR: No directional symbols to rank")

    print("TRADINGOS OPPORTUNITY RANKER V1")
    print("Source symbols:", len(symbols))
    print("No orders will be placed.\n")

    ranked = []
    failures = []
    neutral_excluded = 0

    for index, symbol in enumerate(symbols, 1):
        print(
            f"[{index}/{len(symbols)}] {symbol}",
            flush=True,
        )

        try:
            result = rank_one(symbol)

            if result is None:
                neutral_excluded += 1
                print("  -> EXCLUDED (fresh 1H neutral)")
                continue

            ranked.append(result)
            print(
                "  ->",
                result["status"],
                "/ distance:",
                result["distance_pct"],
                "%",
                flush=True,
            )

        except Exception as error:
            failures.append({
                "symbol": symbol,
                "error": str(error),
            })
            print("  -> ERROR:", error, flush=True)

    ranked.sort(
        key=lambda row: (
            row["priority"],
            float("inf")
            if row["distance_pct"] is None
            else row["distance_pct"],
            row["symbol"],
        )
    )

    output = {
        "generated_at": utc_now(),
        "source_symbols": len(symbols),
        "neutral_excluded_after_refresh": neutral_excluded,
        "directional_count": len(ranked),
        "top_count": min(MAX_TOP, len(ranked)),
        "top_10": ranked[:MAX_TOP],
        "remaining": ranked[MAX_TOP:],
        "failures": failures,
        "rules": {
            "zone_width_pct": ZONE_WIDTH_PCT,
            "near_zone_pct": NEAR_ZONE_PCT,
            "watch_zone_pct": WATCH_ZONE_PCT,
            "range_extension_threshold_pct": 50,
            "entry_confirmation": (
                "Liquidity Sweep + Pin Bar OR Engulfing"
            ),
        },
        "trading_enabled": False,
    }

    # Atomic write: readers never see half-written JSON.
    tmp = OUTPUT.with_suffix(".json.tmp")

    try:
        tmp.write_text(
            json.dumps(output, indent=2),
            encoding="utf-8",
        )
        tmp.replace(OUTPUT)
    finally:
        if tmp.exists():
            tmp.unlink()

    print("\n" + "=" * 54)
    print("RANKING COMPLETE")
    print("Directional:", len(ranked))
    print("Neutral excluded:", neutral_excluded)
    print("Top:", output["top_count"])
    print("Remaining:", len(output["remaining"]))
    print("Failures:", len(failures))

    print("\nTOP OPPORTUNITIES:")
    for position, row in enumerate(output["top_10"], 1):
        print(
            f"{position:>2}. {row['symbol']:<16}"
            f"{row['direction']:<6} "
            f"{row['status']:<16} "
            f"Zone distance: {row['distance_pct']}"
        )

    print("\nSaved:", OUTPUT)
    print("NO ORDERS PLACED")
    print("=" * 54)


if __name__ == "__main__":
    main()

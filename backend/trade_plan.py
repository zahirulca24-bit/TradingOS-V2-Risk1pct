import math
from datetime import datetime, timezone

from universe_candles import fetch_closed_candles
from market_structure import detect_trend
from liquidity_sweep import find_confirmed_levels
from pipeline_contract import MIN_RR

SL_BUFFER_PERCENT = 0.001
CANDLE_SECONDS = 15 * 60


def candle_shape(candle):
    body = abs(candle["close"] - candle["open"])
    candle_range = candle["high"] - candle["low"]

    if candle_range <= 0:
        return None

    upper_wick = candle["high"] - max(
        candle["open"], candle["close"]
    )

    lower_wick = min(
        candle["open"], candle["close"]
    ) - candle["low"]

    return body, candle_range, upper_wick, lower_wick


def detect_pattern(previous, current, direction):
    shape = candle_shape(current)

    if shape is None:
        return None

    body, candle_range, upper, lower = shape

    # Avoid classifying almost-bodyless candles as pin bars
    meaningful_body = body >= candle_range * 0.05

    if direction == "LONG":
        pin_bar = (
            meaningful_body
            and lower >= 2 * body
            and lower > upper
            and current["close"] > current["open"]
        )

        engulfing = (
            previous["close"] < previous["open"]
            and current["close"] > current["open"]
            and current["open"] <= previous["close"]
            and current["close"] >= previous["open"]
        )

    else:
        pin_bar = (
            meaningful_body
            and upper >= 2 * body
            and upper > lower
            and current["close"] < current["open"]
        )

        engulfing = (
            previous["close"] > previous["open"]
            and current["close"] < current["open"]
            and current["open"] >= previous["close"]
            and current["close"] <= previous["open"]
        )

    if engulfing:
        return "ENGULFING"

    if pin_bar:
        return "PIN_BAR"

    return None


def decision(status, reason, **details):
    return {
        "status": status,
        "reason": reason,
        **details,
    }


def build_trade_plan(candles_1h, candles_15m):
    if len(candles_1h) < 5 or len(candles_15m) < 6:
        return decision("REJECTED", "INSUFFICIENT_DATA")

    # Validate numerical integrity of signal candles
    for c in (candles_15m[-1], candles_15m[-2]):
        for k in ("open", "high", "low", "close"):
            v = c.get(k)
            if v is None or not isinstance(v, (int, float)) or math.isnan(v) or math.isinf(v):
                return decision("REJECTED", "INVALID_CANDLE_DATA")

    trend = detect_trend(candles_1h)["trend"]

    if trend == "NEUTRAL":
        return decision("EXCLUDED", "NEUTRAL_1H_STRUCTURE")

    direction = "LONG" if trend == "BULLISH" else "SHORT"

    current = candles_15m[-1]
    previous = candles_15m[-2]

    # Only use levels confirmed before the signal candle
    highs, lows = find_confirmed_levels(
        candles_15m[:-1],
        before_time=current["time"],
    )

    if direction == "LONG":
        if not lows:
            return decision("WAIT", "NO_CONFIRMED_SUPPORT", trend=trend, direction=direction, candle_time=current["time"])

        sweep_level = lows[-1]

        swept = (
            current["low"] < sweep_level
            and current["close"] > sweep_level
        )

    else:
        if not highs:
            return decision("WAIT", "NO_CONFIRMED_RESISTANCE", trend=trend, direction=direction, candle_time=current["time"])

        sweep_level = highs[-1]

        swept = (
            current["high"] > sweep_level
            and current["close"] < sweep_level
        )

    if not swept:
        return decision(
            "WAIT",
            "NO_SWEEP_ON_LAST_CLOSED_CANDLE",
            trend=trend,
            direction=direction,
            candle_time=current["time"],
            valid_after=current["time"] + CANDLE_SECONDS,
        )

    pattern = detect_pattern(previous, current, direction)

    if pattern is None:
        return decision(
            "REJECTED",
            "SWEEP_WITHOUT_REQUIRED_PATTERN",
            trend=trend,
            direction=direction,
            sweep_level=sweep_level,
            candle_time=current["time"],
        )

    # Reference entry, not a guaranteed execution price
    entry = current["close"]
    buffer = entry * SL_BUFFER_PERCENT

    if direction == "LONG":
        sl = current["low"] - buffer

        # Closest confirmed resistance above entry
        targets = [price for price in highs if price > entry]
        tp = min(targets) if targets else None

        risk = entry - sl
        reward = tp - entry if tp is not None else 0

    else:
        sl = current["high"] + buffer

        # Closest confirmed support below entry
        targets = [price for price in lows if price < entry]
        tp = max(targets) if targets else None

        risk = sl - entry
        reward = entry - tp if tp is not None else 0

    base = {
        "trend": trend,
        "direction": direction,
        "pattern": pattern,
        "sweep_level": sweep_level,
        "entry_reference": entry,
        "sl": sl,
        "tp": tp,
        "candle_time": current["time"],
        "valid_after": current["time"] + CANDLE_SECONDS,
    }

    if tp is None:
        return decision(
            "REJECTED",
            "NO_OPPOSING_SWING_TARGET",
            **base,
        )

    # Validate numerical precision, finite positive values, and valid geometry
    for val in (entry, sl, tp, risk, reward):
        if val is None or not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val):
            return decision("REJECTED", "INVALID_RISK_OR_REWARD", **base)

    if risk <= 0 or reward <= 0:
        return decision(
            "REJECTED",
            "INVALID_RISK_OR_REWARD",
            **base,
        )

    # Validate directional geometry
    if direction == "LONG" and not (sl < entry < tp):
        return decision("REJECTED", "INVALID_RISK_OR_REWARD", **base)
    elif direction == "SHORT" and not (tp < entry < sl):
        return decision("REJECTED", "INVALID_RISK_OR_REWARD", **base)

    rr = reward / risk

    if math.isnan(rr) or math.isinf(rr):
        return decision("REJECTED", "INVALID_RISK_OR_REWARD", **base)

    rr_rounded = round(rr, 2)
    if rr_rounded < MIN_RR:
        return decision(
            "REJECTED",
            "RISK_REWARD_TOO_LOW",
            rr=rr_rounded,
            **base,
        )

    return decision(
        "PLAN_READY",
        "SWEEP_PATTERN_AND_RR_CONFIRMED",
        rr=rr_rounded,
        **base,
    )


def main():
    symbols = ["BTCUSDT", "ETHUSDT", "RLCUSDT"]

    print("\nTRADINGOS - TRADE PLAN ENGINE V1")
    print("Research only. No order execution.\n")

    for symbol in symbols:
        try:
            one_hour = fetch_closed_candles(symbol, "60")
            fifteen_min = fetch_closed_candles(symbol, "15")

            plan = build_trade_plan(one_hour, fifteen_min)

            print(symbol, "=>", plan["status"])
            print("Reason:", plan["reason"])

            if "entry_reference" in plan:
                for field in (
                    "direction", "pattern", "entry_reference",
                    "sl", "tp", "rr", "valid_after"
                ):
                    print(" ", field, ":", plan.get(field))

            print("-" * 55)

        except Exception as error:
            print(symbol, "ERROR:", error)

    print("NO ORDERS PLACED")


if __name__ == "__main__":
    main()

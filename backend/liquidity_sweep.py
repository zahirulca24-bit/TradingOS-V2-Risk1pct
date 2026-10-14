import time
from datetime import datetime, timezone

from universe import get_top_coins
from universe_candles import fetch_closed_candles
from market_structure import detect_trend

LEFT = 2
RIGHT = 2
FIFTEEN_MINUTES = 15 * 60


def find_confirmed_levels(candles, before_time):
    """Find 15m swings confirmed before the signal candle."""
    swing_highs = []
    swing_lows = []

    for i in range(LEFT, len(candles) - RIGHT):
        current = candles[i]
        neighbors = (
            candles[i - LEFT:i]
            + candles[i + 1:i + RIGHT + 1]
        )

        confirmed_at = (
            candles[i + RIGHT]["time"] + FIFTEEN_MINUTES
        )

        # The level must already be known
        if confirmed_at > before_time:
            continue

        if all(current["high"] > c["high"] for c in neighbors):
            swing_highs.append(current["high"])

        if all(current["low"] < c["low"] for c in neighbors):
            swing_lows.append(current["low"])

    return swing_highs, swing_lows


def detect_liquidity_sweep(candles, trend):
    if len(candles) < 6:
        return None

    if trend not in ("BULLISH", "BEARISH"):
        return None

    # Only examine the newest closed 15m candle
    signal_candle = candles[-1]

    highs, lows = find_confirmed_levels(
        candles[:-1],
        before_time=signal_candle["time"]
    )

    if trend == "BULLISH":
        if not lows:
            return None

        level = lows[-1]

        # Wick sweeps below low; candle closes above it
        if (
            signal_candle["low"] < level
            and signal_candle["close"] > level
        ):
            return {
                "direction": "LONG",
                "level": level,
                "close": signal_candle["close"],
                "time": signal_candle["time"],
            }

    if trend == "BEARISH":
        if not highs:
            return None

        level = highs[-1]

        # Wick sweeps above high; candle closes below it
        if (
            signal_candle["high"] > level
            and signal_candle["close"] < level
        ):
            return {
                "direction": "SHORT",
                "level": level,
                "close": signal_candle["close"],
                "time": signal_candle["time"],
            }

    return None


def main():
    coins = get_top_coins()
    candidates = []
    failures = []
    bullish = 0
    bearish = 0
    neutral = 0

    print("\nTRADINGOS - 15m LIQUIDITY SWEEP SCANNER")
    print("1H Trend + 15m Confirmed Swing Levels")
    print("-" * 75)

    for index, coin in enumerate(coins, start=1):
        symbol = coin["symbol"]

        try:
            candles_1h = fetch_closed_candles(symbol, "60")
            candles_15m = fetch_closed_candles(symbol, "15")

            structure = detect_trend(candles_1h)
            trend = structure["trend"]

            if trend == "BULLISH":
                bullish += 1
            elif trend == "BEARISH":
                bearish += 1
            else:
                neutral += 1

            result = detect_liquidity_sweep(
                candles_15m,
                trend
            )

            if result:
                candidates.append({
                    "symbol": symbol,
                    "trend": trend,
                    **result,
                })
                status = result["direction"] + " CANDIDATE"
            else:
                status = "WAIT"

            print(
                f"{index:02d}. {symbol:16} "
                f"{trend:8} | {status}"
            )

        except Exception as error:
            failures.append((symbol, str(error)))
            print(f"{index:02d}. {symbol:16} ERROR")

        time.sleep(0.25)

    print("\n" + "-" * 75)
    print("BULLISH:", bullish)
    print("BEARISH:", bearish)
    print("NEUTRAL:", neutral)
    print("SWEEP CANDIDATES:", len(candidates))
    print("FAILED:", len(failures))

    for item in candidates:
        candle_time = datetime.fromtimestamp(
            item["time"], tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")

        print(
            "CANDIDATE:",
            item["symbol"],
            item["direction"],
            "| Level:", item["level"],
            "| Close:", item["close"],
            "| Candle:", candle_time,
        )

    for symbol, error in failures:
        print("ERROR:", symbol, error)

    print("NO ORDERS PLACED")


if __name__ == "__main__":
    main()

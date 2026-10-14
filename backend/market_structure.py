import time

from universe import get_top_coins
from universe_candles import fetch_closed_candles

SWING_LEFT = 2
SWING_RIGHT = 2


def find_swings(candles):
    highs = []
    lows = []

    for i in range(SWING_LEFT, len(candles) - SWING_RIGHT):
        current = candles[i]

        neighbors = (
            candles[i - SWING_LEFT:i]
            + candles[i + 1:i + SWING_RIGHT + 1]
        )

        is_high = all(
            current["high"] > item["high"]
            for item in neighbors
        )

        is_low = all(
            current["low"] < item["low"]
            for item in neighbors
        )

        # The swing becomes known only after two later candles close
        confirmed_at = (
            candles[i + SWING_RIGHT]["time"] + 3600
        )

        if is_high:
            highs.append({
                "time": current["time"],
                "price": current["high"],
                "confirmed_at": confirmed_at,
            })

        if is_low:
            lows.append({
                "time": current["time"],
                "price": current["low"],
                "confirmed_at": confirmed_at,
            })

    return highs, lows


def detect_trend(candles):
    highs, lows = find_swings(candles)

    if len(highs) < 2 or len(lows) < 2:
        return {
            "trend": "NEUTRAL",
            "reason": "Not enough confirmed swings",
            "swing_highs": len(highs),
            "swing_lows": len(lows),
        }

    previous_high = highs[-2]["price"]
    latest_high = highs[-1]["price"]

    previous_low = lows[-2]["price"]
    latest_low = lows[-1]["price"]

    if latest_high > previous_high and latest_low > previous_low:
        trend = "BULLISH"
        reason = "Higher High + Higher Low"

    elif latest_high < previous_high and latest_low < previous_low:
        trend = "BEARISH"
        reason = "Lower High + Lower Low"

    else:
        trend = "NEUTRAL"
        reason = "Mixed market structure"

    return {
        "trend": trend,
        "reason": reason,
        "swing_highs": len(highs),
        "swing_lows": len(lows),
        "last_high": latest_high,
        "last_low": latest_low,
        "high_confirmed_at": highs[-1]["confirmed_at"],
        "low_confirmed_at": lows[-1]["confirmed_at"],
    }


def main():
    coins = get_top_coins()

    counts = {
        "BULLISH": 0,
        "BEARISH": 0,
        "NEUTRAL": 0,
    }

    failures = []

    print("\nTRADINGOS - 1H MARKET STRUCTURE")
    print("Swing rule: 2 Left + 2 Right")
    print("-" * 75)

    for index, coin in enumerate(coins, start=1):
        symbol = coin["symbol"]

        try:
            candles = fetch_closed_candles(symbol, "60")
            result = detect_trend(candles)

            trend = result["trend"]
            counts[trend] += 1

            print(
                f"{index:02d}. {symbol:16} "
                f"{trend:8} | {result['reason']}"
            )

        except Exception as error:
            failures.append((symbol, str(error)))
            print(f"{index:02d}. {symbol:16} ERROR")

        # Reduce pressure on the Bybit API
        time.sleep(0.25)

    print("-" * 75)
    print("BULLISH:", counts["BULLISH"])
    print("BEARISH:", counts["BEARISH"])
    print("NEUTRAL:", counts["NEUTRAL"])
    print("FAILED:", len(failures))
    print("NO TRADES PLACED")

    for symbol, error in failures:
        print("ERROR DETAILS:", symbol, error)


if __name__ == "__main__":
    main()

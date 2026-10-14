import time
from datetime import datetime, timezone

from universe import get_top_coins, get_bybit_data

TIMEFRAMES = {"15m": "15", "1H": "60"}
CANDLE_LIMIT = 100


def fetch_closed_candles(symbol, interval):
    result = get_bybit_data(
        "/v5/market/kline",
        {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": CANDLE_LIMIT + 1,
        }
    )

    now_ms = int(time.time() * 1000)
    duration_ms = int(interval) * 60 * 1000

    candles = []

    for row in result["list"]:
        start_ms = int(row[0])

        # Use only fully closed candles
        if start_ms + duration_ms > now_ms:
            continue

        candle = {
            "time": start_ms // 1000,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }

        # Validate OHLC prices
        if not (
            candle["low"] <= candle["open"] <= candle["high"]
            and candle["low"] <= candle["close"] <= candle["high"]
        ):
            raise ValueError("Invalid OHLC data")

        candles.append(candle)

    candles.sort(key=lambda item: item["time"])

    return candles[-CANDLE_LIMIT:]


def main():
    coins = get_top_coins()

    # Data stays in memory during this test
    market_data = {}
    failures = []

    print("\nTRADINGOS - 50 SYMBOL MARKET DATA TEST")
    print("Timeframes: 15m + 1H")
    print("-" * 65)

    for index, coin in enumerate(coins, start=1):
        symbol = coin["symbol"]
        market_data[symbol] = {}

        for label, interval in TIMEFRAMES.items():
            try:
                candles = fetch_closed_candles(symbol, interval)

                if not candles:
                    raise ValueError("No closed candles")

                market_data[symbol][label] = candles

            except Exception as error:
                failures.append((symbol, label, str(error)))

            # Small delay to reduce API request pressure
            time.sleep(0.25)

        count_15m = len(market_data[symbol].get("15m", []))
        count_1h = len(market_data[symbol].get("1H", []))

        print(
            f"{index:02d}. {symbol:16}"
            f" 15m: {count_15m:3d}"
            f" | 1H: {count_1h:3d}"
        )

    print("-" * 65)

    successful = sum(
        1 for item in market_data.values()
        if item.get("15m") and item.get("1H")
    )

    print("Symbols with both timeframes:", successful)
    print("Failed timeframe requests:", len(failures))
    print("Checked at:", datetime.now(timezone.utc).isoformat())
    print("No orders placed. No database writes.")

    if failures:
        print("\nFAILURES:")
        for symbol, label, error in failures:
            print(symbol, label, error)


if __name__ == "__main__":
    main()

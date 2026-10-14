import json
from datetime import datetime, timezone
from urllib.request import urlopen

# Our TradingOS backend API
API_URL = (
    "http://127.0.0.1:8765/market/candles"
    "?symbol=BTCUSDT&interval=60&limit=200"
)

LEFT = 2
RIGHT = 2


def find_swings(candles):
    swings = []

    # Two candles on each side are required
    for i in range(LEFT, len(candles) - RIGHT):
        current = candles[i]

        left = candles[i - LEFT:i]
        right = candles[i + 1:i + RIGHT + 1]
        neighbors = left + right

        # Check Swing High
        is_swing_high = all(
            current["high"] > candle["high"]
            for candle in neighbors
        )

        # Check Swing Low
        is_swing_low = all(
            current["low"] < candle["low"]
            for candle in neighbors
        )

        candle_time = datetime.fromtimestamp(
            current["time"],
            tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M")

        # Confirmation occurs after the right candles close
        confirmation_time = datetime.fromtimestamp(
            candles[i + RIGHT]["time"] + 3600,
            tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M")

        if is_swing_high:
            swings.append({
                "type": "SWING HIGH",
                "time": candle_time,
                "price": current["high"],
                "confirmed_at": confirmation_time,
            })

        if is_swing_low:
            swings.append({
                "type": "SWING LOW",
                "time": candle_time,
                "price": current["low"],
                "confirmed_at": confirmation_time,
            })

    return swings


def main():
    try:
        with urlopen(API_URL, timeout=20) as response:
            data = json.load(response)

        candles = data["candles"]

        if len(candles) < 5:
            print("Not enough closed candles.")
            return

        swings = find_swings(candles)

        print("\nBTCUSDT - 1H CONFIRMED SWINGS")
        print("Rule: 2 Left + 2 Right")
        print("-" * 75)

        for swing in swings[-12:]:
            print(
                swing["type"],
                "| Candle:", swing["time"], "UTC",
                "| Price:", swing["price"],
                "| Confirmed:", swing["confirmed_at"], "UTC"
            )

        print("-" * 75)
        print("Total confirmed swings:", len(swings))

    except Exception as error:
        print("ERROR:", error)


if __name__ == "__main__":
    main()

import json
from datetime import datetime, timezone
from urllib.request import urlopen
from urllib.parse import urlencode

# Bybit public futures market API
base_url = "https://api.bybit.com/v5/market/kline"

params = urlencode({
    "category": "linear",
    "symbol": "BTCUSDT",
    "interval": "15",
    "limit": 10
})

try:
    with urlopen(base_url + "?" + params, timeout=15) as response:
        data = json.load(response)

    if data.get("retCode") != 0:
        raise ValueError(data.get("retMsg", "Bybit API error"))

    # Bybit sends newest candles first
    candles = sorted(
        data["result"]["list"],
        key=lambda row: int(row[0])
    )

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    print("\nBTCUSDT - BYBIT FUTURES - CLOSED 15m CANDLES")
    print("-" * 65)

    for candle in candles:
        start_ms = int(candle[0])

        # Skip candle if its 15-minute period is still open
        if start_ms + 15 * 60 * 1000 > now_ms:
            continue

        candle_time = datetime.fromtimestamp(
            start_ms / 1000, tz=timezone.utc
        )

        print(
            candle_time.strftime("%Y-%m-%d %H:%M UTC"),
            "| Open:", candle[1],
            "| High:", candle[2],
            "| Low:", candle[3],
            "| Close:", candle[4]
        )

except Exception as error:
    print("ERROR:", error)

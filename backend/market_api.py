import json
import time
from urllib.parse import urlencode
from urllib.request import urlopen

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


@router.get("/market/candles")
def get_candles(
    symbol: str = "BTCUSDT",
    interval: str = "15",
    limit: int = Query(default=100, ge=1, le=200),
):
    # Support our first two timeframes
    if interval not in ("15", "60"):
        raise HTTPException(status_code=400, detail="Use 15 or 60")

    # Only USDT perpetual symbols for now
    if not symbol.isalnum() or not symbol.endswith("USDT"):
        raise HTTPException(status_code=400, detail="Invalid symbol")

    params = urlencode({
        "category": "linear",
        "symbol": symbol,
        "interval": interval,
        "limit": limit + 1,
    })

    url = "https://api.bybit.com/v5/market/kline?" + params

    try:
        with urlopen(url, timeout=15) as response:
            data = json.load(response)

        if data.get("retCode") != 0:
            raise ValueError(data.get("retMsg", "Bybit API error"))

        now_ms = int(time.time() * 1000)
        duration_ms = int(interval) * 60 * 1000

        candles = []

        for row in data["result"]["list"]:
            start_ms = int(row[0])

            # Ignore the still-open candle
            if start_ms + duration_ms > now_ms:
                continue

            candles.append({
                "time": start_ms // 1000,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            })

        candles.sort(key=lambda item: item["time"])

        return {
            "symbol": symbol,
            "interval": interval,
            "candles": candles[-limit:],
        }

    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"Market data unavailable: {type(error).__name__}",
        )

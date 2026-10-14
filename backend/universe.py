import json
from urllib.parse import urlencode
from urllib.request import urlopen

BASE_URL = "https://api.bybit.com"

# TRADINGOS_REQUEST_PACING_V1
import time
import ssl
from urllib.error import URLError, HTTPError

_REQUEST_GAP_SECONDS = 1.25
_REQUEST_LAST_STARTED = 0.0
_MAX_REQUEST_ATTEMPTS = 3


def _wait_for_request_slot():
    global _REQUEST_LAST_STARTED
    elapsed = time.monotonic() - _REQUEST_LAST_STARTED
    remaining = _REQUEST_GAP_SECONDS - elapsed
    if remaining > 0:
        time.sleep(remaining)
    _REQUEST_LAST_STARTED = time.monotonic()

UNIVERSE_SIZE = 50


def get_bybit_data(endpoint, params):
    """Public market data only; bounded retries for transient failures."""
    url = BASE_URL + endpoint + "?" + urlencode(params)

    for attempt in range(_MAX_REQUEST_ATTEMPTS):
        _wait_for_request_slot()
        try:
            with urlopen(url, timeout=20) as response:
                data = json.load(response)

            code = data.get("retCode")
            if code == 0:
                return data["result"]

            message = str(data.get("retMsg") or "Bybit API error")
            is_limited = (
                code == 10006
                or "rate limit" in message.lower()
                or "too many visits" in message.lower()
            )

            if not is_limited:
                raise ValueError(message)

            failure = ValueError(message)
            delay = min(45.0, 12.0 * (2 ** attempt))

        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504):
                raise
            failure = exc
            delay = min(45.0, 12.0 * (2 ** attempt))

        except (URLError, TimeoutError, ssl.SSLError) as exc:
            failure = exc
            delay = min(12.0, 3.0 * (2 ** attempt))

        if attempt == _MAX_REQUEST_ATTEMPTS - 1:
            raise RuntimeError(
                f"Public market data failed after "
                f"{_MAX_REQUEST_ATTEMPTS} attempts: "
                f"{type(failure).__name__}: {failure}"
            ) from failure

        print(
            f"MARKET API RETRY {attempt + 1}/"
            f"{_MAX_REQUEST_ATTEMPTS}: {type(failure).__name__}; "
            f"waiting {delay:.0f}s",
            flush=True
        )
        time.sleep(delay)

    raise RuntimeError("Unexpected request retry state")


def get_trading_symbols():
    symbols = set()
    cursor = ""
    seen_cursors = set()

    while True:
        params = {
            "category": "linear",
            "status": "Trading",
            "limit": 500,
        }

        if cursor:
            params["cursor"] = cursor

        result = get_bybit_data(
            "/v5/market/instruments-info",
            params
        )

        for item in result["list"]:
            if (
                item["status"] == "Trading"
                and item["contractType"] == "LinearPerpetual"
                and item["quoteCoin"] == "USDT"
                and item.get("settleCoin") == "USDT"
            ):
                symbols.add(item["symbol"])

        next_cursor = result.get("nextPageCursor", "")

        if not next_cursor:
            break

        if next_cursor in seen_cursors:
            raise RuntimeError("Repeated pagination cursor")

        seen_cursors.add(next_cursor)
        cursor = next_cursor

    return symbols


def get_top_coins():
    trading_symbols = get_trading_symbols()

    result = get_bybit_data(
        "/v5/market/tickers",
        {"category": "linear"}
    )

    ranked = []

    for ticker in result["list"]:
        symbol = ticker["symbol"]

        if symbol not in trading_symbols:
            continue

        turnover = float(ticker.get("turnover24h") or 0)

        if turnover <= 0:
            continue

        ranked.append({
            "symbol": symbol,
            "turnover24h": turnover,
            "lastPrice": float(ticker["lastPrice"]),
        })

    ranked.sort(
        key=lambda coin: coin["turnover24h"],
        reverse=True
    )

    if len(ranked) < UNIVERSE_SIZE:
        raise RuntimeError("Fewer than 50 eligible coins found")

    return ranked[:UNIVERSE_SIZE]


if __name__ == "__main__":
    try:
        coins = get_top_coins()

        print("\nTRADINGOS - TOP 50 BYBIT FUTURES COINS")
        print("Ranking: 24-hour turnover")
        print("-" * 60)

        for rank, coin in enumerate(coins, start=1):
            print(
                f"{rank:02d}. {coin['symbol']:16}"
                f" Turnover: ${coin['turnover24h']:,.0f}"
            )

        print("-" * 60)
        print("TOTAL SELECTED:", len(coins))
        print("No trades were placed.")

    except Exception as error:
        print("ERROR:", error)

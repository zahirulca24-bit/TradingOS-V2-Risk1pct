import json
import time
from datetime import datetime, timezone
from pathlib import Path

from universe import get_top_coins
from universe_candles import fetch_closed_candles
from market_structure import detect_trend
from liquidity_sweep import detect_liquidity_sweep

OUTPUT_FILE = Path(__file__).resolve().parent / "scanner_snapshot.json"


def run_scanner():
    coins = get_top_coins()
    results = []
    failures = []

    print("\nTRADINGOS - SCANNER SNAPSHOT")
    print("-" * 65)

    for index, coin in enumerate(coins, start=1):
        symbol = coin["symbol"]

        try:
            candles_1h = fetch_closed_candles(symbol, "60")
            candles_15m = fetch_closed_candles(symbol, "15")

            trend = detect_trend(candles_1h)["trend"]
            sweep = detect_liquidity_sweep(candles_15m, trend)

            result = {
                "symbol": symbol,
                "trend": trend,
                "status": "CANDIDATE" if sweep else "WAIT",
                "direction": sweep["direction"] if sweep else None,
                "level": sweep["level"] if sweep else None,
                "close": sweep["close"] if sweep else None,
                "candle_time": sweep["time"] if sweep else None,
            }

            results.append(result)

            print(
                f"{index:02d}. {symbol:16} "
                f"{trend:8} | "
                f"{result['direction'] or 'WAIT'}"
            )

        except Exception as error:
            failures.append({
                "symbol": symbol,
                "error": str(error),
            })
            print(f"{index:02d}. {symbol:16} ERROR")

        time.sleep(0.25)

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_scanned": len(results),
        "candidate_count": sum(
            item["status"] == "CANDIDATE"
            for item in results
        ),
        "failures": failures,
        "results": results,
    }

    # Write safely so readers never see a half-written file
    temp_file = OUTPUT_FILE.with_suffix(".tmp")
    temp_file.write_text(
        json.dumps(snapshot, indent=2),
        encoding="utf-8",
    )
    temp_file.replace(OUTPUT_FILE)

    print("-" * 65)
    print("Scanned:", snapshot["total_scanned"])
    print("Candidates:", snapshot["candidate_count"])
    print("Errors:", len(failures))
    print("Saved to:", OUTPUT_FILE)
    print("Snapshot only. No orders placed.")


if __name__ == "__main__":
    run_scanner()

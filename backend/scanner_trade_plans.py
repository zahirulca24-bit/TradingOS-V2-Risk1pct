import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from universe_candles import fetch_closed_candles
from trade_plan import build_trade_plan


BASE_DIR = Path(__file__).resolve().parent
SOURCE_FILE = BASE_DIR / "scanner_snapshot.json"
OUTPUT_FILE = BASE_DIR / "trade_plans_snapshot.json"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_atomic(path, data):
    temp_path = path.with_suffix(".json.tmp")

    try:
        temp_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def main():
    if not SOURCE_FILE.exists():
        raise SystemExit(
            "ERROR: scanner_snapshot.json missing. "
            "Run scanner_snapshot.py first."
        )

    snapshot = json.loads(
        SOURCE_FILE.read_text(encoding="utf-8-sig")
    )

    source_rows = snapshot.get("results")

    if not isinstance(source_rows, list):
        raise SystemExit(
            "ERROR: Invalid scanner snapshot. "
            "'results' must be a list."
        )

    # The old universe may contain neutral symbols.
    # Exclude neutral symbols from active trade-plan work.
    directional = [
        row
        for row in source_rows
        if isinstance(row, dict)
        and row.get("trend") in ("BULLISH", "BEARISH")
        and isinstance(row.get("symbol"), str)
        and row["symbol"].endswith("USDT")
    ]

    print("TRADINGOS V2 - SCANNER TRADE PLANS")
    print("Source symbols:", len(source_rows))
    print("Directional symbols:", len(directional))
    print("Neutral / others excluded:",
          len(source_rows) - len(directional))
    print("Execution: DISABLED")
    print()

    results = []
    failures = []

    for index, row in enumerate(directional, start=1):
        symbol = row["symbol"]

        print(
            f"[{index}/{len(directional)}] {symbol}",
            flush=True,
        )

        try:
            candles_1h = fetch_closed_candles(
                symbol, "60"
            )
            candles_15m = fetch_closed_candles(
                symbol, "15"
            )

            # Recalculate the plan from currently fetched candles.
            # Do not reuse an old candidate direction as a new signal.
            plan = build_trade_plan(
                candles_1h, candles_15m
            )

            result = {
                "symbol": symbol,
                "status": plan["status"],
                "reason": plan["reason"],
                "trend": plan.get(
                    "trend",
                    row["trend"],
                ),
                "direction": plan.get("direction"),
                "pattern": plan.get("pattern"),
                "sweep_level": plan.get("sweep_level"),
                "entry_reference": plan.get(
                    "entry_reference"
                ),
                "sl": plan.get("sl"),
                "tp": plan.get("tp"),
                "rr": plan.get("rr"),
                "candle_time": plan.get("candle_time"),
                "valid_after": plan.get("valid_after"),
            }

            results.append(result)

            print(
                "  ->",
                result["status"],
                "/",
                result["reason"],
                flush=True,
            )

        except Exception as error:
            failures.append({
                "symbol": symbol,
                "error": str(error),
            })

            print(
                "  -> ERROR:",
                error,
                flush=True,
            )

    counts = Counter(
        item["status"] for item in results
    )

    report = {
        "generated_at": utc_now(),
        "source_snapshot_generated_at": snapshot.get(
            "generated_at"
        ),
        "source_symbols": len(source_rows),
        "directional_attempted": len(directional),
        "evaluated": len(results),
        "neutral_or_other_excluded": (
            len(source_rows) - len(directional)
        ),
        "ready_count": counts.get("PLAN_READY", 0),
        "wait_count": counts.get("WAIT", 0),
        "rejected_count": counts.get("REJECTED", 0),
        "excluded_after_refresh": counts.get("EXCLUDED", 0),
        "failures": failures,
        "results": results,
        "trading_enabled": False,
    }

    write_atomic(OUTPUT_FILE, report)

    print()
    print("=" * 52)
    print("TRADE PLAN REPORT")
    print("Directional attempted:", len(directional))
    print("Evaluated:", len(results))
    print("PLAN_READY:", report["ready_count"])
    print("WAIT:", report["wait_count"])
    print("REJECTED:", report["rejected_count"])
    print(
        "Excluded after fresh 1H trend check:",
        report["excluded_after_refresh"],
    )
    print("Errors:", len(failures))
    print("Saved:", OUTPUT_FILE)
    print("NO ORDERS PLACED")
    print("=" * 52)


if __name__ == "__main__":
    main()

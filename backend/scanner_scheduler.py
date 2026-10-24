"""TradingOS Phase 2 scheduler: exactly one read-only unified cycle per 15m close."""

import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

from pipeline_contract import last_closed_boundary
from pipeline_lock import exclusive_pipeline_lock, PipelineLockContentionError
from pipeline_store import STATE_DIR, atomic_json
from scanner_pipeline import run_once, WAIT_AFTER_CLOSE_SECONDS, CANDLE_SECONDS


def next_scan_time(now_seconds=None):
    current = time.time() if now_seconds is None else float(now_seconds)
    boundary = last_closed_boundary(current, CANDLE_SECONDS)
    target = boundary + WAIT_AFTER_CLOSE_SECONDS
    if current >= target:
        target += CANDLE_SECONDS
    return datetime.fromtimestamp(target, timezone.utc)


def main():
    print("TRADINGOS PHASE 2 AUTO SCANNER (UTC)")
    print("Read-only: crypto-only / 1H / 15m / trade plan / Top 10")
    print("Ctrl+C to stop; no demo or live orders.", flush=True)
    try:
        while True:
            target = next_scan_time()
            print("NEXT SCAN:", target.isoformat(), flush=True)
            while time.time() < target.timestamp():
                time.sleep(min(5, max(0.01, target.timestamp() - time.time())))
            try:
                with exclusive_pipeline_lock():
                    status = run_once()
                    attempt_data = {
                        "status": status,
                        "at": datetime.now(timezone.utc).isoformat(),
                    }
                    atomic_json(STATE_DIR / "last_attempt.json", attempt_data)
                    try:
                        from db_adapter import save_pipeline_last_attempt
                        save_pipeline_last_attempt(attempt_data)
                    except Exception:
                        pass
            except PipelineLockContentionError as lock_err:
                print("SCAN SKIPPED (BUSY): Another scheduler/scan instance is currently executing:", lock_err, flush=True)
                attempt_data = {
                    "status": "skipped",
                    "reason": "busy_lock_held",
                    "at": datetime.now(timezone.utc).isoformat(),
                }
                atomic_json(STATE_DIR / "last_attempt.json", attempt_data)
                # Sleep briefly before waiting for next candle interval. Never alert Telegram on normal concurrency skip.
                time.sleep(1)
            except Exception as exc:
                print("SCAN FAILED; committed cycle unchanged:", exc, flush=True)
                attempt_data = {
                    "status": "failed", "reason": str(exc)[:500],
                    "at": datetime.now(timezone.utc).isoformat(),
                }
                atomic_json(STATE_DIR / "last_attempt.json", attempt_data)
                try:
                    from db_adapter import save_pipeline_last_attempt
                    save_pipeline_last_attempt(attempt_data)
                except Exception:
                    pass
                try:
                    from telegram_notifier import notify_scan_failed
                    notify_scan_failed(str(exc))
                except Exception:
                    pass
                # Sleep until the next interval. Never retry and mix cycles.
                time.sleep(1)
    except KeyboardInterrupt:
        print("Scanner stopped. No orders placed.")


if __name__ == "__main__":
    main()

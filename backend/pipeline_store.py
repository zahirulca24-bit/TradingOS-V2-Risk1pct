"""Committed scan-cycle reads and atomic publication; no trading API calls."""

import json
import os
import tempfile
import shutil
from datetime import datetime, timezone
from pathlib import Path

from pipeline_contract import is_complete_cycle

BASE = Path(__file__).resolve().parent
STATE_DIR = BASE / "pipeline_state"
MANIFEST = STATE_DIR / "current_cycle.json"
NAMES = {"scanner": "scanner_snapshot.json", "trade_plans": "trade_plans_snapshot.json", "opportunities": "opportunities_snapshot.json"}


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="cycle-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fp:
            json.dump(value, fp, indent=2, ensure_ascii=False, allow_nan=False)
            fp.write("\n")
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_reports(reports):
    if set(reports) != set(NAMES):
        raise ValueError("Expected three matching scanner reports")
    if not is_complete_cycle(list(reports.values())):
        raise ValueError("Reports are not from one complete successful cycle")
    scanner = reports["scanner"]
    plans = reports["trade_plans"]
    ranked = reports["opportunities"]
    symbols = {row["symbol"] for row in scanner["results"]}
    plan_symbols = {row["symbol"] for row in plans["results"]}
    rank_rows = ranked["top_10"] + ranked["remaining"]
    rank_symbols = {row["symbol"] for row in rank_rows}
    if (
        len(symbols) != len(scanner["results"])
        or len(plan_symbols) != len(plans["results"])
        or len(rank_symbols) != len(rank_rows)
        or not symbols == plan_symbols == rank_symbols
    ):
        raise ValueError("Symbol membership differs across reports")
    if len(ranked["top_10"]) > 10:
        raise ValueError("Top 10 exceeded")
    if any(row["trend"] not in ("BULLISH", "BEARISH") for row in scanner["results"]):
        raise ValueError("Neutral symbol in active scanner")
    if any(row["status"] == "PLAN_READY" and row.get("rr", 0) < 2 for row in plans["results"]):
        raise ValueError("Unqualified ready plan")
    if any(row.get("trading_enabled") for row in reports.values()):
        raise ValueError("Trading must be disabled")


def _cycle_id_to_dir(cycle_id):
    if not isinstance(cycle_id, str) or len(cycle_id) != 16 or not cycle_id.endswith("Z") or not cycle_id[:-1].replace("T", "").isdigit():
        raise ValueError("Invalid cycle ID")
    return STATE_DIR / "cycles" / cycle_id


def publish(reports):
    """Write complete immutable cycle first; commit manifest last.

    Compatibility mirrors updated after committing; verified readers must use
    read_committed() or /scanner/cycle for consistent 3-report reads.
    """
    validate_reports(reports)
    ident = reports["scanner"]["cycle_id"]
    cycle_dir = _cycle_id_to_dir(ident)
    if cycle_dir.exists():
        # Safe/idempotent handling: if this cycle directory already exists and is complete,
        # ensure database and manifest are consistent without raising unhandled FileExistsError
        try:
            from db_adapter import save_pipeline_cycle
            save_pipeline_cycle(reports)
        except Exception as exc:
            print("ERROR: Durable database pipeline cycle storage failed:", type(exc).__name__, exc)
            raise
        return ident
    cycles_root = cycle_dir.parent
    cycles_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="staging-", dir=str(cycles_root)))
    try:
        for key, filename in NAMES.items():
            atomic_json(staging / filename, reports[key])
        # Validate all on-disk reports before exposing this directory.
        read_back = {key: json.loads((staging / filename).read_text(encoding="utf-8")) for key, filename in NAMES.items()}
        validate_reports(read_back)
        staging.rename(cycle_dir)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    # 1. Persist in durable database (Neon PostgreSQL or SQLite pipeline_cycles table)
    # Database persistence happens before committing manifest to avoid disk-committed/db-missing inconsistency
    try:
        from db_adapter import save_pipeline_cycle
        save_pipeline_cycle(reports)
    except Exception as exc:
        print("ERROR: Durable database pipeline cycle storage failed:", type(exc).__name__, exc)
        if cycle_dir.exists():
            shutil.rmtree(cycle_dir, ignore_errors=True)
        raise

    # 2. Commit manifest after durable database save succeeds
    atomic_json(MANIFEST, {
        "cycle_id": ident,
        "candle_close_utc": reports["scanner"]["candle_close_utc"],
        "committed_at": datetime.now(timezone.utc).isoformat(),
        "status": "success",
    })

    # 3. Compatibility mirrors updated after committing
    for key, filename in NAMES.items():
        try:
            atomic_json(BASE / filename, reports[key])
        except OSError as exc:
            print("WARNING: Legacy mirror could not update:", filename, type(exc).__name__)

    try:
        from journal_db import ingest_cycle
        ingest_cycle(reports)
    except Exception as exc:
        print("WARNING: Research journal ingestion failed:", type(exc).__name__, exc)

    try:
        from telegram_notifier import notify_cycle_published
        notify_cycle_published(reports)
    except Exception as exc:
        print("WARNING: Telegram notification dispatch failed:", type(exc).__name__, exc)

    return ident



def read_committed():
    from db_adapter import get_active_engine, load_committed_pipeline_cycle
    engine = get_active_engine()
    if engine == "postgres":
        cycle_data = load_committed_pipeline_cycle()
        if cycle_data is not None:
            validate_reports({k: cycle_data[k] for k in NAMES})
            return cycle_data
        return None

    # SQLite / local Windows: check database first for consistency
    cycle_data = load_committed_pipeline_cycle()
    if cycle_data is not None:
        validate_reports({k: cycle_data[k] for k in NAMES})
        return cycle_data

    # Fallback to local manifest and files if db empty
    if not MANIFEST.is_file():
        return None

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("status") != "success":
        raise ValueError("Cycle not committed")
    cycle_dir = _cycle_id_to_dir(manifest["cycle_id"])
    if not cycle_dir.is_dir():
        return None
    reports = {key: json.loads((cycle_dir / filename).read_text(encoding="utf-8")) for key, filename in NAMES.items()}
    validate_reports(reports)
    if manifest["cycle_id"] != reports["scanner"]["cycle_id"] or manifest.get("candle_close_utc") != reports["scanner"]["candle_close_utc"]:
        raise ValueError("Manifest does not match committed reports")
    return {"manifest": manifest, **reports}


def read_pipeline_status():
    from db_adapter import load_pipeline_last_attempt
    cycle = read_committed()
    path = STATE_DIR / "last_attempt.json"
    last = None
    if path.exists():
        try:
            last = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    if last is None:
        last = load_pipeline_last_attempt()
    return {
        "last_committed_cycle": cycle["manifest"] if cycle else None,
        "last_attempt": last,
        "trading_enabled": False,
    }


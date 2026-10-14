"""TradingOS V2 Operational Health & Error Monitoring (P2).

Read-only operational status without modifying scanner strategy.
No trading APIs, orders, or private credentials.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
REPORTS_DIR = PROJECT_ROOT / "update_reports"
STATE_DIR = BASE_DIR / "pipeline_state"
MANIFEST = STATE_DIR / "current_cycle.json"
LAST_ATTEMPT = STATE_DIR / "last_attempt.json"

FRESHNESS_THRESHOLD_SECONDS = 18 * 60  # 18 minutes (15m cycle + 3m buffer)

RATE_LIMIT_PATTERNS = [
    r"429",
    r"rate\s*limit",
    r"too\s*many\s*requests",
    r"ratelimit",
    r"10002",
]

NETWORK_TIMEOUT_PATTERNS = [
    r"timeout",
    r"timed\s*out",
    r"connection\s*error",
    r"connect\s*timeout",
    r"read\s*timeout",
    r"connection\s*refused",
    r"name\s*resolution",
    r"dns",
    r"network\s*is\s*unreachable",
]

SECRET_PATTERNS = [
    (r"(api[_-]?key[\"'\s:=]+)[A-Za-z0-9_-]{8,}", r"\1[REDACTED]"),
    (r"(secret[\"'\s:=]+)[A-Za-z0-9_-]{8,}", r"\1[REDACTED]"),
    (r"(password[\"'\s:=]+)[A-Za-z0-9_-]{4,}", r"\1[REDACTED]"),
    (r"(token[\"'\s:=]+)[A-Za-z0-9_.-]{8,}", r"\1[REDACTED]"),
]


def sanitize_text(text: str | None) -> str | None:
    if not text:
        return text
    clean = str(text)
    for pattern, repl in SECRET_PATTERNS:
        clean = re.sub(pattern, repl, clean, flags=re.IGNORECASE)
    return clean


def classify_error(reason: str | None) -> str | None:
    if not reason:
        return None
    lower = reason.lower()
    for pat in RATE_LIMIT_PATTERNS:
        if re.search(pat, lower):
            return "RATE_LIMIT"
    for pat in NETWORK_TIMEOUT_PATTERNS:
        if re.search(pat, lower):
            return "NETWORK_TIMEOUT"
    return "GENERAL_ERROR"


def evaluate_freshness(
    committed_cycle: dict | None,
    last_attempt: dict | None,
    now: datetime | None = None,
) -> tuple[str, float | None]:
    """Determine scan freshness.

    Rule: Never claim fresh scan after a failed attempt.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    if not committed_cycle or not committed_cycle.get("candle_close_utc"):
        return "UNKNOWN", None

    try:
        close_str = committed_cycle["candle_close_utc"]
        close_dt = datetime.fromisoformat(close_str)
        if close_dt.tzinfo is None:
            close_dt = close_dt.replace(tzinfo=timezone.utc)
        age_seconds = (now - close_dt).total_seconds()
    except (ValueError, TypeError, KeyError):
        return "UNKNOWN", None

    # If the latest scan attempt failed, we MUST NOT claim the scan is fresh!
    if last_attempt and last_attempt.get("status") == "failed":
        return "STALE", max(0.0, age_seconds)

    if age_seconds < 0:
        return "UNKNOWN", age_seconds

    if age_seconds <= FRESHNESS_THRESHOLD_SECONDS:
        return "FRESH", age_seconds

    return "STALE", age_seconds


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="health-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fp:
            json.dump(data, fp, indent=2, ensure_ascii=False)
            fp.write("\n")
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def build_health_report(
    backend_status: str = "UNKNOWN",
    backend_pid: int | None = None,
    frontend_status: str = "UNKNOWN",
    frontend_pid: int | None = None,
    scheduler_running: bool = False,
    scheduler_logical_count: int = 0,
    scheduler_pids: list[int] | None = None,
    supervisor_status: str = "RUNNING",
    supervisor_pid: int | None = None,
    uptime_seconds: int = 0,
    restart_counts: dict | None = None,
    now: datetime | None = None,
) -> dict:
    if now is None:
        now = datetime.now(timezone.utc)

    committed = None
    if MANIFEST.is_file():
        try:
            committed = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            committed = None

    last_attempt = None
    if LAST_ATTEMPT.is_file():
        try:
            last_attempt = json.loads(LAST_ATTEMPT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            last_attempt = None

    failure_reason = None
    error_category = None
    if last_attempt and last_attempt.get("status") == "failed":
        failure_reason = sanitize_text(last_attempt.get("reason"))
        error_category = classify_error(failure_reason)

    freshness, staleness_seconds = evaluate_freshness(committed, last_attempt, now)

    scheduler_status = (
        "RUNNING"
        if (scheduler_running and scheduler_logical_count == 1)
        else ("CONFLICT" if scheduler_logical_count > 1 else "STOPPED")
    )

    overall = "HEALTHY"
    if backend_status != "ONLINE" or frontend_status != "ONLINE":
        overall = "UNHEALTHY"
    elif scheduler_status != "RUNNING" or freshness != "FRESH":
        overall = "DEGRADED"

    report = {
        "timestamp": now.isoformat(),
        "overall_status": overall,
        "supervisor": {
            "status": supervisor_status,
            "pid": supervisor_pid,
            "uptime_seconds": uptime_seconds,
            "restarts": restart_counts or {"backend": 0, "frontend": 0, "scanner": 0},
        },
        "backend": {
            "status": backend_status,
            "url": "http://127.0.0.1:8765",
            "pid": backend_pid,
        },
        "frontend": {
            "status": frontend_status,
            "url": "http://127.0.0.1:5200",
            "pid": frontend_pid,
        },
        "scanner": {
            "scheduler_status": scheduler_status,
            "logical_count": scheduler_logical_count,
            "pids": scheduler_pids or [],
            "last_committed_cycle": committed,
            "last_attempt": {
                "status": last_attempt.get("status", "UNKNOWN") if last_attempt else "UNKNOWN",
                "at": last_attempt.get("at") if last_attempt else None,
                "reason": failure_reason,
            },
            "freshness": freshness,
            "staleness_seconds": staleness_seconds,
            "error_category": error_category,
            "last_failure_reason": failure_reason,
        },
    }
    return report


def update_health_report(
    backend_status: str = "UNKNOWN",
    backend_pid: int | None = None,
    frontend_status: str = "UNKNOWN",
    frontend_pid: int | None = None,
    scheduler_running: bool = False,
    scheduler_logical_count: int = 0,
    scheduler_pids: list[int] | None = None,
    supervisor_status: str = "RUNNING",
    supervisor_pid: int | None = None,
    uptime_seconds: int = 0,
    restart_counts: dict | None = None,
    out_path: Path | None = None,
) -> dict:
    report = build_health_report(
        backend_status=backend_status,
        backend_pid=backend_pid,
        frontend_status=frontend_status,
        frontend_pid=frontend_pid,
        scheduler_running=scheduler_running,
        scheduler_logical_count=scheduler_logical_count,
        scheduler_pids=scheduler_pids,
        supervisor_status=supervisor_status,
        supervisor_pid=supervisor_pid,
        uptime_seconds=uptime_seconds,
        restart_counts=restart_counts,
    )
    target = out_path or (REPORTS_DIR / "health_report.json")
    atomic_write_json(target, report)
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="TradingOS Operational Health Reporter")
    parser.add_argument("--backend-status", default="UNKNOWN")
    parser.add_argument("--backend-pid", type=int, default=None)
    parser.add_argument("--frontend-status", default="UNKNOWN")
    parser.add_argument("--frontend-pid", type=int, default=None)
    parser.add_argument("--scheduler-running", action="store_true")
    parser.add_argument("--logical-count", type=int, default=0)
    parser.add_argument("--pids", default="")
    parser.add_argument("--supervisor-status", default="RUNNING")
    parser.add_argument("--supervisor-pid", type=int, default=None)
    parser.add_argument("--uptime", type=int, default=0)
    parser.add_argument("--restarts-backend", type=int, default=0)
    parser.add_argument("--restarts-frontend", type=int, default=0)
    parser.add_argument("--restarts-scanner", type=int, default=0)
    args = parser.parse_args()

    pids_list = [int(p.strip()) for p in args.pids.split(",") if p.strip().isdigit()]
    restarts_dict = {
        "backend": args.restarts_backend,
        "frontend": args.restarts_frontend,
        "scanner": args.restarts_scanner,
    }

    report = update_health_report(
        backend_status=args.backend_status,
        backend_pid=args.backend_pid,
        frontend_status=args.frontend_status,
        frontend_pid=args.frontend_pid,
        scheduler_running=args.scheduler_running,
        scheduler_logical_count=args.logical_count,
        scheduler_pids=pids_list,
        supervisor_status=args.supervisor_status,
        supervisor_pid=args.supervisor_pid,
        uptime_seconds=args.uptime,
        restart_counts=restarts_dict,
    )
    print(json.dumps(report, indent=2))

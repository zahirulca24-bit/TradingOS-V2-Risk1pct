"""Read-only committed cycle endpoints for Phase 3 frontend."""
import json
from fastapi import APIRouter, HTTPException
from pipeline_store import STATE_DIR, read_committed

router = APIRouter()


@router.get("/scanner/cycle")
def scanner_cycle():
    try:
        result = read_committed()
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Committed scanner cycle is invalid") from exc
    if result is None:
        raise HTTPException(status_code=503, detail="No committed Phase 2 cycle yet")
    return result


@router.get("/scanner/pipeline-status")
def pipeline_status():
    try:
        cycle = read_committed()
        path = STATE_DIR / "last_attempt.json"
        last = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Pipeline status unavailable") from exc
    return {
        "last_committed_cycle": cycle["manifest"] if cycle else None,
        "last_attempt": last,
        "trading_enabled": False,
    }

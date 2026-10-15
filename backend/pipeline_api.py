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
        from pipeline_store import read_pipeline_status
        return read_pipeline_status()
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Pipeline status unavailable") from exc


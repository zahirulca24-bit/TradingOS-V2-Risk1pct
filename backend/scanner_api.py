
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

SNAPSHOT = Path(__file__).resolve().parent / "scanner_snapshot.json"


@router.get("/scanner/results")
def scanner_results():
    from pipeline_store import read_committed
    from fastapi import HTTPException
    try:
        committed = read_committed()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Committed cycle invalid") from exc
    if committed is not None:
        return committed["scanner"]
    if not SNAPSHOT.exists():
        raise HTTPException(
            status_code=404,
            detail="Scanner snapshot not generated yet"
        )

    try:
        data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise HTTPException(
            status_code=500,
            detail="Scanner snapshot could not be read"
        )

    return data

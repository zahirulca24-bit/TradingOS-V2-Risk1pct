import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

router = APIRouter(prefix="/auth", tags=["Local Login"])

CONFIG = Path.home() / "AppData" / "Local" / "TradingOSV2" / "login.json"
SESSIONS = {}
SESSION_SECONDS = 8 * 60 * 60


class LoginRequest(BaseModel):
    password: str


@router.post("/login")
def login(data: LoginRequest, response: Response):
    if not CONFIG.is_file():
        raise HTTPException(status_code=503, detail="Password not configured")

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        data.password.encode("utf-8"),
        bytes.fromhex(config["salt"]),
        config["iterations"]
    )

    if not hmac.compare_digest(digest.hex(), config["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect password")

    session = secrets.token_urlsafe(32)
    SESSIONS[session] = time.time() + SESSION_SECONDS

    response.set_cookie(
        "tradingos_session",
        session,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=SESSION_SECONDS,
        path="/",
    )

    return {"authenticated": True}


@router.get("/session")
def session_status(request: Request):
    session = request.cookies.get("tradingos_session")
    expires = SESSIONS.get(session, 0) if session else 0
    return {"authenticated": expires > time.time()}


@router.post("/logout")
def logout(request: Request, response: Response):
    session = request.cookies.get("tradingos_session")
    if session:
        SESSIONS.pop(session, None)

    response.delete_cookie("tradingos_session", path="/")
    return {"authenticated": False}

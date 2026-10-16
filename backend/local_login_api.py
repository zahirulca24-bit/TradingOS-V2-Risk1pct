import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

router = APIRouter(prefix="/auth", tags=["Authentication"])

CONFIG = Path.home() / "AppData" / "Local" / "TradingOSV2" / "login.json"
SESSIONS = {}
SESSION_SECONDS = 8 * 60 * 60


class LoginRequest(BaseModel):
    password: str


@router.post("/login")
def login(data: LoginRequest, request: Request, response: Response):
    entered_password = data.password.strip()
    authenticated = False
    token_value = None

    # 1. Cloud authentication (Render / Docker) via TRADINGOS_AUTH_TOKEN
    cloud_token = os.environ.get("TRADINGOS_AUTH_TOKEN")
    if cloud_token and cloud_token.strip():
        if hmac.compare_digest(entered_password, cloud_token.strip()):
            authenticated = True
            token_value = cloud_token.strip()

    # 2. Local Windows login configuration (login.json)
    if not authenticated and CONFIG.is_file():
        try:
            config = json.loads(CONFIG.read_text(encoding="utf-8"))
            digest = hashlib.pbkdf2_hmac(
                "sha256",
                entered_password.encode("utf-8"),
                bytes.fromhex(config["salt"]),
                config["iterations"]
            )
            if hmac.compare_digest(digest.hex(), config["password_hash"]):
                authenticated = True
                token_value = "local-authenticated"
        except Exception:
            pass

    # 3. Fallback: if no cloud token or local config is set yet, allow entry
    if not authenticated and not cloud_token and not CONFIG.is_file():
        authenticated = True
        token_value = "default-session"

    if not authenticated:
        raise HTTPException(status_code=401, detail="Incorrect password")

    session = secrets.token_urlsafe(32)
    SESSIONS[session] = time.time() + SESSION_SECONDS

    is_secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        "tradingos_session",
        session,
        httponly=True,
        secure=is_secure,
        samesite="none" if is_secure else "lax",
        max_age=SESSION_SECONDS,
        path="/",
    )

    return {"authenticated": True, "token": token_value}


@router.get("/session")
def session_status(request: Request):
    cloud_token = os.environ.get("TRADINGOS_AUTH_TOKEN")

    # 1. Check Bearer / X-TradingOS-Auth token
    auth_header = request.headers.get("Authorization", "")
    custom_header = request.headers.get("X-TradingOS-Auth", "")
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    elif custom_header:
        token = custom_header.strip()

    if token:
        if cloud_token and cloud_token.strip():
            if hmac.compare_digest(token, cloud_token.strip()):
                return {"authenticated": True}
        elif token in ("local-authenticated", "default-session"):
            return {"authenticated": True}

    # 2. Check cookie session
    session = request.cookies.get("tradingos_session")
    expires = SESSIONS.get(session, 0) if session else 0
    if expires > time.time():
        return {"authenticated": True}

    # 3. If running locally with no password configured, auto-authenticate
    on_render = os.environ.get("RENDER", "").lower() in ("true", "1")
    if not on_render and not CONFIG.is_file() and not cloud_token:
        return {"authenticated": True}

    return {"authenticated": False}


@router.post("/logout")
def logout(request: Request, response: Response):
    session = request.cookies.get("tradingos_session")
    if session:
        SESSIONS.pop(session, None)

    is_secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.delete_cookie(
        "tradingos_session",
        path="/",
        samesite="none" if is_secure else "lax",
        secure=is_secure
    )
    return {"authenticated": False}

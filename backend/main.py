import os
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Create our TradingOS backend application
app = FastAPI(title="TradingOS V2 API")

# Fail closed for sensitive endpoints on public Render until secure session
# authentication is implemented. Local Windows research workflow is unchanged.
@app.middleware("http")
async def prevent_anonymous_cloud_data_access(request: Request, call_next):
    on_render = os.environ.get("RENDER", "").lower() in ("true", "1")
    if on_render and request.method != "OPTIONS":
        public_paths = {"/", "/health"}
        public_prefixes = ("/market/",)
        path = request.url.path
        if path not in public_paths and not path.startswith(public_prefixes):
            configured_token = os.environ.get("TRADINGOS_AUTH_TOKEN")
            if not configured_token or not configured_token.strip():
                # Fail-closed protection: disabled on cloud until authentication token is configured
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Private TradingOS API temporarily disabled on public cloud pending authentication"},
                )

            # Validate client authentication token via Authorization: Bearer <token> or X-TradingOS-Auth
            auth_header = request.headers.get("Authorization", "")
            custom_header = request.headers.get("X-TradingOS-Auth", "")
            provided_token = None
            if auth_header.startswith("Bearer "):
                provided_token = auth_header[7:].strip()
            elif custom_header:
                provided_token = custom_header.strip()

            if not provided_token or provided_token != configured_token.strip():
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized: valid authentication token required"},
                )

    return await call_next(request)

default_origins = [
    "http://127.0.0.1:5200",
    "http://localhost:5200",
    "http://localhost:5180",
    "http://127.0.0.1:5180",
    "http://localhost:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
    "https://trading-os-v2-risk1pct.vercel.app",
]
env_origins = os.getenv("ALLOWED_ORIGINS")
if env_origins:
    default_origins.extend([o.strip() for o in env_origins.split(",") if o.strip()])

# Allow the TradingOS frontend to access the backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=default_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "X-TradingOS-Auth", "Content-Type", "*"],
)

# Home endpoint
@app.get("/")
def home():
    return {"message": "TradingOS V2 Backend is running"}


# Health check endpoint
@app.get("/health")
def health():
    return {
        "status": "online",
        "service": "TradingOS V2 Backend",
        "bybit_connected": False,
        "database_connected": False,
        "trading_enabled": False
    }


# Operational health endpoint (P2)
@app.get("/health/operational")
def operational_health():
    import json
    from operational_health import REPORTS_DIR, build_health_report
    path = REPORTS_DIR / "health_report.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return build_health_report(backend_status="ONLINE")


from market_api import router as market_router
app.include_router(market_router)

from scanner_api import router as scanner_router
app.include_router(scanner_router)


# TradingOS V2 read-only opportunity ranking
@app.get("/scanner/opportunities")
def get_scanner_opportunities():
    # Prefer a single committed scanner cycle over legacy snapshots.
    from pipeline_store import read_committed
    try:
        committed = read_committed()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Committed cycle invalid") from exc
    if committed is not None:
        return committed["opportunities"]
    import json
    from pathlib import Path
    from fastapi import HTTPException

    snapshot_file = (
        Path(__file__).resolve().parent
        / "opportunities_snapshot.json"
    )

    if not snapshot_file.is_file():
        raise HTTPException(
            status_code=503,
            detail="Opportunity ranking not generated yet",
        )

    try:
        data = json.loads(
            snapshot_file.read_text(encoding="utf-8-sig")
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Opportunity snapshot unavailable",
        ) from exc

    if not isinstance(data, dict):
        raise HTTPException(
            status_code=503,
            detail="Invalid opportunity snapshot",
        )

    return data


# TRADINGOS_DEMO_DASHBOARD_V1
from demo_dashboard_api import router as demo_dashboard_router
app.include_router(demo_dashboard_router)

# Read-only unified cycle API
from pipeline_api import router as pipeline_router
app.include_router(pipeline_router)

# DEMO ONLY: read-only 1% risk previews, no order execution.
from demo_risk_api import router as demo_risk_router
app.include_router(demo_risk_router)

# P6 Trade Journal & Daily Reports (Read-Only)
from journal_api import router as journal_router
app.include_router(journal_router)

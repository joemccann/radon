"""Radon FastAPI server — replaces Python shell-outs from Next.js.

Persistent IB connections, shared UW client, uniform JSON responses.
Port 8321, no auth for local use.

Usage:
    python3 -m uvicorn scripts.api.server:app --host 127.0.0.1 --port 8321 --reload
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# Ensure scripts/ is on sys.path for client imports
SCRIPTS_DIR = Path(__file__).parent.parent
PROJECT_ROOT = SCRIPTS_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
INTERNALS_SKEW_CACHE_DIR = DATA_DIR / "cache"
INTERNALS_SKEW_CACHE_TTL_SECONDS = 60 * 15

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from api.ib_pool import IBPool
from api import db_http
from api.subprocess import run_script, run_module, run_script_raw, ScriptResult
from api.ib_gateway import (
    check_ib_gateway,
    ensure_ib_gateway,
    restart_ib_gateway,
    is_docker_mode,
    is_cloud_mode,
    is_launchd_mode,
    reset_restart_backoff,
)
from api import services as admin_services
from clients.ib_client import DEFAULT_GATEWAY_PORT
from api.pool_order_manage import pool_cancel_order, pool_modify_order
from api.auth import verify_clerk_jwt, verify_api_key, is_trusted_local_request
from api.ws_ticket import create_ticket, validate_ticket
from api.routes.historical import router as historical_router

# Load .env from project root for Python scripts.
# .env.ib-mode (managed by scripts/ib mode) overlays after .env so its
# IB_GATEWAY_MODE/HOST values win — single switch, no .env rewriting.
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(PROJECT_ROOT / "web" / ".env")
    load_dotenv(PROJECT_ROOT / ".env.ib-mode", override=True)
except ImportError:
    pass

logger = logging.getLogger("radon.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# Suppress verbose ib_insync logging (positions, orders at INFO level)
logging.getLogger("ib_insync").setLevel(logging.WARNING)
logging.getLogger("ib_insync.wrapper").setLevel(logging.WARNING)
logging.getLogger("ib_insync.client").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
from clients.uw_client import UWClient
from clients.uw_client import UWAPIError, UWNotFoundError
from ib_insync import Index


# Shared state
# ---------------------------------------------------------------------------
ib_pool: Optional[IBPool] = None
uw_available: bool = False
test_mode: bool = os.environ.get("RADON_API_TEST_MODE", "").lower() in {"1", "true", "yes", "on"}
test_order_counter: int = 900000


def _next_test_order_ids() -> tuple[int, int]:
    global test_order_counter
    test_order_counter += 1
    order_id = test_order_counter
    perm_id = 8_000_000 + order_id
    return order_id, perm_id


IB_HEARTBEAT_INTERVAL_SECS = 15

# Worst-case wall-clock budget for the /health IB gateway probe. The probe can
# block for tens of seconds when the pool is mid-reconnect after a 2FA approval
# (handle_auth_state_transition -> pool.reconnect_all bounded at 30s + heal 10s).
# When it does, uvicorn workers pile up and every health-dependent UI surface
# shows a scary "operation aborted due to timeout" / RELAY OFFLINE state even
# though IB is healthy. /health must ALWAYS return fast with a structured
# payload; recovery itself stays on the unbounded _ib_recovery_heartbeat_loop.
HEALTH_GATEWAY_PROBE_TIMEOUT_SECS = 2.5


async def _ib_recovery_heartbeat_tick() -> None:
    """Drive check_ib_gateway WITH the pool once, so the documented
    awaiting_2fa -> authenticated pool recovery (pool.reconnect_all) fires
    server-side, independent of any browser poll.

    The status consumers now read the read-only /edge-health surface (which
    probes /health/lite with pool=None and has NO side effects), so this loop is
    the sole FAST driver of recovery; the every-minute watchdog /health curl is
    the slower backstop. See feedback_ib_pool_stuck_after_2fa.md.
    """
    if ib_pool is None:
        return
    try:
        await check_ib_gateway(pool_status=ib_pool.status(), pool=ib_pool)
    except Exception:
        logger.exception("IB recovery heartbeat tick failed")


async def _ib_recovery_heartbeat_loop(interval: float = IB_HEARTBEAT_INTERVAL_SECS) -> None:
    while True:
        await asyncio.sleep(interval)
        await _ib_recovery_heartbeat_tick()


ORDERS_SYNC_INTERVAL_SECS = 5 * 60  # 5 min — comfortably under the 10-min watchdog window


async def _orders_sync_tick() -> None:
    """Refresh open orders from IB during market hours.

    Keeps the orders-sync service_health row fresh so the watchdog's
    intraday bucket (10-min window) does not fire stale alerts during the
    trading day. The actual work mirrors what POST /orders/refresh does:
    run ib_orders.py --sync via the recovery-aware subprocess helper,
    which writes orders.json + heartbeats the orders-sync service_health
    row via service_cycle.

    Guards (all must pass):
    - not test_mode          — never run subprocess syncs in unit tests
    - market hours open      — the watchdog window is intraday-only; no
                               need to run outside 09:30–16:00 ET weekdays
    - pool has a connection  — proxy for "IB Gateway authenticated"; if
                               the pool is fully disconnected we would
                               just burn the IB cooldown and log an error
    """
    if test_mode:
        return
    if not _is_market_open_now_et():
        return
    if not _pool_has_any_connection():
        logger.debug("orders-sync loop: pool disconnected — skipping tick")
        return
    logger.info("orders-sync loop: running ib_orders.py --sync")
    result = await _run_ib_script_with_recovery(
        "ib_orders.py", ["--sync", "--port", str(DEFAULT_GATEWAY_PORT)], timeout=30, raw=True
    )
    if result.ok:
        logger.info("orders-sync loop: sync complete")
    else:
        logger.warning("orders-sync loop: sync failed: %s", result.error)


async def _orders_sync_loop(interval: float = ORDERS_SYNC_INTERVAL_SECS) -> None:
    """Autonomous market-hours orders refresh loop.

    Sleeps first so the initial page-load /orders/refresh call (fired
    by the Next.js /orders route a few seconds after startup) has
    already run before we kick off the first autonomous sync.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            await _orders_sync_tick()
        except Exception:
            logger.exception("orders-sync loop: unhandled exception — continuing")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start IB pool and UW client on startup, tear down on shutdown."""
    global ib_pool, uw_available

    if test_mode:
        logger.info("Radon API starting in test mode; IB Gateway and pool startup are disabled")
        uw_available = bool(os.environ.get("UW_TOKEN"))
        yield
        logger.info("Radon API test mode shut down")
        return

    # Ensure IB Gateway is running before connecting pool
    gw_status = await ensure_ib_gateway()
    logger.info("IB Gateway: %s", gw_status)

    # IB pool — connect_all() blocks ~25-30s per client when IB Gateway is
    # awaiting_2fa. Three clients × that timeout = ~80s lifespan stall,
    # which prevents uvicorn from binding port 8321 inside the deploy
    # script's 45s health-check window and triggers a false-positive
    # rollback. Kick connect off as a background task instead — routes
    # already tolerate a not-yet-connected pool, and /health exposes
    # pool + auth_state so operators can see "connecting" without us
    # blocking the listener.
    ib_pool = IBPool()
    app.state.ib_pool = ib_pool

    async def _connect_ib_pool() -> None:
        try:
            pool_status = await ib_pool.connect_all()
            logger.info("IB pool status: %s", pool_status)
        except Exception:
            logger.exception("IB pool background connect failed")

    asyncio.create_task(_connect_ib_pool())

    # Server-side 2FA-recovery heartbeat. Consumers now poll the read-only
    # /edge-health surface, so the mutating recovery path can no longer ride a
    # browser /health poll — drive it here on a fixed cadence instead.
    asyncio.create_task(_ib_recovery_heartbeat_loop())

    # Autonomous orders-sync loop — keeps the orders-sync service_health row
    # fresh during market hours so the watchdog's intraday bucket (10-min
    # window) does not fire stale alerts when no browser has visited /orders.
    asyncio.create_task(_orders_sync_loop())

    # UW client — just verify token exists
    uw_available = bool(os.environ.get("UW_TOKEN"))
    if not uw_available:
        logger.warning("UW_TOKEN not set — UW-dependent endpoints will fail")

    # Phase 6: lifespan warming hooks for CRI / GEX have been removed.
    # The Turso embedded replica keeps both reads sub-millisecond, and the
    # systemd timers in radon-services (Hetzner) or laptop launchd plists
    # (local mode) refresh the underlying snapshots on cadence — so the
    # FastAPI server no longer needs to bootstrap those caches at boot.
    # Journal reconciliation still runs once at startup because trade-fill
    # rehydration is lifecycle-bound, not periodic.
    asyncio.create_task(_warm_journal_reconciliation_on_startup())

    yield

    # Shutdown
    if ib_pool:
        await ib_pool.disconnect_all()
    logger.info("Radon API shut down")


app = FastAPI(title="Radon API", version="1.0.0", lifespan=lifespan)
app.include_router(historical_router)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://.*\.radon\.run|http://localhost:3000|http://127\.0\.0\.1:3000",
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth middleware — protect all routes except /health and internal ticket validation
AUTH_EXEMPT_PATHS = {"/health", "/ws-ticket/validate", "/docs", "/openapi.json"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require Clerk JWT for all endpoints except exempted paths and localhost."""
    if request.url.path in AUTH_EXEMPT_PATHS:
        return await call_next(request)

    # Skip auth for genuine server-to-server calls from localhost or tailnet
    # (Next.js → FastAPI; cloud-thin laptop dev → Hetzner FastAPI over Tailscale).
    # Requests forwarded through the public reverse proxy are NOT trusted.
    # Checked BEFORE the JWKS-configured gate so a server-to-server call never
    # depends on Clerk being configured.
    if is_trusted_local_request(request):
        return await call_next(request)

    # API key auth — scoped to historical/contract endpoints only
    service_identity = verify_api_key(request)
    if service_identity:
        request.state.user = service_identity
        return await call_next(request)

    # FAIL CLOSED: an untrusted/public request that reached here has no
    # server-to-server bypass and no API key, so it MUST present a valid Clerk
    # JWT. If CLERK_JWKS_URL is unset we cannot verify one — that is a deploy
    # misconfiguration, NOT an open door. Returning call_next() here would make
    # all 47 routes (orders/place, pi/exec, admin/*) world-callable through the
    # public Caddy proxy on a single missing env var (the "middleware is the
    # perimeter" / world-callable-/api/* incident class). Deny with 503. The
    # only way to disable auth is the explicit, loud, dev-only opt-in below —
    # never set RADON_AUTH_DISABLED on a public deployment.
    if not os.environ.get("CLERK_JWKS_URL"):
        if os.environ.get("RADON_AUTH_DISABLED") == "1":
            return await call_next(request)
        return JSONResponse(
            status_code=503,
            content={"detail": "Authentication unavailable: server auth is not configured."},
        )

    try:
        payload = await verify_clerk_jwt(request)
        request.state.user = payload
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    return await call_next(request)


# ---------------------------------------------------------------------------
# Secret scrubbing for error responses
# ---------------------------------------------------------------------------
# Error paths interpolate raw upstream text (str(exc), subprocess stderr,
# result.error) into HTTPException detail at ~64 sites. A libsql/Turso failure,
# an IB error, or a subprocess crash can carry the Turso URL, an auth token, or
# an IB account id in that text — which would then ride out to the client (the
# same information-disclosure class as the /health account-id leak). Rather than
# scrub at 64 call sites, scrub once at the single chokepoint every raised
# HTTPException flows through: a custom handler.
_SECRET_SCRUB_PATTERNS = [
    (re.compile(r"libsql://[^\s'\"]+", re.IGNORECASE), "[redacted-db-url]"),
    (re.compile(r"https://[a-z0-9.-]+\.turso\.io[^\s'\"]*", re.IGNORECASE), "[redacted-db-url]"),
    (re.compile(r"(auth[_-]?token|authorization|bearer)(\s*[=:]\s*)\S+", re.IGNORECASE), r"\1\2[redacted]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]*"), "[redacted-jwt]"),
    (re.compile(r"\bU\d{6,}\b"), "[redacted-account]"),
]


def _scrub_secrets(value):
    """Redact Turso URLs, auth tokens/JWTs, and IB account ids from any string
    (recursing into dict/list detail payloads) before it reaches the client."""
    if isinstance(value, str):
        for pattern, repl in _SECRET_SCRUB_PATTERNS:
            value = pattern.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {k: _scrub_secrets(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_secrets(v) for v in value]
    return value


@app.exception_handler(StarletteHTTPException)
async def scrubbed_http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Replaces the default HTTPException handler so the detail (which often
    carries raw upstream error text) is scrubbed of secrets before it is
    serialized to the client. Status + headers are preserved."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": _scrub_secrets(exc.detail)},
        headers=getattr(exc, "headers", None),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_cache(path: Path) -> Optional[dict]:
    """Read a JSON cache file, return None if missing/corrupt."""
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _write_cache(path: Path, data: dict) -> None:
    """Write JSON to cache file atomically via temp file + os.replace().

    The Turso snapshot + service_health mirror that used to piggyback here
    lives in the scan subprocesses now (db/scan_mirror.py) — synchronous
    libsql writes on this process starved the event loop even from a worker
    thread. See feedback_no_sync_libsql_on_fastapi_event_loop.
    """
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=".cache_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _today_et_str() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _is_market_open_now_et() -> bool:
    try:
        from zoneinfo import ZoneInfo
        et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
    except Exception:
        et = datetime.now()
    if et.weekday() >= 5:
        return False
    minutes = et.hour * 60 + et.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60


def _scan_time_to_et_date(scan_time: str) -> Optional[str]:
    try:
        ts = datetime.fromisoformat(scan_time.replace("Z", "+00:00"))
        from zoneinfo import ZoneInfo
        return ts.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return None


def _is_gex_cache_stale(data: Optional[dict], *, now_ts: Optional[float] = None, current_market_open: Optional[bool] = None, today_et: Optional[str] = None) -> bool:
    """Market-aware GEX staleness check mirrored from the Next route logic."""
    if not data or not isinstance(data, dict):
        return True

    scan_time = data.get("scan_time")
    if not isinstance(scan_time, str) or not scan_time:
        return True

    today_et = today_et or _today_et_str()
    current_market_open = _is_market_open_now_et() if current_market_open is None else current_market_open

    session_date = _scan_time_to_et_date(scan_time)
    if not session_date or session_date != today_et:
        return True

    if not current_market_open:
        return False

    try:
        scan_dt = datetime.fromisoformat(scan_time.replace("Z", "+00:00"))
        current_ts = now_ts if now_ts is not None else time.time()
        return (current_ts - scan_dt.timestamp()) > 60
    except Exception:
        return True


def _is_cri_cache_stale(data: Optional[dict], *, mtime_ms: Optional[float] = None, now_ts: Optional[float] = None, current_market_open: Optional[bool] = None, today_et: Optional[str] = None) -> bool:
    """Market-aware CRI staleness check mirrored from the Next route logic."""
    if not data or not isinstance(data, dict):
        return True

    today_et = today_et or _today_et_str()
    current_market_open = _is_market_open_now_et() if current_market_open is None else current_market_open

    data_date = data.get("date")
    if not isinstance(data_date, str) or data_date != today_et:
        return True

    market_open_flag = data.get("market_open")
    if market_open_flag is False and not current_market_open:
        return False

    if mtime_ms is None:
        scan_time = data.get("scan_time")
        if not isinstance(scan_time, str) or not scan_time:
            return True
        try:
            mtime_ms = datetime.fromisoformat(scan_time.replace("Z", "+00:00")).timestamp() * 1000
        except Exception:
            return True

    current_ms = (now_ts if now_ts is not None else time.time()) * 1000
    return (current_ms - mtime_ms) > 60_000


async def _warm_journal_reconciliation_on_startup() -> None:
    logger.info("Journal startup reconcile triggered")
    # raw=True: ib_reconcile.py emits a status report on stdout, not
    # JSON. The default runner crashes on the first '{' in the report.
    result = await run_script_raw("ib_reconcile.py", [], timeout=120)
    if result.ok:
        logger.info("Journal startup reconcile complete")
    else:
        logger.warning("Journal startup reconcile failed: %s", result.error)


# Phase 6: _warm_cri_cache_on_startup and _warm_gex_cache_on_startup were
# deleted alongside their lifespan-task call sites. The Turso embedded
# replica keeps both caches current without any FastAPI-side bootstrap;
# scheduled refreshes are owned by the radon-services container (Hetzner
# mode) or the laptop launchd plists (local mode).


def _atomic_save(path: str, data: dict) -> str:
    """Use the project's atomic_save for portfolio/orders files."""
    from utils.atomic_io import atomic_save
    return atomic_save(path, data)


def _coerce_float(value: object) -> Optional[float]:
    """Parse an arbitrary value into a finite float."""
    if isinstance(value, (int, float)):
        return float(value) if value == value and value != float("inf") and value != float("-inf") else None
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else None
    return None


def _coerce_date(value: object) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None


def _normalize_risk_reversal_series(raw: object) -> List[dict]:
    """Normalize UW historical risk reversal payloads into a stable list."""
    rows: Iterable[object] = []
    if isinstance(raw, dict):
        raw_rows = raw.get("data")
        if isinstance(raw_rows, list):
            rows = raw_rows
    elif isinstance(raw, list):
        rows = raw

    normalized: List[dict] = []
    seen_dates: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        date = row.get("date")
        value = row.get("risk_reversal")
        if not isinstance(date, str):
            continue
        numeric = _coerce_float(value)
        if numeric is None:
            continue
        # Skip invalid or duplicate dates; keep the latest row for a date.
        if date in seen_dates:
            continue
        seen_dates.add(date)
        normalized.append({"date": date, "value": numeric})

    normalized.sort(key=lambda item: item["date"])
    return normalized


def _extract_expiry_candidates(raw: object) -> List[str]:
    rows: Iterable[object] = []
    if isinstance(raw, dict):
        raw_rows = raw.get("data")
        if isinstance(raw_rows, list):
            rows = raw_rows
    elif isinstance(raw, list):
        rows = raw

    candidates: List[str] = []
    for row in rows:
        if isinstance(row, dict):
            expiry = row.get("expiry")
            if not isinstance(expiry, str):
                expiry = row.get("expires")
            if not isinstance(expiry, str):
                expiry = row.get("expiration")
            if isinstance(expiry, str) and expiry not in candidates:
                candidates.append(expiry)
    return candidates


def _pick_preferred_expiry(raw: object, now: Optional[datetime] = None) -> Optional[str]:
    """Choose the nearest expiry that is today or newer, else the most recent expiry."""
    candidates = _extract_expiry_candidates(raw)
    if not candidates:
        return None

    parsed: List[Tuple[str, datetime]] = []
    for expiry in candidates:
        parsed_date = _coerce_date(expiry)
        if parsed_date is None:
            continue
        parsed.append((expiry, parsed_date))

    if not parsed:
        return candidates[0]

    current = now or datetime.now(timezone.utc)
    future_candidates = [(expiry, expiry_date) for expiry, expiry_date in parsed if expiry_date.date() >= current.date()]
    if future_candidates:
        return min(future_candidates, key=lambda item: item[1])[0]
    return max(parsed, key=lambda item: item[1])[0]


def _normalize_expiry_string(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None

    parsed = _coerce_date(value)
    if parsed is not None:
        return parsed.date().isoformat()

    compact = value.strip()
    if len(compact) == 8 and compact.isdigit():
        try:
            return datetime.strptime(compact, "%Y%m%d").date().isoformat()
        except ValueError:
            return None

    return None


def _sort_expiry_candidates(expiries: Iterable[str], now: Optional[datetime] = None) -> List[str]:
    parsed: List[Tuple[str, datetime]] = []
    seen: set[str] = set()
    for expiry in expiries:
        normalized = _normalize_expiry_string(expiry)
        if normalized is None or normalized in seen:
            continue
        parsed_date = _coerce_date(normalized)
        if parsed_date is None:
            continue
        seen.add(normalized)
        parsed.append((normalized, parsed_date))

    if not parsed:
        return []

    current = now or datetime.now(timezone.utc)
    future = sorted(
        (item for item in parsed if item[1].date() >= current.date()),
        key=lambda item: item[1],
    )
    past = sorted(
        (item for item in parsed if item[1].date() < current.date()),
        key=lambda item: item[1],
        reverse=True,
    )
    return [expiry for expiry, _ in [*future, *past]]


def _extract_ib_expiry_candidates(raw: object) -> List[str]:
    rows: Iterable[object] = raw if isinstance(raw, list) else []
    candidates: List[str] = []
    for row in rows:
        expirations = getattr(row, "expirations", None)
        if not expirations:
            continue
        for expiry in expirations:
            normalized = _normalize_expiry_string(expiry)
            if normalized and normalized not in candidates:
                candidates.append(normalized)
    return candidates


async def _fetch_ib_expiry_candidates(ticker: str) -> List[str]:
    normalized_ticker = ticker.upper()
    if ib_pool is None:
        return []

    attempts = [
        ("NASDAQ", "IND"),
        ("CBOE", "IND"),
        ("SMART", "IND"),
        ("", "IND"),
    ]
    for exchange, sec_type in attempts:
        try:
            async with ib_pool.acquire("data") as client:
                chains = await asyncio.to_thread(
                    _fetch_ib_index_option_chain,
                    client,
                    normalized_ticker,
                    exchange,
                    sec_type,
                )
            candidates = _sort_expiry_candidates(_extract_ib_expiry_candidates(chains))
            if candidates:
                logger.info(
                    "Internals skew: IB expiries for %s resolved via %s/%s (%d candidates)",
                    normalized_ticker,
                    exchange or "default",
                    sec_type,
                    len(candidates),
                )
                return candidates
        except Exception as exc:
            logger.warning(
                "Internals skew: IB expiry lookup failed for %s via %s/%s: %s",
                normalized_ticker,
                exchange or "default",
                sec_type,
                exc,
            )
    return []


def _preferred_index_exchange(ticker: str) -> str:
    return "NASDAQ" if ticker.upper() == "NDX" else "CBOE"


def _fetch_ib_index_option_chain(client: Any, ticker: str, exchange: str, sec_type: str) -> object:
    if sec_type != "IND":
        return client.get_option_chain(ticker, exchange, sec_type)

    contract = Index(symbol=ticker, exchange=exchange or _preferred_index_exchange(ticker))
    qualified = client.qualify_contract(contract)
    return client.ib.reqSecDefOptParams(ticker, exchange, sec_type, qualified.conId)


def _prepend_expiry(candidates: List[str], expiry: Optional[str]) -> List[str]:
    normalized = _normalize_expiry_string(expiry)
    if normalized is None:
        return candidates
    return [normalized, *[candidate for candidate in candidates if candidate != normalized]]


def _limit_expiry_candidates(candidates: List[str], max_expiries: int) -> List[str]:
    if max_expiries <= 0 or len(candidates) <= max_expiries:
        return candidates
    if max_expiries == 1:
        return candidates[:1]

    last_index = len(candidates) - 1
    selected_indices = {0, last_index}
    for slot in range(1, max_expiries - 1):
        index = round(slot * last_index / (max_expiries - 1))
        selected_indices.add(index)

    return [candidates[index] for index in sorted(selected_indices)[:max_expiries]]


def _build_internals_skew_cache_path(
    nq_ticker: str,
    spx_ticker: str,
    timeframe: str,
    nq_delta: int,
    spx_delta: int,
    nq_expiry: Optional[str],
    spx_expiry: Optional[str],
) -> Path:
    key = (
        f"v7-uw-skew-history|{nq_ticker}|{spx_ticker}|{timeframe}|"
        f"{nq_delta}|{spx_delta}|{nq_expiry or ''}|{spx_expiry or ''}"
    )
    key_hash = hashlib.md5(key.encode()).hexdigest()[:16]
    return INTERNALS_SKEW_CACHE_DIR / f"internals_skew_history_{key_hash}.json"


def _read_internals_skew_cache(path: Path) -> Optional[dict]:
    cached = _read_cache(path)
    if not isinstance(cached, dict):
        return None

    generated_at = cached.get("generated_at")
    if not isinstance(generated_at, str):
        return None

    parsed = _coerce_date(generated_at)
    if parsed is None:
        return None

    age_seconds = (datetime.now(timezone.utc) - parsed.replace(tzinfo=timezone.utc)).total_seconds()
    if age_seconds > INTERNALS_SKEW_CACHE_TTL_SECONDS:
        return None
    return cached


def _internals_skew_cache_payload(
    nq_ticker: str,
    spx_ticker: str,
    timeframe: str,
    nq_delta: int,
    spx_delta: int,
    nq_expiry: Optional[str],
    spx_expiry: Optional[str],
    nq_rows: List[dict],
    spx_rows: List[dict],
    used_nq_expiries: List[str],
    used_spx_expiries: List[str],
) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "expiry_discovery": "Unusual Whales",
            "skew_history": "Unusual Whales",
        },
        "nq": {
            "ticker": nq_ticker.upper(),
            "expiry": used_nq_expiries[0] if used_nq_expiries else None,
            "expiries": used_nq_expiries,
            "delta": nq_delta,
            "timeframe": timeframe,
            "data": nq_rows,
        },
        "spx": {
            "ticker": spx_ticker.upper(),
            "expiry": used_spx_expiries[0] if used_spx_expiries else None,
            "expiries": used_spx_expiries,
            "delta": spx_delta,
            "timeframe": timeframe,
            "data": spx_rows,
        },
    }


def _merge_risk_reversal_series(series_rows: Iterable[List[dict]]) -> List[dict]:
    merged: dict[str, float] = {}
    for rows in series_rows:
        for row in rows:
            date = row.get("date")
            value = row.get("value")
            if not isinstance(date, str) or not isinstance(value, (int, float)):
                continue
            if date not in merged:
                merged[date] = float(value)
    return [{"date": date, "value": merged[date]} for date in sorted(merged)]


def _series_span_days(rows: List[dict]) -> int:
    if len(rows) < 2:
        return 0
    start = _coerce_date(rows[0].get("date"))
    end = _coerce_date(rows[-1].get("date"))
    if start is None or end is None:
        return 0
    return (end.date() - start.date()).days


def _needs_deeper_backfill(rows: List[dict], timeframe: str) -> bool:
    if not rows:
        return True
    span_days = _series_span_days(rows)
    normalized = timeframe.upper().strip()
    if normalized in {"5Y", "ALL"}:
        return span_days < 700
    if normalized == "2Y":
        return span_days < 400
    return False


async def _resolve_expiry_candidates(
    ticker: str,
    expiry: Optional[str] = None,
) -> Tuple[List[str], List[str], str]:
    normalized_ticker = ticker.upper()
    uw_candidates: List[str] = []
    try:
        with UWClient() as client:
            expiry_breakdown = client.get_expiry_breakdown(normalized_ticker)
        uw_candidates = _sort_expiry_candidates(_extract_expiry_candidates(expiry_breakdown))
    except Exception:
        uw_candidates = []

    uw_candidates = _prepend_expiry(uw_candidates, expiry)
    if uw_candidates:
        return [], uw_candidates, "uw"

    raise HTTPException(status_code=422, detail=f"No expiry available for {normalized_ticker}")


def _compose_expiry_candidates(
    ib_candidates: List[str],
    uw_candidates: List[str],
    max_expiries: int,
) -> List[str]:
    if not ib_candidates:
        return _limit_expiry_candidates(uw_candidates, max_expiries)
    if not uw_candidates:
        return _limit_expiry_candidates(ib_candidates, max_expiries)

    ib_budget = min(4, max_expiries)
    selected = _limit_expiry_candidates(ib_candidates, ib_budget)
    remaining = max_expiries - len(selected)
    if remaining <= 0:
        return selected

    uw_only = [candidate for candidate in uw_candidates if candidate not in selected]
    return selected + _limit_expiry_candidates(uw_only, remaining)


async def _fetch_risk_reversal_history(
    ticker: str,
    timeframe: str,
    delta: int,
    expiry: Optional[str] = None,
    max_expiries: int = 8,
) -> Tuple[List[dict], List[str], str]:
    normalized_ticker = ticker.upper()
    ib_candidates, uw_candidates, expiry_source = await _resolve_expiry_candidates(normalized_ticker, expiry)
    selected_candidates = _compose_expiry_candidates(ib_candidates, uw_candidates, max_expiries)

    last_error: Optional[BaseException] = None
    merged_rows: List[List[dict]] = []
    used_expiries: List[str] = []
    requested_expiry = _normalize_expiry_string(expiry)

    for candidate_expiry in selected_candidates:
        try:
            with UWClient() as client:
                payload = client.get_historical_risk_reversal_skew(
                    normalized_ticker,
                    expiry=candidate_expiry,
                    timeframe=timeframe,
                    delta=delta,
                )
            rows = _normalize_risk_reversal_series(payload)
            if rows:
                merged_rows.append(rows)
                used_expiries.append(candidate_expiry)
        except UWNotFoundError as exc:
            last_error = exc
            if requested_expiry and candidate_expiry == requested_expiry:
                continue
        except UWAPIError as exc:
            last_error = exc
            continue

    merged = _merge_risk_reversal_series(merged_rows)
    if "uw" in expiry_source and _needs_deeper_backfill(merged, timeframe):
        extra_candidates = _limit_expiry_candidates(
            [candidate for candidate in uw_candidates if candidate not in selected_candidates],
            12,
        )
        for candidate_expiry in extra_candidates:
            try:
                with UWClient() as client:
                    payload = client.get_historical_risk_reversal_skew(
                        normalized_ticker,
                        expiry=candidate_expiry,
                        timeframe=timeframe,
                        delta=delta,
                    )
                rows = _normalize_risk_reversal_series(payload)
                if rows:
                    merged_rows.append(rows)
                    used_expiries.append(candidate_expiry)
            except UWAPIError as exc:
                last_error = exc
                continue
        merged = _merge_risk_reversal_series(merged_rows)

    if merged:
        return merged, used_expiries, expiry_source

    if last_error is None:
        raise HTTPException(status_code=502, detail=f"Failed to fetch skew history for {normalized_ticker}")
    raise HTTPException(
        status_code=502,
        detail=getattr(last_error, "args", (f"Failed to fetch skew history for {normalized_ticker}",))[0],
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health(request: Request):
    # /health is auth-exempt and reachable from the public internet via Caddy's
    # `handle_path /api/ib/*`. Untrusted (proxied/public) callers get liveness
    # only — never IB auth/connection state, account IDs, restart backoff, or
    # internal topology. Short-circuit BEFORE check_ib_gateway so an internet
    # GET can't drive its pool-reconnect / heal side effects.
    if not is_trusted_local_request(request):
        return {"status": "ok"}

    pool_status = ib_pool.status() if ib_pool else None
    # Pass the pool itself so the auth-state transition handler can autorecover
    # from the documented "pool stuck after 2FA" failure mode without an
    # operator step. See feedback_ib_pool_stuck_after_2fa.md.
    #
    # Bound the probe: check_ib_gateway can block for tens of seconds when the
    # pool is reconnecting (see HEALTH_GATEWAY_PROBE_TIMEOUT_SECS). On timeout or
    # any error we fall back to a fast "degraded" gateway dict (probe_timed_out)
    # rather than hanging the endpoint. The payload SHAPE stays identical so the
    # web IBStatusContext / admin panel keep parsing the same keys. Recovery is
    # NOT done here — the unbounded _ib_recovery_heartbeat_loop owns it.
    try:
        gw = await asyncio.wait_for(
            check_ib_gateway(pool_status=pool_status, pool=ib_pool),
            timeout=HEALTH_GATEWAY_PROBE_TIMEOUT_SECS,
        )
    except Exception as exc:  # defensive: never hang or 500 /health
        timed_out = isinstance(exc, asyncio.TimeoutError)
        logger.warning(
            "/health gateway probe %s after %.1fs; returning degraded status",
            "timed out" if timed_out else f"raised {type(exc).__name__}",
            HEALTH_GATEWAY_PROBE_TIMEOUT_SECS,
        )
        gw = {
            "port_listening": False,
            "auth_state": "unknown",
            "service_state": "unknown",
            "container_state": "unknown",
            "upstream_dead": False,
            "probe_timed_out": True,
        }
    return {
        "status": "ok",
        "test_mode": test_mode,
        "ib_gateway": gw,
        "ib_pool": pool_status or {},
        "uw": uw_available,
    }


async def _measure_event_loop_lag_ms() -> float:
    """One timed call_soon roundtrip on the running loop (DUR-12).

    Microseconds when the loop is healthy; a loop starved by blocking work
    (the libsql dual-write wedge class) shows milliseconds-to-seconds.
    Never blocks — the await IS the measurement.
    """
    loop = asyncio.get_running_loop()
    queued_at = loop.time()
    woke = loop.create_future()
    loop.call_soon(woke.set_result, None)
    await woke
    return (loop.time() - queued_at) * 1000.0


@app.get("/health/lite")
async def health_lite():
    """Side-effect-free, account-free coarse IB state for high-frequency pollers
    (the standalone health daemon + the host-metrics sampler).

    Unlike /health, this passes pool=None: it must NEVER trigger
    handle_auth_state_transition / pool.reconnect_all(). The 2FA-recovery
    heartbeat deliberately stays on /health (driven by the operator's 15s admin
    poll); a frequently-polling daemon hitting a mutating endpoint would perturb
    the very recovery it observes. The payload is coarse on purpose — never
    managed_accounts (IBKR account IDs), ports, restart backoff, or topology.

    ``loop_lag_ms`` is the event-loop health signal the host-metrics sampler
    persists every minute (scripts/host_metrics_sampler.py).

    NOT in AUTH_EXEMPT_PATHS: the in-box daemon reaches it from loopback (covered
    by the bypass); public callers via Caddy /api/ib/health/lite get 401.
    """
    loop_lag_ms = await _measure_event_loop_lag_ms()
    pool_status = ib_pool.status() if ib_pool else None
    gw = await check_ib_gateway(pool_status=pool_status, pool=None)
    return {
        "status": "ok",
        "auth_state": gw.get("auth_state", "unknown"),
        "service_state": gw.get("service_state", "unknown"),
        "upstream_dead": gw.get("upstream_dead", False),
        "port_listening": gw.get("port_listening", False),
        "loop_lag_ms": round(loop_lag_ms, 3),
    }


@app.post("/ws-ticket")
async def get_ws_ticket(payload: dict = Depends(verify_clerk_jwt)):
    """Issue a short-lived ticket for WebSocket authentication."""
    ticket = create_ticket(payload["sub"])
    return {"ticket": ticket}


@app.post("/ws-ticket/validate")
async def validate_ws_ticket(request: Request):
    """Validate a WebSocket ticket (called by the Node.js relay). Internal only."""
    body = await request.json()
    ticket = body.get("ticket", "")
    user_id = validate_ticket(ticket)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired ticket")
    return {"user_id": user_id}


@app.post("/ib/restart")
async def ib_restart():
    """Restart IB Gateway via IBC service, then reconnect pool.

    Honors the restart backoff (1m → 60m capped) when prior attempts haven't
    completed login. Use POST /ib/reset-backoff after approving 2FA to retry
    immediately.
    """
    result = await restart_ib_gateway(pool=ib_pool)
    if not result.get("restarted"):
        # Surface deferred (backoff) and unauthenticated outcomes as 503 so the
        # caller treats them as failure, but include the structured payload for
        # operator follow-up.
        raise HTTPException(status_code=503, detail=result)

    # Reconnect pool after Gateway restart
    if ib_pool:
        await ib_pool.disconnect_all()
        pool_status = await ib_pool.connect_all()
        result["pool"] = pool_status

    return result


@app.post("/ib/reset-backoff")
async def ib_reset_backoff():
    """Clear restart backoff state. Operator path: 'I just approved 2FA, try now'."""
    return reset_restart_backoff()


# ---------------------------------------------------------------------------
# Operator admin — service control (systemd-backed)
# ---------------------------------------------------------------------------

@app.get("/admin/services")
async def admin_services_list():
    """List radon-* systemd units with current load/active/sub state.

    On non-systemd hosts (laptop dev), returns the placeholder catalogue with
    ``supported=False`` so the UI can render a graceful "not controllable
    from here" state. Status payload is identical to the systemd path.
    """
    supported = admin_services.is_systemd_available()
    units = await admin_services.list_units_with_status()
    return {
        "supported": supported,
        "units": [u.to_dict() for u in units],
    }


@app.post("/admin/services/{unit}/{action}")
async def admin_service_action(unit: str, action: str):
    """Run ``systemctl <action> <unit>``. Allowlist-gated to radon-* units."""
    result = await admin_services.control_unit(unit, action)
    if not result.ok:
        if result.returncode == admin_services.PUSH_LOCK_HELD_RC:
            raise HTTPException(status_code=409, detail=result.to_dict())
        raise HTTPException(status_code=400 if result.returncode == -1 else 502, detail=result.to_dict())
    return result.to_dict()


@app.post("/admin/stack/restart")
async def admin_stack_restart():
    """Run the operator CLI's ``radon restart`` to cycle every radon-* unit.

    The TCP response may not survive the restart (FastAPI itself is one of
    the units cycled). The Next.js route handles this by treating a dropped
    request after ~2s as "restart in flight, poll /health to verify".
    """
    result = await admin_services.restart_full_stack()
    if not result.ok:
        raise HTTPException(status_code=400 if result.returncode == -1 else 502, detail=result.to_dict())
    return result.to_dict()


# ---------------------------------------------------------------------------
# Phase 1: Stateless UW-only endpoints (subprocess-based)
# ---------------------------------------------------------------------------

@app.post("/scan")
async def scan():
    """Run watchlist scanner (scanner.py --top 25)."""
    result = await run_script("scanner.py", ["--top", "25"], timeout=120)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    _write_cache(DATA_DIR / "scanner.json", result.data)
    return result.data


@app.post("/discover")
async def discover():
    """Run market-wide discovery (discover.py --min-alerts 1)."""
    result = await run_script("discover.py", ["--min-alerts", "1"], timeout=120)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if result.data and result.data.get("error"):
        raise HTTPException(status_code=400, detail=result.data["error"])
    _write_cache(DATA_DIR / "discover.json", result.data)
    return result.data


@app.post("/flow-analysis")
async def flow_analysis():
    """Run portfolio flow analysis (flow_analysis.py)."""
    result = await run_script("flow_analysis.py", timeout=120)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    _write_cache(DATA_DIR / "flow_analysis.json", result.data)
    return result.data


_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
_FLOW_REPORTS_DIR = DATA_DIR / "flow_reports"


@app.get("/flow-analysis/{ticker}")
async def get_flow_report(ticker: str):
    """Return the most recent flow report for a single ticker.

    Reads the cached report on disk; never triggers a fresh scan. The Next.js
    layer compares the cache age against `flowReportStaleness` to decide
    whether to issue a POST.
    """
    upper = ticker.upper()
    if not _TICKER_RE.match(upper):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    cache_path = _FLOW_REPORTS_DIR / f"{upper}.json"
    if not cache_path.exists():
        raise HTTPException(status_code=404, detail=f"No flow report cached for {upper}")
    try:
        return json.loads(cache_path.read_text())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read cache: {exc}")


@app.post("/flow-analysis/{ticker}")
async def run_flow_report(ticker: str):
    """Run a fresh flow scan for a single ticker, persist, and return."""
    upper = ticker.upper()
    if not _TICKER_RE.match(upper):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    result = await run_script("flow_report.py", [upper], timeout=120)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if not result.data:
        raise HTTPException(status_code=502, detail="Flow report returned no data")
    if isinstance(result.data, dict) and result.data.get("error"):
        raise HTTPException(status_code=502, detail=result.data["error"])

    # Don't persist a structurally degraded report. The aggregate-only
    # check (2026-05-14 fix) caught reports where the WHOLE 5-day window
    # was zero, but it let through partial degradations where 1-2 days
    # silently swallowed a UW rate-limit and the OTHER days carried the
    # cross-day total past `> 0`. EWY surfaced 2026-05-15 with two recent
    # days showing "NO DATA" while UW was healthy — three good days
    # (1,499 total prints) waved the bad report through this guard.
    #
    # Two checks now:
    #   (a) aggregate `analysis.num_prints > 0` — the original guard
    #   (b) every per-day darkpool row covering a real trading day has
    #       `num_prints > 0`. If any trading-day slot is zero, we
    #       assume a per-day call was swallowed and refuse the cache
    #       write. The previous valid cache stays served.
    if not _flow_report_is_cacheable(result.data, upper):
        return result.data
    _write_cache(_FLOW_REPORTS_DIR / f"{upper}.json", result.data)
    return result.data


def _flow_report_is_cacheable(report: dict, ticker: str) -> bool:
    """Gate the flow-report cache write on structural validity.

    Refuses to write when the aggregate is empty OR any per-day darkpool
    row covering a real trading day shows zero prints. The latter is the
    signal that a per-day call was swallowed by `fetch_flow.py`'s retry
    layer — even after the 2026-05-15 narrowing, a sustained rate-limit
    can still bubble up as an empty day; that report is unsafe to cache.
    """
    aggregate_prints = (report.get("analysis") or {}).get("num_prints") or 0
    if aggregate_prints <= 0:
        logger.warning(
            "Skipping flow_reports cache for %s: aggregate num_prints=0 (transient UW failure)",
            ticker,
        )
        return False

    daily_rows = ((report.get("dark_pool") or {}).get("daily") or [])
    blank_dates: list[str] = []
    for row in daily_rows:
        date_str = (row.get("date") or "").strip()
        if not date_str:
            continue
        if (row.get("num_prints") or 0) > 0:
            continue
        # Only flag dates that are real US trading days. UW will legitimately
        # return [] for weekends / holidays / pre-data-availability dates.
        try:
            year, month, day = (int(p) for p in date_str.split("-"))
            from utils.market_calendar import _is_trading_day  # local import — keeps top tidy
            if _is_trading_day(datetime(year, month, day)):
                blank_dates.append(date_str)
        except Exception:
            continue

    if blank_dates:
        logger.warning(
            "Skipping flow_reports cache for %s: %d trading day(s) returned zero prints (%s) — likely partial UW outage",
            ticker, len(blank_dates), ",".join(blank_dates),
        )
        return False
    return True


@app.get("/attribution")
async def attribution():
    """Run portfolio attribution (portfolio_attribution.py --json)."""
    result = await run_script("portfolio_attribution.py", ["--json"], timeout=15)
    if not result.ok:
        raise HTTPException(status_code=500, detail=result.error)
    return result.data


# ---------------------------------------------------------------------------
# Phase 2: IB file-writer endpoints
# ---------------------------------------------------------------------------

@app.post("/portfolio/sync")
async def portfolio_sync():
    """Sync portfolio from IB via subprocess.

    Scripts auto-allocate client IDs from subprocess range (20-49).
    Auto-restarts IB Gateway on ECONNREFUSED and retries once.
    """
    # raw=True: ib_sync.py --sync writes data/portfolio.json + emits
    # human-readable status text on stdout. The default JSON-parsing
    # runner crashes on the first '{' it finds in the status report —
    # broken since the script grew its summary banner. See
    # feedback_dont_cache_empty_results / journalctl ERROR
    # "Invalid JSON output: Extra data".
    result = await _run_ib_script_with_recovery(
        "ib_sync.py", ["--sync", "--port", str(DEFAULT_GATEWAY_PORT)], timeout=30, raw=True
    )
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    # ib_sync.py writes to data/portfolio.json; read it back
    from utils.atomic_io import verified_load
    try:
        data = verified_load(str(DATA_DIR / "portfolio.json"))
        return data
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to read synced portfolio: {e}")


@app.post("/portfolio/background-sync", status_code=202)
async def portfolio_background_sync(bg: BackgroundTasks):
    """Fire-and-forget portfolio sync."""
    bg.add_task(_bg_sync_via_subprocess)
    return {"status": "accepted"}


async def _bg_sync_via_subprocess():
    """Background task: run ib_sync.py as subprocess with auto-recovery."""
    result = await _run_ib_script_with_recovery(
        "ib_sync.py", ["--sync", "--port", str(DEFAULT_GATEWAY_PORT)], timeout=30, raw=True
    )
    if result.ok:
        logger.info("Background portfolio sync complete")
    else:
        logger.error("Background portfolio sync failed: %s", result.error)


@app.post("/orders/refresh")
async def orders_refresh():
    """Sync orders from IB via subprocess.

    Scripts auto-allocate client IDs from subprocess range (20-49).
    Auto-restarts IB Gateway on ECONNREFUSED and retries once.
    """
    if test_mode:
        return {"status": "ok", "orders": []}

    result = await _run_ib_script_with_recovery(
        "ib_orders.py", ["--sync", "--port", str(DEFAULT_GATEWAY_PORT)], timeout=30, raw=True
    )
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    # ib_orders.py writes to data/orders.json; read it back
    cache = _read_cache(DATA_DIR / "orders.json")
    if cache:
        return cache
    raise HTTPException(status_code=502, detail="Failed to read synced orders")


# ---------------------------------------------------------------------------
# Phase 3: IB order operations
# ---------------------------------------------------------------------------

@app.post("/orders/place")
async def orders_place(request: Request):
    """Place an order via IB (on-demand connection, client_id=26)."""
    body = await request.json()
    if test_mode:
        order_id, perm_id = _next_test_order_ids()
        return {
            "status": "ok",
            "orderId": order_id,
            "permId": perm_id,
            "initialStatus": "Submitted",
            "message": "Order accepted in test mode",
            "echo": body,
        }

    order_json = json.dumps(body)
    # 25s timeout accommodates: connect (~3s) + qualify (~2s) + place + the
    # 12s combo confirm-poll inside ib_place_order.py + finally-disconnect.
    # 15s was tight for combos and timed out before the script could surface
    # PendingSubmit-stuck rejections — the script then never wrote a result
    # and the route reported an "Invalid JSON output" or timeout. Combo
    # orders DAY-TIF outside RTH naturally sit longer in PendingSubmit, so
    # the script must be able to detect the no-confirm case and return an
    # error inside the FastAPI timeout window.
    result = await _run_ib_script_with_recovery(
        "ib_place_order.py", ["--json", order_json], timeout=25
    )
    if not result.ok:
        # SPX-02: log infra failures before raising so the reason survives journald
        logger.warning(
            "orders/place infra error for %s %s %s: %s",
            body.get("action", "?"),
            body.get("quantity", "?"),
            body.get("symbol", "?"),
            result.error,
        )
        raise HTTPException(status_code=502, detail=result.error)
    if result.data and result.data.get("status") == "error":
        # SPX-02: log the full structured detail (including ib_error_code / ib_error_text
        # from the grace-wait) so the reason survives journald even when IB Gateway
        # logs are encrypted (.ibgzenc).  Preserve the structured dict in the
        # HTTPException detail so radonFetch's coerceRadonErrorDetail can unwrap it
        # rather than collapsing to "[object Object]".
        error_detail = result.data
        logger.warning(
            "orders/place rejected by IB for %s %s %s: %s (ib_error_code=%s)",
            body.get("action", "?"),
            body.get("quantity", "?"),
            body.get("symbol", "?"),
            error_detail.get("message", "Order failed"),
            error_detail.get("ib_error_code"),
        )
        raise HTTPException(status_code=502, detail=error_detail)
    return result.data


@app.post("/orders/cancel")
async def orders_cancel(request: Request):
    """Cancel an open order via subprocess.

    IB scopes cancelOrder by clientId — only the clientId that placed the
    order can cancel it. The subprocess detects the original clientId and
    reconnects as that client before cancelling.
    """
    body = await request.json()
    if test_mode:
        return {
            "status": "ok",
            "message": "Cancel accepted in test mode",
            "echo": body,
        }

    order_id = body.get("orderId", 0)
    perm_id = body.get("permId", 0)

    args = ["cancel"]
    if order_id:
        args.extend(["--order-id", str(order_id)])
    if perm_id:
        args.extend(["--perm-id", str(perm_id)])

    result = await _run_ib_script_with_recovery("ib_order_manage.py", args, timeout=15)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if result.data and result.data.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.data.get("message", "Cancel failed"))
    return result.data


@app.post("/orders/modify")
async def orders_modify(request: Request):
    """Modify an open order via subprocess.

    Modify requires the original clientId that placed the order (IB scopes
    placeOrder by clientId). The subprocess detects the original clientId
    and reconnects as that client before modifying. Cancel can use the pool
    (master clientId=0 can cancel anything), but modify cannot.
    """
    body = await request.json()
    if test_mode:
        return {
            "status": "ok",
            "message": "Modify accepted in test mode",
            "echo": body,
        }

    order_id = body.get("orderId", 0)
    perm_id = body.get("permId", 0)
    new_price = body.get("newPrice")
    new_quantity = body.get("newQuantity")
    outside_rth = body.get("outsideRth")

    args = ["modify"]
    if order_id:
        args.extend(["--order-id", str(order_id)])
    if perm_id:
        args.extend(["--perm-id", str(perm_id)])
    if new_price is not None:
        args.extend(["--new-price", str(new_price)])
    if new_quantity is not None:
        args.extend(["--new-quantity", str(new_quantity)])
    if outside_rth is True:
        args.append("--outside-rth")
    elif outside_rth is False:
        args.append("--no-outside-rth")

    result = await _run_ib_script_with_recovery("ib_order_manage.py", args, timeout=15)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if result.data and result.data.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.data.get("message", "Modify failed"))
    return result.data


# ---------------------------------------------------------------------------
# Phase 4: Market data & long-running endpoints (subprocess-based)
# ---------------------------------------------------------------------------

@app.post("/cta/share")
async def cta_share():
    """Generate CTA X share report (4 cards + preview HTML). Returns output path."""
    result = await run_script("generate_cta_share.py", ["--json", "--no-open"], timeout=120)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return result.data


@app.post("/journal/reconcile")
async def journal_reconcile():
    """Run IB reconciliation to refresh reconciliation.json for journal auto-import."""
    # ib_reconcile.py writes a file and emits human-readable status on
    # stdout — use raw runner to avoid the JSON-parse crash.
    result = await run_script_raw("ib_reconcile.py", [], timeout=120)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return {"ok": True}


@app.post("/journal/rehydrate")
async def journal_rehydrate(days: int = 365):
    """Backfill trade_log.json from IB Flex Query (up to 365 days).

    Idempotent: each row carries an ib_exec_id and existing rows are
    skipped on re-run. Use this after multi-day reconcile gaps where
    in-session fills (24h window) are no longer reachable.
    """
    result = await run_script("journal_rehydrate.py", ["--days", str(days)], timeout=300)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if result.data and result.data.get("ok") is False:
        raise HTTPException(status_code=502, detail=result.data.get("error", "Rehydrate failed"))
    return result.data or {"ok": True, "imported": 0, "skipped": 0}


@app.post("/regime/scan")
async def regime_scan():
    """Run CRI scan (cri_scan.py --json). 120s timeout."""
    result = await run_script("cri_scan.py", ["--json"], timeout=120)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    _write_cache(DATA_DIR / "cri.json", result.data)
    return result.data


# ── VCG (Volatility-Credit Gap) ─────────────────────────────────────

_vcg_last_scan: float = 0.0
_vcg_scan_lock: Optional[asyncio.Lock] = None
VCG_COOLDOWN_S = 60


@app.post("/vcg/scan")
async def vcg_scan():
    """Run VCG scan (vcg_scan.py --json). 60s cooldown between scans."""
    global _vcg_last_scan, _vcg_scan_lock
    import time as _time
    if _vcg_scan_lock is None:
        _vcg_scan_lock = asyncio.Lock()
    now = _time.monotonic()
    if now - _vcg_last_scan < VCG_COOLDOWN_S:
        cached = _read_cache(DATA_DIR / "vcg.json")
        if cached:
            return cached
    async with _vcg_scan_lock:
        if _time.monotonic() - _vcg_last_scan < VCG_COOLDOWN_S:
            cached = _read_cache(DATA_DIR / "vcg.json")
            if cached:
                return cached
        result = await run_script("vcg_scan.py", ["--json"], timeout=120)
        if not result.ok:
            raise HTTPException(status_code=502, detail=result.error)
        _write_cache(DATA_DIR / "vcg.json", result.data)
        _vcg_last_scan = _time.monotonic()
        return result.data


@app.post("/vcg/share")
async def vcg_share():
    """Generate VCG X share report (4 cards + preview HTML). Returns output path."""
    result = await run_script("generate_vcg_share.py", ["--json", "--no-open"], timeout=120)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return result.data


# ── LEAP (IV Mispricing Scanner) ────────────────────────────────────

_leap_last_scan: float = 0.0
_leap_scan_lock: Optional[asyncio.Lock] = None
LEAP_COOLDOWN_S = 600  # 10 min — LEAP scans are slow + low-cadence


@app.post("/leap/scan")
async def leap_scan(preset: str = "mag7", min_gap: float = 10.0):
    """Run LEAP scan (leap_scanner_uw.py --preset X --json).

    The scanner writes data/leap.json directly; stdout is text + a summary
    rather than JSON, so we ignore run_script's parsed payload and re-read
    the cache file after the subprocess completes. 600s cooldown stops
    accidental thrash on the Unusual Whales API.
    """
    global _leap_last_scan, _leap_scan_lock
    import time as _time
    if _leap_scan_lock is None:
        _leap_scan_lock = asyncio.Lock()
    now = _time.monotonic()
    if now - _leap_last_scan < LEAP_COOLDOWN_S:
        cached = _read_cache(DATA_DIR / "leap.json")
        if cached:
            return cached
    async with _leap_scan_lock:
        if _time.monotonic() - _leap_last_scan < LEAP_COOLDOWN_S:
            cached = _read_cache(DATA_DIR / "leap.json")
            if cached:
                return cached
        result = await run_script(
            "leap_scanner_uw.py",
            ["--preset", preset, "--min-gap", str(min_gap), "--json"],
            timeout=300,
        )
        if not result.ok:
            raise HTTPException(status_code=502, detail=result.error)
        _leap_last_scan = _time.monotonic()
        # The leap scanner subprocess wrote the JSON cache atomically AND
        # recorded its own service_health[leap-scan] row (db/scan_mirror.py).
        cached = _read_cache(DATA_DIR / "leap.json")
        return cached or {"scan_time": "", "min_gap": min_gap, "results": []}


# ── Index options chain (Phase 3 — VIX et al.) ──────────────────────

_INDEX_OPTIONS_CHAIN_TIMEOUT_S = 45.0  # patched in tests


@app.get("/index-options/chain")
async def index_options_chain(symbol: str, expiry: str = ""):
    """List CBOE-listed index option contracts for `symbol`.

    Subprocess-backed (ib_chain.py --kind option) for the same reason
    /futures/chain is — cross-thread event loop deadlock on the pool's
    data client when result sets exceed ~50 contracts. VIX/SPX/NDX
    chains routinely return 1000+ contracts when expiry is unscoped.
    """
    from clients.contract_resolver import supports_index_options

    symbol_upper = symbol.upper()
    if not supports_index_options(symbol_upper):
        raise HTTPException(
            status_code=400,
            detail=f"index options not supported for {symbol_upper}; supported: VIX, SPX, NDX, RUT, XSP",
        )

    args = ["--kind", "option", "--symbol", symbol_upper]
    if expiry:
        args.extend(["--expiry", expiry])

    result = await run_script("ib_chain.py", args, timeout=_INDEX_OPTIONS_CHAIN_TIMEOUT_S)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if result.data and result.data.get("error"):
        raise HTTPException(status_code=502, detail=result.data["error"])
    return result.data or {
        "symbol": symbol_upper,
        "exchange": "CBOE",
        "tradingClass": symbol_upper,
        "expirations": [],
        "contracts": [],
        "count": 0,
    }


# ── GARCH Convergence (Cross-Asset Vol Repricing Lag) ───────────────

_garch_last_scan: float = 0.0
_garch_scan_lock: Optional[asyncio.Lock] = None
GARCH_COOLDOWN_S = 600  # 10 min — UW rate-limit + scan latency


@app.post("/garch-convergence/scan")
async def garch_convergence_scan(preset: str = "mega-tech"):
    """Run GARCH convergence scan (garch_convergence.py --preset X --json).

    Mirrors /leap/scan semantics: 600s cooldown + lock, subprocess writes
    data/garch_convergence.json directly (and records its own
    service_health[garch-scan] row), we re-read the cache file after the
    subprocess completes.

    Built-in presets: semis, mega-tech, energy, china-etf, all. File
    presets (data/presets/) also accepted.
    """
    global _garch_last_scan, _garch_scan_lock
    import time as _time
    if _garch_scan_lock is None:
        _garch_scan_lock = asyncio.Lock()
    now = _time.monotonic()
    if now - _garch_last_scan < GARCH_COOLDOWN_S:
        cached = _read_cache(DATA_DIR / "garch_convergence.json")
        if cached:
            return cached
    async with _garch_scan_lock:
        if _time.monotonic() - _garch_last_scan < GARCH_COOLDOWN_S:
            cached = _read_cache(DATA_DIR / "garch_convergence.json")
            if cached:
                return cached
        result = await run_script(
            "garch_convergence.py",
            ["--preset", preset, "--json", "--no-open"],
            timeout=180,
        )
        if not result.ok:
            raise HTTPException(status_code=502, detail=result.error)
        _garch_last_scan = _time.monotonic()
        cached = _read_cache(DATA_DIR / "garch_convergence.json")
        return cached or {"scan_time": "", "tickers": {}, "pairs": []}


# ── GEX (Gamma Exposure Levels) ─────────────────────────────────────

_gex_last_scan: float = 0.0
_gex_scan_lock: Optional[asyncio.Lock] = None
GEX_COOLDOWN_S = 60


@app.post("/gex/share")
async def gex_share():
    """Generate GEX X share report (4 cards + preview HTML). Returns output path."""
    result = await run_script("generate_gex_share.py", ["--json", "--no-open"], timeout=120)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return result.data


@app.post("/gex/scan")
async def gex_scan(ticker: str = "SPX"):
    """Run GEX scan (gex_scan.py --json --ticker X). 60s cooldown between scans."""
    global _gex_last_scan, _gex_scan_lock
    import time as _time
    if _gex_scan_lock is None:
        _gex_scan_lock = asyncio.Lock()
    now = _time.monotonic()
    if now - _gex_last_scan < GEX_COOLDOWN_S:
        cached = _read_cache(DATA_DIR / "gex.json")
        if cached:
            return cached
    async with _gex_scan_lock:
        if _time.monotonic() - _gex_last_scan < GEX_COOLDOWN_S:
            cached = _read_cache(DATA_DIR / "gex.json")
            if cached:
                return cached
        result = await run_script(
            "gex_scan.py", ["--json", "--ticker", ticker.upper()], timeout=120
        )
        if not result.ok:
            raise HTTPException(status_code=502, detail=result.error)
        _write_cache(DATA_DIR / "gex.json", result.data)
        _gex_last_scan = _time.monotonic()
        return result.data


# ── Gamma Rotation Gap (SPY/TLT cross-asset gamma) ───────────────────

_gamma_rotation_last_scan: float = 0.0
_gamma_rotation_scan_lock: Optional[asyncio.Lock] = None
GAMMA_ROTATION_COOLDOWN_S = 60


@app.post("/gamma-rotation/scan")
async def gamma_rotation_scan():
    """Run SPY/TLT Gamma Rotation Gap scan."""
    global _gamma_rotation_last_scan, _gamma_rotation_scan_lock
    import time as _time
    if _gamma_rotation_scan_lock is None:
        _gamma_rotation_scan_lock = asyncio.Lock()
    now = _time.monotonic()
    if now - _gamma_rotation_last_scan < GAMMA_ROTATION_COOLDOWN_S:
        cached = _read_cache(DATA_DIR / "gamma_rotation_gap.json")
        if cached:
            return cached
    async with _gamma_rotation_scan_lock:
        if _time.monotonic() - _gamma_rotation_last_scan < GAMMA_ROTATION_COOLDOWN_S:
            cached = _read_cache(DATA_DIR / "gamma_rotation_gap.json")
            if cached:
                return cached
        result = await run_script("gamma_rotation_gap.py", ["--json"], timeout=120)
        if not result.ok:
            raise HTTPException(status_code=502, detail=result.error)
        _write_cache(DATA_DIR / "gamma_rotation_gap.json", result.data)
        _gamma_rotation_last_scan = _time.monotonic()
        return result.data


@app.post("/regime/share")
async def regime_share():
    """Generate Regime/CRI X share report (4 cards + preview HTML). Returns output path."""
    result = await run_script("generate_regime_share.py", ["--json", "--no-open"], timeout=120)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return result.data


# ── LLM Token Expenditure Index ─────────────────────────────────────

_llm_token_index_cache: dict[str, Any] = {"data": None, "fetched_at": 0.0, "days": 0}
_LLM_TOKEN_INDEX_TTL_S = 300  # 5 min — the underlying data updates once/day


@app.get("/llm-token-index")
async def llm_token_index(days: int = Query(default=180, ge=1, le=3650)):
    """Last N days of the Radon LLM Token Expenditure Index, ASC by date.

    Cached 5 min — the daily timer writes once at 06:30 UTC so anything
    tighter is wasted DB hops. Empty table returns an empty list (NOT a
    404) so the UI can render "no data yet" gracefully until the first
    timer fires.
    """
    import time as _time
    now = _time.monotonic()
    if (
        _llm_token_index_cache["data"] is not None
        and _llm_token_index_cache["days"] == days
        and now - _llm_token_index_cache["fetched_at"] < _LLM_TOKEN_INDEX_TTL_S
    ):
        return _llm_token_index_cache["data"]

    # Bounded hrana read (db_http) — sync libsql is banned in this process
    # (GIL-holding native calls starve the event loop even from a thread).
    # `components` is intentionally omitted from the row shape so the chart
    # payload stays light.
    try:
        raw_rows = await asyncio.to_thread(
            db_http.hrana_execute,
            """
            SELECT date, index_value, raw_avg_usd, methodology_version
            FROM llm_token_index
            ORDER BY date DESC
            LIMIT ?
            """,
            (int(days),),
        )
        rows = [
            {
                "date": row[0],
                "index_value": float(row[1]),
                "raw_avg_usd": float(row[2]),
                "methodology_version": int(row[3]),
            }
            for row in raw_rows
        ]
        rows.reverse()  # ASC for chart consumption
    except Exception as exc:
        logger.warning("[llm-token-index] DB read failed: %s", exc)
        rows = []

    payload = {
        "rows": rows,
        "count": len(rows),
        "days": days,
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    _llm_token_index_cache["data"] = payload
    _llm_token_index_cache["fetched_at"] = now
    _llm_token_index_cache["days"] = days
    return payload


@app.post("/internals/share")
async def internals_share():
    """Generate internals share report using the shared CRI report builder."""
    result = await run_script("generate_regime_share.py", ["--json", "--no-open"], timeout=120)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return result.data


@app.get("/internals/skew-history")
async def internals_skew_history(
    nq_ticker: str = Query(default="NDX"),
    spx_ticker: str = Query(default="SPX"),
    timeframe: str = Query(default="5Y"),
    nq_delta: int = Query(default=25),
    spx_delta: int = Query(default=25),
    nq_expiry: Optional[str] = None,
    spx_expiry: Optional[str] = None,
):
    if not uw_available:
        raise HTTPException(status_code=503, detail="UW token is required for internals skew history")

    normalized_timeframe = timeframe.upper().strip() or "5Y"
    cache_path = _build_internals_skew_cache_path(
        nq_ticker,
        spx_ticker,
        normalized_timeframe,
        nq_delta,
        spx_delta,
        nq_expiry,
        spx_expiry,
    )
    cached = _read_internals_skew_cache(cache_path)
    if cached:
        return cached

    try:
        nq_rows, used_nq_expiries, nq_expiry_source = await _fetch_risk_reversal_history(
            nq_ticker,
            normalized_timeframe,
            nq_delta,
            nq_expiry,
            max_expiries=12,
        )
        spx_rows, used_spx_expiries, spx_expiry_source = await _fetch_risk_reversal_history(
            spx_ticker,
            normalized_timeframe,
            spx_delta,
            spx_expiry,
            max_expiries=12,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    payload = _internals_skew_cache_payload(
        nq_ticker,
        spx_ticker,
        normalized_timeframe,
        nq_delta,
        spx_delta,
        nq_expiry,
        spx_expiry,
        nq_rows,
        spx_rows,
        used_nq_expiries,
        used_spx_expiries,
    )
    payload["nq"]["expiry_source"] = nq_expiry_source
    payload["spx"]["expiry_source"] = spx_expiry_source
    _write_cache(cache_path, payload)
    return payload


@app.post("/blotter")
async def blotter_sync():
    """Run IB Flex Query for historical trades. 120s timeout."""
    result = await run_module("trade_blotter.flex_query", ["--json"], timeout=120)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    _write_cache(DATA_DIR / "blotter.json", result.data)
    return result.data


# ---------------------------------------------------------------------------
# Performance — task registry for deduplication (single-worker assumed)
# ---------------------------------------------------------------------------
_running_build: Optional[asyncio.Task] = None


async def _do_performance_rebuild() -> dict:
    """Run portfolio_performance.py and cache result."""
    result = await run_script("portfolio_performance.py", ["--json"], timeout=180)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    _write_cache(DATA_DIR / "performance.json", result.data)
    return result.data


@app.post("/performance")
async def performance_sync():
    """Run portfolio performance metrics. 180s timeout.

    If a build is already in-flight, piggybacks on it (returns same result).
    """
    global _running_build
    if _running_build is not None and not _running_build.done():
        return await _running_build
    _running_build = asyncio.create_task(_do_performance_rebuild())
    return await _running_build


@app.post("/performance/background", status_code=202)
async def performance_background():
    """Fire-and-forget performance rebuild. Returns 202 immediately.

    If a build is already in-flight, returns already_running (no duplicate).
    """
    global _running_build
    if _running_build is not None and not _running_build.done():
        return {"status": "already_running"}
    _running_build = asyncio.create_task(_do_performance_rebuild())
    return {"status": "accepted"}


@app.get("/options/chain")
async def options_chain(symbol: str, expiry: Optional[str] = None):
    """Fetch options chain for a symbol."""
    args = ["--symbol", symbol.upper()]
    if expiry:
        args.extend(["--expiry", expiry])
    result = await _run_ib_script_with_recovery("ib_option_chain.py", args, timeout=15)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if result.data and result.data.get("error"):
        raise HTTPException(status_code=502, detail=result.data["error"])
    return result.data


@app.get("/options/expirations")
async def options_expirations(symbol: str):
    """List option expirations for a symbol."""
    result = await run_script(
        "ib_option_chain.py", ["--symbol", symbol.upper()], timeout=15
    )
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if result.data and result.data.get("error"):
        raise HTTPException(status_code=502, detail=result.data["error"])
    return {"symbol": result.data.get("symbol"), "expirations": result.data.get("expirations")}


# ── Futures chain (Phase 2 — VIX et al.) ────────────────────────────

_FUTURES_CHAIN_TIMEOUT_S = 30.0  # patched in tests


def _futures_chain_cache_path(symbol_upper: str) -> Path:
    return DATA_DIR / f"futures_chain_{symbol_upper}.json"


def _is_fresh_futures_cache(cached: Optional[dict]) -> bool:
    """A cache is fresh when it was stamped on the current ET trading day
    and carries at least one contract. The listed-futures chain is static
    intraday (the front month only rolls at expiry), so a same-day cache is
    always valid to serve.
    """
    if not isinstance(cached, dict):
        return False
    if cached.get("as_of_date") != _today_et_str():
        return False
    contracts = cached.get("contracts")
    return isinstance(contracts, list) and len(contracts) > 0


def _stamp_futures_chain(data: dict, symbol_upper: str) -> dict:
    stamped = dict(data)
    stamped["symbol"] = stamped.get("symbol") or symbol_upper
    stamped["as_of"] = datetime.now(timezone.utc).isoformat()
    stamped["as_of_date"] = _today_et_str()
    stamped["stale"] = False
    return stamped


@app.get("/futures/chain")
async def futures_chain(symbol: str):
    """List listed futures contracts for a supported underlying.

    Routes through ib_chain.py (subprocess) so the request gets its
    own event loop. The pool's data client lives on a thread with its
    own loop — calling sync IB methods from asyncio.to_thread crashes
    intermittently with "There is no current event loop in thread"
    (large payloads consistently; small payloads luckily). Subprocess
    avoids the cross-thread loop deadlock entirely.

    Per-symbol disk cache + stale-on-failure: the listed-futures chain is
    static intraday, so a same-day cache is served immediately (no live
    call, no farm dependency). On a cold/cross-day miss we run the
    subprocess; if that fails we serve the last good cache (flagged stale)
    rather than surfacing a timeout to the order ticket. Only 502 when
    there is no cache at all.
    """
    from clients.contract_resolver import supports_futures

    symbol_upper = symbol.upper()
    if not supports_futures(symbol_upper):
        raise HTTPException(
            status_code=400,
            detail=f"futures not supported for {symbol_upper}; supported: VIX",
        )

    cache_path = _futures_chain_cache_path(symbol_upper)
    cached = _read_cache(cache_path)

    if _is_fresh_futures_cache(cached):
        return cached

    result = await run_script(
        "ib_chain.py",
        ["--kind", "future", "--symbol", symbol_upper],
        timeout=_FUTURES_CHAIN_TIMEOUT_S,
    )

    live_ok = (
        result.ok
        and result.data
        and not result.data.get("error")
        and isinstance(result.data.get("contracts"), list)
        and len(result.data["contracts"]) > 0
    )
    if live_ok:
        stamped = _stamp_futures_chain(result.data, symbol_upper)
        try:
            from utils.atomic_io import atomic_save
            atomic_save(str(cache_path), stamped)
        except Exception:
            pass
        return stamped

    if isinstance(cached, dict) and isinstance(cached.get("contracts"), list):
        stale = dict(cached)
        stale["stale"] = True
        return stale

    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if result.data and result.data.get("error"):
        raise HTTPException(status_code=502, detail=result.data["error"])
    return {"symbol": symbol_upper, "exchange": "CFE", "contracts": [], "count": 0}


# ── PI command surface ──────────────────────────────────────────────
# Allowlist of scripts the embedded /api/pi chat command surface is
# permitted to spawn. The Next.js layer does the argument parsing /
# normalisation (preserving the existing helpers + tests); FastAPI just
# enforces the allowlist + executes. Anything not in this set returns 400.
_PI_SCRIPT_ALLOWLIST = frozenset({
    "scanner.py",
    "discover.py",
    "evaluate.py",
    "ib_sync.py",
    "leap_scanner_uw.py",
})


@app.post("/pi/exec")
async def pi_exec(payload: dict):
    """Execute an allowlisted PI script and return raw stdout/stderr text.

    Body shape: {"script": "scanner.py", "args": ["--top", "20"], "timeout": 120}

    Returns: {"ok": bool, "stdout": str, "stderr": str, "exit_code": int|null,
              "timed_out": bool}

    The Next.js /api/pi route owns parsing + allowlisting upstream; this
    enforces the same allowlist as a defence-in-depth measure.
    """
    script = payload.get("script") if isinstance(payload, dict) else None
    if not isinstance(script, str) or not script:
        raise HTTPException(status_code=400, detail="script is required")
    if script not in _PI_SCRIPT_ALLOWLIST:
        raise HTTPException(status_code=400, detail=f"Script not allowed: {script}")

    args = payload.get("args") or []
    if not isinstance(args, list) or any(not isinstance(a, str) for a in args):
        raise HTTPException(status_code=400, detail="args must be a list of strings")

    timeout = payload.get("timeout", 120)
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="timeout must be a number")
    timeout = max(1.0, min(timeout, 600.0))

    result = await run_script_raw(script, args, timeout=timeout)
    return {
        "ok": result.ok,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
    }


@app.get("/ticker/ratings")
async def ticker_ratings(ticker: str):
    """Analyst ratings + targets for a single ticker.

    Thin passthrough to scripts/fetch_analyst_ratings.py with --json. The
    script outputs a JSON array (one entry per ticker requested); for the
    single-ticker case we unwrap and return the first element so the Next.js
    route can render it directly.
    """
    upper = ticker.upper().strip()
    if not upper:
        raise HTTPException(status_code=400, detail="ticker is required")
    result = await run_script(
        "fetch_analyst_ratings.py", [upper, "--json"], timeout=60
    )
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    payload = result.data
    if isinstance(payload, list):
        return payload[0] if payload else {}
    return payload


# ---------------------------------------------------------------------------
# IB Gateway auto-recovery
# ---------------------------------------------------------------------------

_IB_CONN_REFUSED_PATTERNS = (
    "Connect call failed",
    "ECONNREFUSED",
    "Connection refused",
    "TimeoutError",
    "API connection failed",
    "Failed to connect to IB",
    "IBConnectionError",
    "Make sure API port",
    "Connectivity between IBKR and",
    "request timed out",
)

# Cooldown: after an IB subprocess fails with a connection error, skip
# subsequent attempts for this many seconds to avoid churn.
_IB_SCRIPT_COOLDOWN_SECS = 15.0
_ib_last_failure: float = 0.0  # monotonic timestamp of last IB connection failure


def _is_ib_connection_error(error_msg: str) -> bool:
    """Check if an error message indicates IB Gateway is unreachable."""
    return any(p in (error_msg or "") for p in _IB_CONN_REFUSED_PATTERNS)


def _pool_has_any_connection() -> bool:
    """Quick check: does the pool have at least one live IB connection?

    If yes, the Gateway is up and subprocesses should be able to connect.
    If no, the Gateway is likely down — subprocess will also fail.
    """
    if not ib_pool:
        return False
    for role in ("sync", "orders", "data"):
        if ib_pool.is_connected(role):
            return True
    return False


def _should_auto_restart_ib_gateway_after_runtime_failure() -> bool:
    """Runtime subprocess failures should not churn a local launchd/IBC session.

    Startup still uses ensure_ib_gateway(); this only governs mid-session recovery.
    """
    if is_cloud_mode() or is_docker_mode():
        return False
    if is_launchd_mode():
        return False
    return True


async def _run_ib_script_with_recovery(
    script: str, args: list, timeout: float = 30, raw: bool = False
) -> ScriptResult:
    """Run an IB-dependent script with pre-flight health check and cooldown.

    Three layers of fast-fail:
    1. Cooldown: if a recent IB script failed, skip for _IB_SCRIPT_COOLDOWN_SECS
    2. Pool check: if pool is disconnected, verify Gateway before spawning
    3. Post-failure: verify Gateway health before restarting

    Pass `raw=True` for scripts that write to a file and emit
    human-readable text on stdout (ib_sync.py --sync, ib_orders.py
    --sync, ib_reconcile.py). The default JSON-parsing path crashes on
    those with "Invalid JSON output" because run_script greedily parses
    the first '{' it finds in the status text.
    """
    global _ib_last_failure
    _runner = run_script_raw if raw else run_script

    # Layer 1: Cooldown — skip if a recent failure occurred
    now = time.monotonic()
    if _ib_last_failure > 0 and (now - _ib_last_failure) < _IB_SCRIPT_COOLDOWN_SECS:
        elapsed = now - _ib_last_failure
        logger.debug(
            "Skipping %s — IB cooldown active (%.1fs since last failure, %ds cooldown)",
            script, elapsed, _IB_SCRIPT_COOLDOWN_SECS,
        )
        return ScriptResult(
            ok=False,
            error="IB Gateway connection recently failed. Retrying shortly.",
        )

    # Layer 2: Pre-flight pool check
    if not _pool_has_any_connection():
        gw_status = await check_ib_gateway()
        port_ok = gw_status.get("port_listening", False)
        upstream_dead = gw_status.get("upstream_dead", False)

        if not port_ok or upstream_dead:
            _ib_last_failure = now
            logger.warning(
                "Skipping %s — Gateway down (port=%s, upstream_dead=%s), pool disconnected",
                script, port_ok, upstream_dead,
            )
            return ScriptResult(
                ok=False,
                error="IB Gateway is not accepting connections. Check IBKR Mobile for 2FA approval.",
            )

    result = await _runner(script, args, timeout=timeout)

    # Clear cooldown on success
    if result.ok:
        _ib_last_failure = 0.0

    if not result.ok and _is_ib_connection_error(result.error):
        # Set cooldown to prevent churn from repeated failures
        _ib_last_failure = time.monotonic()

        # Verify Gateway is actually down before restarting
        gw_status = await check_ib_gateway()
        port_ok = gw_status.get("port_listening", False)
        upstream_dead = gw_status.get("upstream_dead", False)

        if port_ok and not upstream_dead:
            # Gateway is healthy — subprocess failed for other reasons
            logger.warning(
                "Script %s failed but Gateway is healthy — not restarting (cooldown %ds)",
                script, _IB_SCRIPT_COOLDOWN_SECS,
            )
            return result

        if not _should_auto_restart_ib_gateway_after_runtime_failure():
            if is_cloud_mode() or is_docker_mode():
                # Cloud/Docker manages Gateway reliability — don't attempt restart.
                mode = "cloud" if is_cloud_mode() else "Docker"
                logger.warning(
                    "IB Gateway unreachable in %s mode (port=%s, upstream_dead=%s) — not restarting (%s handles it)",
                    mode, port_ok, upstream_dead, mode,
                )
                msg = (
                    f"IB Gateway is not responding ({mode} mode). "
                    + ("Check remote host and Tailscale." if is_cloud_mode()
                       else "Docker will auto-restart the container. Check IBKR Mobile for 2FA approval.")
                )
                result = ScriptResult(ok=False, error=msg)
            else:
                logger.warning(
                    "IB Gateway unreachable in local launchd mode (port=%s, upstream_dead=%s) — not auto-restarting to avoid repeated 2FA prompts",
                    port_ok, upstream_dead,
                )
                result = ScriptResult(
                    ok=False,
                    error=(
                        "IB Gateway is not responding (local launchd mode). "
                        "Manual restart required to avoid repeated 2FA prompts. "
                        "Use ~/ibc/bin/restart-secure-ibc-service.sh and approve IBKR Mobile 2FA if prompted."
                    ),
                )
        else:
            logger.warning(
                "IB Gateway unreachable (port=%s, upstream_dead=%s), attempting auto-restart...",
                port_ok, upstream_dead,
            )
            gw_result = await restart_ib_gateway()

            if gw_result.get("restarted") and gw_result.get("port_listening"):
                logger.info("IB Gateway restarted, retrying %s", script)
                _ib_last_failure = 0.0  # Clear cooldown after successful restart
                if ib_pool:
                    await ib_pool.disconnect_all()
                    await ib_pool.connect_all()
                result = await _runner(script, args, timeout=timeout)
            else:
                logger.error("IB Gateway restart failed: %s", gw_result)
                result = ScriptResult(
                    ok=False,
                    error=f"IB Gateway is down and restart failed. {gw_result.get('error', '')}".strip()
                        + " Check IBKR Mobile for 2FA approval.",
                )

    return result


# ---------------------------------------------------------------------------
# Cash flows (deposits, withdrawals, dividends, interest, fees)
# ---------------------------------------------------------------------------

@app.get("/cash-flows")
async def cash_flows(
    days: int = 90,
    types: str = "",
):
    """Return cash transactions from the `cash_flows` Turso table.

    Reads-only — populated by `scripts/cash_flow_sync.py` which runs daily
    via the monitor_daemon `cash_flow_sync` handler. Falls back to
    `data/cash_flows.json` if the DB read fails.

    Query params:
      days  - lookback window in days, default 90
      types - comma-separated filter (e.g. "Deposit,Withdrawal"); empty = all
    """
    type_filter = {t.strip() for t in types.split(",") if t.strip()} or None
    cutoff_iso = (datetime.now(timezone.utc).date() - timedelta(days=max(1, days))).isoformat()

    rows: list[dict[str, Any]] = []
    db_error: Optional[str] = None

    # Bounded hrana read (db_http) — sync libsql is banned in this process;
    # this route runs on every /orders view, the exact hot path the 06-11
    # wedge took down.
    try:
        raw_rows = await asyncio.to_thread(
            db_http.hrana_execute,
            """
            SELECT id, date, type, amount, currency, description, raw_type, synced_at
            FROM cash_flows
            WHERE date >= ?
            ORDER BY date DESC, id DESC
            """,
            (cutoff_iso,),
        )
        for row in raw_rows:
            rows.append({
                "id": row[0],
                "date": row[1],
                "type": row[2],
                "amount": row[3],
                "currency": row[4],
                "description": row[5],
                "raw_type": row[6],
                "synced_at": row[7],
            })
    except Exception as exc:
        db_error = str(exc)
        # Fall back to JSON file
        try:
            from utils.atomic_io import verified_load
            snapshot = verified_load(str(DATA_DIR / "cash_flows.json"))
            rows = [r for r in snapshot.get("rows", []) if r.get("date", "") >= cutoff_iso]
        except Exception:
            pass

    if type_filter:
        rows = [r for r in rows if r.get("type") in type_filter]

    summary = {
        "deposits": sum(r["amount"] for r in rows if r["type"] == "Deposit"),
        "withdrawals": sum(r["amount"] for r in rows if r["type"] == "Withdrawal"),
        "dividends": sum(r["amount"] for r in rows if r["type"] == "Dividend"),
        "net": sum(r["amount"] for r in rows),
    }

    # Most-recent successful sync touch among the rows that survived the
    # date cutoff + type filter. The UI uses this to render a small
    # "synced Xh ago — Flex publishes daily (T+1)" lozenge so operators
    # who just initiated a withdrawal understand WHY the panel hasn't
    # picked it up yet. See feedback_flex_cash_transaction_lag.md —
    # CashTransaction publishes once per day with a ~1-day settlement
    # lag, so a withdrawal initiated after the 17:00 ET daemon fire
    # won't appear until the next morning's pull.
    synced_values = [r["synced_at"] for r in rows if r.get("synced_at")]
    last_synced_at = max(synced_values) if synced_values else None

    # service_health row for cash-flow-sync — surfaces the daemon's most
    # recent attempt state so the lozenge can explain WHY a synced-Xh-ago
    # reading is stale. Most common cause: IBKR Flex throttle code 1001
    # ("Statement could not be generated"). When throttle is active we
    # surface the next_attempt_at so the operator knows when fresh data
    # will land instead of guessing.
    sync_status = await asyncio.to_thread(_load_cash_flow_sync_status)

    return {
        "rows": rows,
        "count": len(rows),
        "from_date": cutoff_iso,
        "summary": summary,
        "last_synced_at": last_synced_at,
        "sync_status": sync_status,
        "db_error": db_error,  # null on success; non-null when DB read failed and we fell back
    }


def _load_cash_flow_sync_status() -> dict[str, Any]:
    """Read service_health[cash-flow-sync] and surface throttle/error context.

    Returns a payload safe for the public route to expose:

        {
          "state": "ok" | "error" | "stale" | "unknown",
          "last_attempt_at": ISO str or None,
          "next_attempt_at": ISO str or None,
          "error_summary": short human-readable message or None,
          "is_throttled": bool,
        }

    `last_attempt_at` is intentionally distinct from `last_synced_at` —
    `last_synced_at` is the freshest row's sync timestamp (the last
    SUCCESS), `last_attempt_at` is the daemon's last try (success or
    failure). When `last_attempt_at > last_synced_at`, the daemon
    attempted but didn't write new rows — usually a throttle.

    All exceptions are swallowed; the route shouldn't 500 because we
    couldn't read service_health.
    """
    payload: dict[str, Any] = {
        "state": "unknown",
        "last_attempt_at": None,
        "next_attempt_at": None,
        "error_summary": None,
        "is_throttled": False,
    }
    try:
        # Bounded hrana read; runs off-loop via asyncio.to_thread at the
        # call site. Sync libsql is banned in this process.
        rows = db_http.hrana_execute(
            """
            SELECT state, last_attempt_finished_at, last_error
            FROM service_health
            WHERE service = ?
            """,
            ("cash-flow-sync",),
        )
        row = rows[0] if rows else None
        if row is None:
            return payload
        state = row[0] or "unknown"
        last_attempt_at = row[1]
        last_error_raw = row[2]

        payload["state"] = state
        payload["last_attempt_at"] = last_attempt_at

        if last_error_raw:
            try:
                parsed = json.loads(last_error_raw)
                message = parsed.get("message") if isinstance(parsed, dict) else None
                next_attempt = parsed.get("next_attempt_at") if isinstance(parsed, dict) else None
            except Exception:
                message = str(last_error_raw)
                next_attempt = None

            if message:
                # Flex throttle is the dominant cash-flow-sync failure mode.
                # Surface a concise tag so the UI doesn't have to substring-
                # match the raw IBKR error text.
                lower = message.lower()
                payload["is_throttled"] = (
                    "throttle" in lower
                    or "code 1001" in lower
                    or "code 1018" in lower
                    or "code 1019" in lower
                )
                # Pull out the user-facing slice. The full message looks
                # like "ERR: cash flow fetch failed: Flex throttle (code
                # 1001): Statement could not be generated at this time."
                # — surface only the post-colon Flex sentence.
                if "Flex throttle" in message:
                    payload["error_summary"] = "Flex throttled by IBKR"
                elif ":" in message:
                    payload["error_summary"] = message.split(":")[-1].strip()
                else:
                    payload["error_summary"] = message

            payload["next_attempt_at"] = next_attempt
    except Exception:
        # Never let a service_health read fail the cash-flows response.
        pass
    return payload


# ---------------------------------------------------------------------------
# Short availability probe
# ---------------------------------------------------------------------------

# IB generic tick IDs for short availability:
#   46 = shortable (difficulty score: 3.0 easy / 1.5-2.5 locate / <1.5 no)
#   89 = short shares available
# Must be STREAMING (not snapshot) — per feedback_ib_snapshot_no_generic_ticks.md.
_SHORT_AVAIL_GENERIC_TICKS = "236"
_SHORT_TICK_DIFFICULTY = 46   # float field tickerId
_SHORT_TICK_SHARES = 89       # float field tickerId
_SHORT_PROBE_TIMEOUT_SECS = 6.0
_SHORTABLE_EASY_THRESHOLD = 2.5   # >= easy to borrow
_SHORTABLE_NO_THRESHOLD = 1.5     # <  no shares available

# UW short data freshness: refuse rows older than 3 trading days
_UW_SHORT_DATA_MAX_AGE_DAYS = 3


def _derive_shortability(
    difficulty: Optional[float],
    shortable_shares: Optional[float] = None,
) -> Optional[bool]:
    """Map IB tick 46 difficulty score (and/or tick 89 shares) to shortable boolean.

    When difficulty IS present:
      >= 2.5 → True (easy), < 1.5 → False (not shortable), 1.5-2.5 → None (locate-only).

    When difficulty is absent but shortable_shares is a positive number, shortable
    MUST be True — you cannot have 190M borrowable shares and "unknown" shortability
    (SPX-03 AAPL live repro: tick 89 arrived in the ~6s window, tick 46 did not).

    shortable stays None ONLY when BOTH difficulty AND shortable_shares are absent
    or shortable_shares is zero/None with no difficulty signal.
    """
    if difficulty is not None:
        if difficulty >= _SHORTABLE_EASY_THRESHOLD:
            return True
        if difficulty < _SHORTABLE_NO_THRESHOLD:
            return False
        return None  # locate-only range — neither clearly shortable nor blocked
    if shortable_shares is not None and shortable_shares > 0:
        return True
    return None


def _probe_short_ticks_in_thread(client: Any, ticker: str) -> dict:
    """Run a bounded streaming market data probe for short availability ticks.

    Uses STREAMING (not snapshot) to receive generic ticks 46 + 89.
    Polls up to _SHORT_PROBE_TIMEOUT_SECS for both fields to arrive,
    then cancels the subscription.

    Returns dict with 'difficulty' and 'shortable_shares' (both may be None).
    """
    from ib_insync import Stock

    contract = Stock(ticker, "SMART", "USD")
    try:
        qualified = client.ib.qualifyContracts(contract)
    except Exception:
        qualified = []
    if not qualified:
        return {"difficulty": None, "shortable_shares": None}

    ticker_obj = client.ib.reqMktData(qualified[0], _SHORT_AVAIL_GENERIC_TICKS, False, False)

    # Poll for tick data arrival; sleep in small increments
    poll_interval = 0.2
    elapsed = 0.0
    while elapsed < _SHORT_PROBE_TIMEOUT_SECS:
        difficulty = getattr(ticker_obj, f"tick{_SHORT_TICK_DIFFICULTY}", None)
        shares = getattr(ticker_obj, f"tick{_SHORT_TICK_SHARES}", None)
        # ib_insync stores generic ticks in the tickerId-indexed attributes;
        # fall back to direct attribute name search on the Ticker object
        if difficulty is None:
            difficulty = _read_generic_tick(ticker_obj, _SHORT_TICK_DIFFICULTY)
        if shares is None:
            shares = _read_generic_tick(ticker_obj, _SHORT_TICK_SHARES)
        if difficulty is not None:
            break
        client.ib.sleep(poll_interval)
        elapsed += poll_interval

    # Re-read after final sleep
    difficulty = _read_generic_tick(ticker_obj, _SHORT_TICK_DIFFICULTY)
    shares = _read_generic_tick(ticker_obj, _SHORT_TICK_SHARES)

    try:
        client.ib.cancelMktData(qualified[0])
    except Exception:
        pass

    return {
        "difficulty": difficulty,
        "shortable_shares": shares,
    }


def _read_generic_tick(ticker_obj: Any, tick_id: int) -> Optional[float]:
    """Read a generic tick value from an ib_insync Ticker object.

    ib_insync stores generic tick data in `ticks` list as GenericTick objects,
    and also populates named attributes like `shortableShares` (tick 89) and
    `shortable` (tick 46).
    """
    # Named shortcut attributes on Ticker
    _NAMED = {
        46: ("shortable",),
        89: ("shortableShares",),
    }
    for attr in _NAMED.get(tick_id, ()):
        val = getattr(ticker_obj, attr, None)
        if val is not None and val == val:  # exclude NaN
            try:
                return float(val)
            except (TypeError, ValueError):
                pass

    # Walk the raw ticks list
    for tick in getattr(ticker_obj, "ticks", []):
        if getattr(tick, "tickType", None) == tick_id:
            val = getattr(tick, "value", None)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
    return None


def _uw_short_data_is_fresh(raw: dict, ticker: str) -> bool:
    """Check UW short data row for staleness and instrument identity.

    UW can serve stale rows for recycled tickers (e.g. SPCX was a SPAC ETF
    before a new company reused the symbol). Reject rows older than
    _UW_SHORT_DATA_MAX_AGE_DAYS trading days.
    """
    data_rows = raw.get("data") or []
    if not data_rows:
        return False
    latest = data_rows[0] if isinstance(data_rows, list) else None
    if not isinstance(latest, dict):
        return False
    as_of_str = latest.get("date") or latest.get("as_of") or ""
    if not as_of_str:
        return False
    try:
        from datetime import date
        as_of = date.fromisoformat(str(as_of_str)[:10])
        age_days = (datetime.now(timezone.utc).date() - as_of).days
        return age_days <= _UW_SHORT_DATA_MAX_AGE_DAYS
    except Exception:
        return False


def _extract_uw_fee_rebate(raw: dict) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Extract fee_rate, rebate_rate, and as_of from a UW short data response."""
    data_rows = raw.get("data") or []
    if not data_rows or not isinstance(data_rows, list):
        return None, None, None
    latest = data_rows[0] if data_rows else None
    if not isinstance(latest, dict):
        return None, None, None
    fee_rate = _safe_float(latest.get("fee_rate") or latest.get("borrowRate"))
    rebate_rate = _safe_float(latest.get("rebate_rate") or latest.get("rebateRate"))
    as_of = latest.get("date") or latest.get("as_of")
    return fee_rate, rebate_rate, str(as_of) if as_of else None


def _safe_float(val: Any) -> Optional[float]:
    """Parse a value to float, returning None on failure or NaN."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if f != f else f  # exclude NaN
    except (TypeError, ValueError):
        return None


@app.get("/short-availability/{ticker}")
async def short_availability(ticker: str, request: Request):
    """Short availability data for a ticker.

    Primary: IB streaming probe for tick 46 (difficulty) + tick 89 (shortable shares).
    Fallback: UW get_short_data() for fee_rate / rebate_rate when IB has no data.

    ALWAYS returns 200 with missing:true when no data is available.
    Never raises 4xx (per feedback_http_status_for_real_errors.md).
    """
    upper = ticker.upper().strip()
    if not _TICKER_RE.match(upper):
        return JSONResponse({"ticker": upper, "shortable": None, "difficulty": None,
                             "shortable_shares": None, "fee_rate": None, "rebate_rate": None,
                             "source": "none", "as_of": datetime.now(timezone.utc).isoformat(),
                             "missing": True})

    difficulty: Optional[float] = None
    shortable_shares: Optional[float] = None
    fee_rate: Optional[float] = None
    rebate_rate: Optional[float] = None
    source = "none"
    as_of = datetime.now(timezone.utc).isoformat()

    # --- IB probe (primary) ---
    if ib_pool is not None and ib_pool.is_connected("data"):
        try:
            async with ib_pool.acquire("data") as client:
                result = await asyncio.wait_for(
                    asyncio.to_thread(_probe_short_ticks_in_thread, client, upper),
                    timeout=_SHORT_PROBE_TIMEOUT_SECS + 2.0,
                )
            difficulty = result.get("difficulty")
            shortable_shares = result.get("shortable_shares")
            if difficulty is not None or shortable_shares is not None:
                source = "ib"
                as_of = datetime.now(timezone.utc).isoformat()
        except asyncio.TimeoutError:
            logger.warning("short-availability/%s: IB probe timed out", upper)
        except Exception as exc:
            logger.warning("short-availability/%s: IB probe error: %s", upper, exc)

    # --- UW fallback (for fee/rebate or when IB returned nothing) ---
    if uw_available:
        try:
            raw = await asyncio.to_thread(_fetch_uw_short_data, upper)
            if raw is not None:
                if _uw_short_data_is_fresh(raw, upper):
                    uw_fee, uw_rebate, uw_as_of = _extract_uw_fee_rebate(raw)
                    fee_rate = uw_fee
                    rebate_rate = uw_rebate
                    if source == "none":
                        source = "uw"
                        as_of = uw_as_of or as_of
                else:
                    logger.info(
                        "short-availability/%s: UW data too old, ignoring (SPCX-style stale row)",
                        upper,
                    )
        except Exception as exc:
            logger.warning("short-availability/%s: UW fallback error: %s", upper, exc)

    shortable = _derive_shortability(difficulty, shortable_shares)
    missing = source == "none"

    return JSONResponse({
        "ticker": upper,
        "shortable": shortable,
        "difficulty": difficulty,
        "shortable_shares": shortable_shares,
        "fee_rate": fee_rate,
        "rebate_rate": rebate_rate,
        "source": source,
        "as_of": as_of,
        "missing": missing,
    })


def _fetch_uw_short_data(ticker: str) -> Optional[dict]:
    """Fetch UW short data synchronously (intended for asyncio.to_thread)."""
    try:
        with UWClient() as client:
            return client.get_short_data(ticker)
    except UWNotFoundError:
        return None
    except UWAPIError as exc:
        logger.info("short-availability/%s: UW get_short_data error: %s", ticker, exc)
        return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "scripts.api.server:app",
        host="127.0.0.1",
        port=8321,
        reload=True,
        reload_dirs=[str(SCRIPTS_DIR)],
        # Trust X-Forwarded-* only from the local Caddy hop so request.client.host
        # reflects the real remote IP for proxied traffic. The auth chokepoint
        # (is_trusted_local_request) also denies the bypass on any forwarded
        # request, so this is defense-in-depth — but it keeps logs/identity correct.
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )

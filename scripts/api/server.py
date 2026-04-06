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
from datetime import datetime, timezone
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Ensure scripts/ is on sys.path for client imports
SCRIPTS_DIR = Path(__file__).parent.parent
PROJECT_ROOT = SCRIPTS_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
INTERNALS_SKEW_CACHE_DIR = DATA_DIR / "cache"
INTERNALS_SKEW_CACHE_TTL_SECONDS = 60 * 15

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from api.ib_pool import IBPool
from api.subprocess import run_script, run_module, ScriptResult
from api.ib_gateway import check_ib_gateway, ensure_ib_gateway, restart_ib_gateway, is_docker_mode, is_cloud_mode
from clients.ib_client import DEFAULT_GATEWAY_PORT
from api.pool_order_manage import pool_cancel_order, pool_modify_order
from api.auth import verify_clerk_jwt, verify_api_key
from api.ws_ticket import create_ticket, validate_ticket
from api.routes.historical import router as historical_router

# Load .env from project root for Python scripts
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(PROJECT_ROOT / "web" / ".env")
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

    # IB pool — starts degraded if Gateway is still down after restart attempt
    ib_pool = IBPool()
    app.state.ib_pool = ib_pool
    pool_status = await ib_pool.connect_all()
    logger.info("IB pool status: %s", pool_status)

    # UW client — just verify token exists
    uw_available = bool(os.environ.get("UW_TOKEN"))
    if not uw_available:
        logger.warning("UW_TOKEN not set — UW-dependent endpoints will fail")

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

    if not os.environ.get("CLERK_JWKS_URL"):
        return await call_next(request)

    # Skip auth for server-to-server calls from localhost (Next.js → FastAPI)
    client_host = request.client.host if request.client else None
    if client_host in ("127.0.0.1", "::1"):
        return await call_next(request)

    # API key auth — scoped to historical/contract endpoints only
    service_identity = verify_api_key(request)
    if service_identity:
        request.state.user = service_identity
        return await call_next(request)

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
# Helpers
# ---------------------------------------------------------------------------

def _read_cache(path: Path) -> Optional[dict]:
    """Read a JSON cache file, return None if missing/corrupt."""
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _write_cache(path: Path, data: dict) -> None:
    """Write JSON to cache file atomically via temp file + os.replace()."""
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
async def health():
    gw = await check_ib_gateway()
    return {
        "status": "ok",
        "test_mode": test_mode,
        "ib_gateway": gw,
        "ib_pool": ib_pool.status() if ib_pool else {},
        "uw": uw_available,
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
    """Restart IB Gateway via IBC service, then reconnect pool."""
    result = await restart_ib_gateway()
    if not result["restarted"]:
        raise HTTPException(status_code=503, detail=result.get("error", "Restart failed"))

    # Reconnect pool after Gateway restart
    if ib_pool:
        await ib_pool.disconnect_all()
        pool_status = await ib_pool.connect_all()
        result["pool"] = pool_status

    return result


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
    result = await _run_ib_script_with_recovery(
        "ib_sync.py", ["--sync", "--port", str(DEFAULT_GATEWAY_PORT)], timeout=30
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
        "ib_sync.py", ["--sync", "--port", str(DEFAULT_GATEWAY_PORT)], timeout=30
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
        "ib_orders.py", ["--sync", "--port", str(DEFAULT_GATEWAY_PORT)], timeout=30
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
    result = await _run_ib_script_with_recovery(
        "ib_place_order.py", ["--json", order_json], timeout=15
    )
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if result.data and result.data.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.data.get("message", "Order failed"))
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


@app.post("/regime/share")
async def regime_share():
    """Generate Regime/CRI X share report (4 cards + preview HTML). Returns output path."""
    result = await run_script("generate_regime_share.py", ["--json", "--no-open"], timeout=120)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return result.data


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


async def _run_ib_script_with_recovery(
    script: str, args: list, timeout: float = 30
) -> ScriptResult:
    """Run an IB-dependent script with pre-flight health check and cooldown.

    Three layers of fast-fail:
    1. Cooldown: if a recent IB script failed, skip for _IB_SCRIPT_COOLDOWN_SECS
    2. Pool check: if pool is disconnected, verify Gateway before spawning
    3. Post-failure: verify Gateway health before restarting
    """
    global _ib_last_failure

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

    result = await run_script(script, args, timeout=timeout)

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
                result = await run_script(script, args, timeout=timeout)
            else:
                logger.error("IB Gateway restart failed: %s", gw_result)
                result = ScriptResult(
                    ok=False,
                    error=f"IB Gateway is down and restart failed. {gw_result.get('error', '')}".strip()
                        + " Check IBKR Mobile for 2FA approval.",
                )

    return result


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
    )

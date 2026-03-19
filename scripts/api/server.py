"""Radon FastAPI server — replaces Python shell-outs from Next.js.

Persistent IB connections, shared UW client, uniform JSON responses.
Port 8321, no auth for local use.

Usage:
    python3 -m uvicorn scripts.api.server:app --host 127.0.0.1 --port 8321 --reload
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Ensure scripts/ is on sys.path for client imports
SCRIPTS_DIR = Path(__file__).parent.parent
PROJECT_ROOT = SCRIPTS_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from api.ib_pool import IBPool
from api.subprocess import run_script, run_module, ScriptResult
from api.ib_gateway import check_ib_gateway, ensure_ib_gateway, restart_ib_gateway

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
# Shared state
# ---------------------------------------------------------------------------
from typing import Optional
ib_pool: Optional[IBPool] = None
uw_available: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start IB pool and UW client on startup, tear down on shutdown."""
    global ib_pool, uw_available

    # Ensure IB Gateway is running before connecting pool
    gw_status = await ensure_ib_gateway()
    logger.info("IB Gateway: %s", gw_status)

    # IB pool — starts degraded if Gateway is still down after restart attempt
    ib_pool = IBPool()
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
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


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    gw = await check_ib_gateway()
    return {
        "status": "ok",
        "ib_gateway": gw,
        "ib_pool": ib_pool.status() if ib_pool else {},
        "uw": uw_available,
    }


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
        "ib_sync.py", ["--sync", "--port", "4001"], timeout=30
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
        "ib_sync.py", ["--sync", "--port", "4001"], timeout=30
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
    result = await _run_ib_script_with_recovery(
        "ib_orders.py", ["--sync", "--port", "4001"], timeout=30
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
    order_json = json.dumps(body)
    result = await run_script(
        "ib_place_order.py", ["--json", order_json], timeout=15
    )
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if result.data and result.data.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.data.get("message", "Order failed"))
    return result.data


@app.post("/orders/cancel")
async def orders_cancel(request: Request):
    """Cancel an open order via IB."""
    body = await request.json()
    order_id = body.get("orderId", 0)
    perm_id = body.get("permId", 0)

    args = ["cancel"]
    if order_id:
        args.extend(["--order-id", str(order_id)])
    if perm_id:
        args.extend(["--perm-id", str(perm_id)])

    result = await run_script("ib_order_manage.py", args, timeout=15)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    if result.data and result.data.get("status") == "error":
        raise HTTPException(status_code=502, detail=result.data.get("message", "Cancel failed"))
    return result.data


@app.post("/orders/modify")
async def orders_modify(request: Request):
    """Modify an open order via IB."""
    body = await request.json()
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

    result = await run_script("ib_order_manage.py", args, timeout=15)
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


@app.post("/regime/share")
async def regime_share():
    """Generate Regime/CRI X share report (4 cards + preview HTML). Returns output path."""
    result = await run_script("generate_regime_share.py", ["--json", "--no-open"], timeout=120)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return result.data


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
    result = await run_script("ib_option_chain.py", args, timeout=15)
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

_IB_CONN_REFUSED_PATTERNS = ("Connect call failed", "ECONNREFUSED", "Connection refused")


def _is_ib_connection_error(error_msg: str) -> bool:
    """Check if an error message indicates IB Gateway is unreachable."""
    return any(p in (error_msg or "") for p in _IB_CONN_REFUSED_PATTERNS)


async def _run_ib_script_with_recovery(
    script: str, args: list, timeout: float = 30
) -> ScriptResult:
    """Run an IB-dependent script. On ECONNREFUSED, restart Gateway and retry once."""
    result = await run_script(script, args, timeout=timeout)

    if not result.ok and _is_ib_connection_error(result.error):
        logger.warning("IB Gateway unreachable, attempting auto-restart...")
        gw_result = await restart_ib_gateway()

        if gw_result.get("restarted") and gw_result.get("port_listening"):
            logger.info("IB Gateway restarted, retrying %s", script)
            # Reconnect pool too
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

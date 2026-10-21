"""Operator research snapshots and bounded server-side UW admission.

No vendor socket or token is ever handed to a browser/MCP client. Historical
flow and broker order/quote calculations are deliberately separate.
"""
from __future__ import annotations
import asyncio
import copy
import json
import os
import re
import time
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter()
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_WEB_PATH = re.compile(r"^(?:stock/[A-Z][A-Z0-9.\-]{0,9}/(?:info|stock-state|quote)|shorts/[A-Z][A-Z0-9.\-]{0,9}/interest-float/v2|seasonality/[A-Z][A-Z0-9.\-]{0,9}/monthly|news/headlines)$")
_WEB_QUERIES = {"ticker", "limit", "page", "search_term", "major_only", "sources", "start_date", "end_date"}
_refresh_tasks: dict[str, asyncio.Task] = {}
_last_refresh: dict[str, float] = {}
_refresh_failed: dict[str, bool] = {}
_refresh_identity: dict[str, str] = {}
_web_tasks: set[asyncio.Task] = set()


def require_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if not _SYMBOL.fullmatch(symbol):
        raise HTTPException(400, "Invalid ticker")
    return symbol


def require_operator_host() -> None:
    # A demo VM's trusted frontend token must never purchase personal UW data.
    if os.environ.get("NEXT_PUBLIC_RADON_DEMO") == "1" or os.environ.get("RADON_DEMO_MODE") == "1" or os.environ.get("RADON_API_TEST_MODE", "").lower() in {"1", "true", "yes", "on"}:
        raise HTTPException(403, "Operator research is unavailable on this deployment")


def _ticker_payload(payload: dict, symbol: str) -> dict:
    result = copy.deepcopy(payload)
    latest = result.get("latest") or {}
    selected = {}
    for channel, values in latest.items():
        if not isinstance(values, dict):
            continue
        tail = {}
        for key, row in values.items():
            if not isinstance(row, dict):
                continue
            payload = row.get("payload", row)
            if not isinstance(payload, dict):
                continue
            ticker = payload.get("ticker") or payload.get("symbol") or payload.get("underlying_symbol")
            suffix = channel.split(":", 1)[1] if ":" in channel else None
            market = channel in {"market_tide", "otm_market_tide", "futures_blocks", "periscope:SPX"}
            if market or ticker == symbol or suffix == symbol or symbol in (payload.get("tickers") or []):
                tail[key] = row
        if tail:
            selected[channel] = dict(list(tail.items())[-100:])
    result["latest"] = selected
    return result


def research_snapshot(symbol: str) -> dict:
    from uw_stream.store import read_snapshot
    from uw_research import read_research
    from utils.uw_governor import snapshot
    return {"schema_version": 1, "ticker": symbol, "source": "unusual_whales",
            "stream": _ticker_payload(read_snapshot(), symbol),
            "research": read_research(symbol), "governor": snapshot(),
            "execution_source": "ib", "oi_cadence": "daily",
            "refresh_pending": symbol in _refresh_tasks, "refresh_failed": _refresh_failed.get(symbol, False)}


@router.get("/uw/capabilities")
async def capabilities():
    require_operator_host()
    from utils.uw_governor import snapshot
    from uw_stream.store import read_snapshot
    from uw_research import SECTION_NAMES
    stream = await asyncio.to_thread(read_snapshot)
    return {"source": "unusual_whales", "governor": await asyncio.to_thread(snapshot),
            "stream_status": stream.get("status", "missing"),
            "channels": stream.get("channels", {}), "sections": sorted(SECTION_NAMES),
            "entitlement": "Verify channel acknowledgements and provider/account limits; a valid REST key alone does not establish streaming access."}


@router.get("/uw/research")
async def read(ticker: str = "SPY"):
    require_operator_host()
    return await asyncio.to_thread(research_snapshot, require_symbol(ticker))


class RefreshInput(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)
    sections: list[str] | None = Field(default=None, max_length=6)
    quarter: str | None = Field(default=None, pattern=r"^20\d{2}Q[1-4]$")
    futures_contract: str | None = Field(default=None, pattern=r"^[A-Z0-9]{2,16}$")
    security_id: str | None = Field(default=None, pattern=r"^\d{1,14}$")


async def _refresh(body: RefreshInput, symbol: str):
    # A single subprocess has a killable wall deadline. It does not hold an
    # event-loop thread through requests retries or a stalled database write.
    from api.subprocess import run_script
    args = ["--ticker", symbol]
    if body.sections:
        args += ["--sections", ",".join(body.sections)]
    if body.quarter:
        args += ["--quarter", body.quarter]
    if body.futures_contract:
        args += ["--futures-contract", body.futures_contract]
    if body.security_id:
        args += ["--security-id", body.security_id]
    result = await run_script("uw_research.py", args, timeout=180)
    if not result.ok:
        raise HTTPException(502, "Research refresh did not complete; retained observations remain available")
    _last_refresh[symbol] = time.monotonic()
    return await asyncio.to_thread(research_snapshot, symbol)


@router.post("/uw/research/refresh")
async def refresh(body: RefreshInput):
    require_operator_host()
    from uw_research import SECTION_NAMES
    symbol = require_symbol(body.ticker)
    if body.sections is not None and (not body.sections or any(s not in SECTION_NAMES for s in body.sections)):
        raise HTTPException(400, "Unknown research section")
    identity = json.dumps({"sections": sorted(body.sections or []), "quarter": body.quarter,
                           "futures_contract": body.futures_contract, "security_id": body.security_id}, sort_keys=True)
    task = _refresh_tasks.get(symbol)
    if task is not None and _refresh_identity.get(symbol) != identity:
        raise HTTPException(409, "Another research context is refreshing for this ticker")
    if task is None:
        if time.monotonic() - _last_refresh.get(symbol, -1000) < 60:
            raise HTTPException(429, "Research refresh is cooling down", headers={"Retry-After": "60"})
        if len(_refresh_tasks) >= 2:
            raise HTTPException(429, "Research refresh capacity is busy", headers={"Retry-After": "15"})
        _last_refresh[symbol] = time.monotonic()
        _refresh_failed[symbol] = False
        # The history is a bounded cooldown, not an unbounded ticker registry.
        if len(_last_refresh) > 200:
            oldest = min(_last_refresh, key=_last_refresh.get)
            _last_refresh.pop(oldest, None)
            _refresh_failed.pop(oldest, None)
        task = asyncio.create_task(_refresh(body, symbol))
        _refresh_tasks[symbol] = task
        _refresh_identity[symbol] = identity
        def remove(done):
            if _refresh_tasks.get(symbol) is done:
                _refresh_tasks.pop(symbol, None)
                _refresh_identity.pop(symbol, None)
            if not done.cancelled():
                _refresh_failed[symbol] = done.exception() is not None
        task.add_done_callback(remove)
    # Refresh jobs can exceed a browser request deadline. The authenticated
    # observation stream reports completion while one bounded subprocess owns
    # the work; disconnecting a browser does not launch another copy.
    return await asyncio.to_thread(research_snapshot, symbol)


class WebRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=160)
    params: dict[str, str] = Field(default_factory=dict)


@router.post("/uw/request")
async def admitted_web_request(body: WebRequest):
    require_operator_host()
    if not _WEB_PATH.fullmatch(body.endpoint) or set(body.params) - _WEB_QUERIES or len(body.params) > 8:
        raise HTTPException(400, "Unsupported research request")
    if any(len(v) > 200 for v in body.params.values()):
        raise HTTPException(400, "Research query is too long")
    for name, maximum in (("limit", 100), ("page", 40)):
        if name in body.params and (not body.params[name].isdigit() or not 0 <= int(body.params[name]) <= maximum):
            raise HTTPException(400, "Research pagination is out of bounds")
    if len(_web_tasks) >= 4:
        raise HTTPException(429, "Research provider capacity is busy", headers={"Retry-After": "3"})
    from api.subprocess import run_script
    # requests' socket timeout is an inactivity timeout. A slow-drip response
    # can defeat it, so a killable child owns provider I/O and JSON parsing.
    task = asyncio.create_task(run_script(
        "uw_admitted_request.py", [body.endpoint, json.dumps(body.params)], timeout=18,
    ))
    _web_tasks.add(task)
    def finish(done):
        _web_tasks.discard(done)
        if not done.cancelled():
            done.exception()
    task.add_done_callback(finish)
    try:
        # Include shared subprocess admission in the caller's wall budget.
        result = await asyncio.wait_for(task, timeout=18)
        if not result.ok or not isinstance(result.data, dict):
            raise HTTPException(503, "Research provider request unavailable")
        return result.data
    except asyncio.TimeoutError:
        raise HTTPException(503, "Research provider request timed out") from None
    except asyncio.CancelledError:
        # run_script kills/reaps its process group before releasing capacity.
        task.cancel()
        raise


@router.get("/uw/research/events")
async def events(request: Request, ticker: str = "SPY"):
    require_operator_host()
    symbol = require_symbol(ticker)
    async def stream():
        last = None
        # Auth is re-evaluated on reconnect instead of allowing an expired
        # operator session to keep a private data stream indefinitely.
        for tick in range(50):
            if await request.is_disconnected():
                break
            try:
                payload = await asyncio.to_thread(research_snapshot, symbol)
                encoded = json.dumps(payload, allow_nan=False, separators=(",", ":"))
            except (OSError, ValueError):
                yield 'event: degraded\ndata: {"status":"degraded"}\n\n'
                break
            if encoded != last:
                yield f"event: research\ndata: {encoded}\n\n"
                last = encoded
            elif tick % 15 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no"})

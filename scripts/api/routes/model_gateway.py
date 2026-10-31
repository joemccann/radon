"""Authenticated app bridge to private inference, without model-management access.

The server's existing JWT/trusted-local perimeter protects every route here.
Native coding protocols and credential management use private Unix sockets only.
"""
from __future__ import annotations
import asyncio
import json
import math
import threading
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from model_gateway import client as gateway

router = APIRouter(prefix="/model-gateway")
MAX_REQUEST_BYTES = gateway.MAX_REQUEST_BYTES
APP_OPERATIONS = frozenset({"chat", "json", "vision", "embed", "ocr", "forecast", "x-search", "clairic"})


def _error(code, status, attempted=()):
    return JSONResponse({"error": {"code": code, "attempted": list(attempted)}}, status_code=status,
                        headers={"Cache-Control": "no-store"})


def _budget(operation, payload):
    if operation == "chat":
        value = payload.get("timeoutMs", 45_000)
        if type(value) not in (int, float):
            raise gateway.GatewayError("invalid_request", status=400)
        value /= 1000
    elif operation == "ocr":
        value = payload.get("timeout_seconds", 100)
    elif operation == "json":
        value = payload.get("read_timeout", 120)
    else:
        value = {"forecast": 300, "x-search": 300, "clairic": 12}.get(operation, 120)
    if type(value) not in (int, float) or not math.isfinite(value) or not .1 <= value <= 300:
        raise gateway.GatewayError("invalid_request", status=400)
    # A small transport allowance lets the service return its coded deadline.
    return float(value) + 5


async def _send(request, operation, payload):
    cancel = threading.Event()
    try:
        timeout = 10 if operation in {"models", "status"} else _budget(operation,payload)
        task = asyncio.create_task(asyncio.to_thread(gateway.request, operation, payload,
                                                    timeout_seconds=timeout, cancelled=cancel.is_set))
        try:
            while not task.done():
                await asyncio.wait({task},timeout=.05)
                if not task.done() and await request.is_disconnected():
                    cancel.set()
                    # The transport watcher closes its socket; no automatic replay.
                    try:
                        await asyncio.wait_for(asyncio.shield(task), timeout=1)
                    except (gateway.GatewayError, asyncio.TimeoutError):
                        pass
                    return _error("cancelled",499)
            result=await task
            return JSONResponse(result, headers={"Cache-Control":"no-store"})
        finally:
            cancel.set()
    except gateway.GatewayError as exc:
        return _error(exc.code,exc.status,exc.attempted)
    except asyncio.CancelledError:
        cancel.set()
        raise


@router.get("/{operation}")
async def inventory(operation: str, request: Request):
    if operation not in {"models","status"}:
        return _error("not_found",404)
    return await _send(request,operation,{})


@router.post("/{operation}")
async def infer(operation: str, request: Request):
    if operation not in APP_OPERATIONS:
        return _error("not_found",404)
    if request.headers.get("content-type", "").split(";",1)[0].strip().lower() != "application/json":
        return _error("invalid_request",400)
    try:
        declared=request.headers.get("content-length")
        if declared is not None and (int(declared)<0 or int(declared)>MAX_REQUEST_BYTES):
            return _error("request_too_large",413)
        chunks=[]; total=0
        async for chunk in request.stream():
            total+=len(chunk)
            if total>MAX_REQUEST_BYTES:
                return _error("request_too_large",413)
            chunks.append(chunk)
        payload=json.loads(b"".join(chunks),parse_constant=lambda value:(_ for _ in ()).throw(ValueError(value)))
        if not isinstance(payload,dict):
            raise ValueError("object required")
    except (ValueError,UnicodeError):
        return _error("invalid_request",400)
    return await _send(request,operation,payload)

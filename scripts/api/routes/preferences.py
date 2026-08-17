"""Operator-tunable runtime preferences behind the /preferences page.

Auth: no per-route dependency. The global auth middleware in
scripts/api/server.py gates these paths (they are deliberately absent from
AUTH_EXEMPT_PATHS), and in production verify_clerk_jwt additionally pins the
caller to ALLOWED_USER_IDS, so every verb here is operator-only.

Event-loop discipline: uvicorn runs a single loop, so every app_preferences call
that can touch Turso goes through asyncio.to_thread. This module never imports
db.client / db.writer / libsql.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

try:
    import app_preferences
except ImportError:  # pragma: no cover - depends on which root is on sys.path
    from scripts import app_preferences

try:
    from api.auth import is_trusted_local_request
except ImportError:  # pragma: no cover - depends on which root is on sys.path
    from scripts.api.auth import is_trusted_local_request

logger = logging.getLogger("radon.preferences")

router = APIRouter()

UPDATED_BY_MAX_LEN = 64


def _generated_at() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _actor(request: Request, body: dict | None = None) -> str | None:
    """Who the audit log records.

    An authenticated principal (request.state.user) always wins. The
    trusted-local bypass in server.py returns before setting
    request.state.user, and the only production caller on that path is the
    Next.js hop on loopback, which forwards the Clerk operator id as
    `updated_by` (PUT body / DELETE query) — record that as the real
    principal. A forwarded id from any non-trusted-local caller is
    self-asserted and ignored; a trusted-local caller that forwards nothing
    is labelled rather than silently recorded as NULL.
    """
    payload = getattr(request.state, "user", None)
    if isinstance(payload, dict):
        subject = payload.get("sub")
        if isinstance(subject, str) and subject:
            return subject[:UPDATED_BY_MAX_LEN]
    forwarded = _forwarded_operator_id(request, body)
    if forwarded is not None:
        return forwarded
    return "trusted-local"


def _forwarded_operator_id(request: Request, body: dict | None) -> str | None:
    """Operator id forwarded by the trusted Next.js hop, or None."""
    if not is_trusted_local_request(request):
        return None
    raw = body.get("updated_by") if body is not None else None
    if raw is None:
        raw = request.query_params.get("updated_by")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()[:UPDATED_BY_MAX_LEN]
    return None


def _bad_request() -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "code": "BAD_REQUEST",
            "message": "body must be a JSON object with a 'value' field",
        },
    )


def _store_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "PREFERENCE_STORE_UNAVAILABLE",
            "message": "preferences store temporarily unavailable",
        },
    )


def _validation_error(exc) -> HTTPException:
    """404 for an unknown key, 422 for a value the registry refuses."""
    detail: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if exc.code == "UNKNOWN_PREFERENCE":
        return HTTPException(status_code=404, detail=detail)
    if exc.code == "OUT_OF_RANGE":
        detail["allowed_min"] = exc.allowed_min
        detail["allowed_max"] = exc.allowed_max
    return HTTPException(status_code=422, detail=detail)


async def _mutate(operation, key: str, *args) -> dict:
    """Run a blocking app_preferences write off-loop and shape the response."""
    try:
        resolved = await asyncio.to_thread(operation, key, *args)
    except app_preferences.PreferenceValidationError as exc:
        raise _validation_error(exc) from exc
    except app_preferences.PreferenceStoreError as exc:
        logger.warning("preferences store write failed for %s: %s", key, exc)
        raise _store_unavailable() from exc

    store = await asyncio.to_thread(app_preferences.store_status)
    return {"preference": resolved.to_dict(), "store": store}


async def _json_object(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception as exc:
        raise _bad_request() from exc
    if not isinstance(body, dict):
        raise _bad_request()
    return body


@router.get("/preferences")
async def list_preferences(request: Request):
    """Every registry entry with its effective value, source and provenance.

    A store outage is reported in `store`, never as a 5xx: the entries still
    resolve from environment and code defaults.
    """
    await asyncio.to_thread(app_preferences.refresh_snapshot)
    resolved = await asyncio.to_thread(app_preferences.resolve_all)
    store = await asyncio.to_thread(app_preferences.store_status)

    return {
        "preferences": [entry.to_dict() for entry in resolved],
        "groups": list(app_preferences.GROUP_ORDER),
        "store": store,
        "generated_at": _generated_at(),
    }


@router.put("/preferences/{key}")
async def put_preference(key: str, request: Request):
    """Store an operator override and return the value that now applies."""
    body = await _json_object(request)
    if "value" not in body:
        raise _bad_request()

    return await _mutate(
        app_preferences.set_value, key, body["value"], _actor(request, body)
    )


@router.delete("/preferences/{key}")
async def reset_preference(key: str, request: Request):
    """Drop the stored override and return the value the system falls back to."""
    return await _mutate(app_preferences.clear_value, key, _actor(request))

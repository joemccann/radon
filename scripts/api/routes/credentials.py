"""Operator credentials behind the profile Credentials tab.

Auth: no per-route dependency. The global auth middleware in
scripts/api/server.py gates these paths (deliberately absent from
AUTH_EXEMPT_PATHS), and in production verify_clerk_jwt pins the caller to
ALLOWED_USER_IDS, so every verb here is operator-only.

Redaction contract: no response on this surface ever carries a plaintext
credential — reads return masked hints, and a successful PUT echoes the
masked entry, never the submitted value.

Live effect: a saved value is exported into os.environ immediately, so this
process and every subprocess it spawns pick it up without a restart. A
delete removes only the stored row; the exported value survives until the
next process restart (services keep working rather than losing a credential
mid-session).

Event-loop discipline: the store is local sqlite and validators do network
I/O, so every call goes through asyncio.to_thread. Slow browser-login
validators run as their own subprocess inside that thread.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

try:
    import credential_validators
    import credentials_registry
    from secret_store import SecretStore, SecretStoreError
except ImportError:  # pragma: no cover - depends on which root is on sys.path
    from scripts import credential_validators, credentials_registry
    from scripts.secret_store import SecretStore, SecretStoreError

try:
    from api.auth import is_trusted_local_request
except ImportError:  # pragma: no cover - depends on which root is on sys.path
    from scripts.api.auth import is_trusted_local_request

logger = logging.getLogger("radon.credentials")

router = APIRouter()

# Names exported from the store into os.environ this process lifetime.
# Distinguishes live exports from .env-file fallbacks after a delete (R-541).
_SESSION_EXPORTED: set[str] = set()

UPDATED_BY_MAX_LEN = 64

# Vendor validators hold a thread for up to SLOW_LOGIN_TIMEOUT_S (~90s):
# bound what is in flight and how often one service can be re-validated.
VALIDATOR_CONCURRENCY = 2
VALIDATOR_COOLDOWN_S = 5.0
_validator_slots: Optional[asyncio.Semaphore] = None
_validator_last_run: Dict[str, float] = {}


def _generated_at() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _store() -> SecretStore:
    """Fresh handle per request: paths resolve from env, opens are cheap."""
    return SecretStore()


def _store_unavailable(exc: SecretStoreError) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "CREDENTIAL_STORE_UNAVAILABLE",
            "message": str(exc),
        },
    )


def _open_store() -> SecretStore:
    """Route-facing constructor: a store that cannot open (missing/mismatched
    key, unopenable DB) is the crafted 503, never a raw 500 (R-521/R-522)."""
    try:
        return _store()
    except (SecretStoreError, OSError) as exc:
        # REL-217 (R-592): OSError-class constructor failures (key-file path
        # is a directory, permission denied) are the same operational fact as
        # an unopenable DB — 503, never a raw 500.
        logger.warning("credential store unavailable: %s", exc)
        raise _store_unavailable(exc)


def _actor(request: Request, body: dict | None = None) -> str:
    """Same principal derivation as routes/preferences.py:_actor."""
    payload = getattr(request.state, "user", None)
    if isinstance(payload, dict):
        subject = payload.get("sub")
        if isinstance(subject, str) and subject:
            return subject[:UPDATED_BY_MAX_LEN]
    if is_trusted_local_request(request):
        raw = body.get("updated_by") if body is not None else None
        if raw is None:
            raw = request.query_params.get("updated_by")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()[:UPDATED_BY_MAX_LEN]
    return "trusted-local"


def _bad_request(message: str) -> HTTPException:
    return HTTPException(
        status_code=400, detail={"code": "BAD_REQUEST", "message": message}
    )


def _unknown_service(service_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "code": "UNKNOWN_SERVICE",
            "message": f"no credential service {service_id!r}",
        },
    )


async def _run_validator(service_id: str, merged: Dict[str, str]):
    """One validation per service per cooldown, VALIDATOR_CONCURRENCY in flight."""
    global _validator_slots
    now = time.monotonic()
    elapsed = now - _validator_last_run.get(service_id, float("-inf"))
    if elapsed < VALIDATOR_COOLDOWN_S:
        retry_after = max(1, math.ceil(VALIDATOR_COOLDOWN_S - elapsed))
        raise HTTPException(
            status_code=429,
            detail={
                "code": "VALIDATION_COOLDOWN",
                "service": service_id,
                "message": f"{service_id} was validated {elapsed:.0f}s ago",
            },
            headers={"Retry-After": str(retry_after)},
        )
    _validator_last_run[service_id] = now
    if _validator_slots is None:
        _validator_slots = asyncio.Semaphore(VALIDATOR_CONCURRENCY)
    async with _validator_slots:
        return await asyncio.to_thread(
            credential_validators.validate, service_id, merged
        )


async def _json_object(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception as exc:
        raise _bad_request("body must be a JSON object") from exc
    if not isinstance(body, dict):
        raise _bad_request("body must be a JSON object")
    return body


def _service_entry(
    service: "credentials_registry.CredentialService",
    stored: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    fields = []
    for field in service.fields:
        row = stored.get(field.name)
        env_present = bool(os.environ.get(field.name))
        exported_only = row is None and env_present and field.name in _SESSION_EXPORTED
        env_fallback = row is None and env_present and field.name not in _SESSION_EXPORTED
        fields.append(
            {
                "name": field.name,
                "label": field.label,
                "secret": field.secret,
                "placeholder": field.placeholder,
                "configured": row is not None,
                "hint": row["hint"] if row else "",
                "version": row["version"] if row else 0,
                "updated_at": row["updated_at"] if row else None,
                "updated_by": row["updated_by"] if row else None,
                "env_fallback": env_fallback,
                "exported_only": exported_only,
            }
        )
    return {
        "id": service.id,
        "label": service.label,
        "group": service.group,
        "validator": service.validator is not None,
        "slow": service.slow,
        "note": service.note,
        "fields": fields,
    }


def _stored_by_name(store: SecretStore) -> Dict[str, Dict[str, Any]]:
    return {entry["name"]: entry for entry in store.list_secrets()}


def _clean_values(service, body: dict) -> Dict[str, str]:
    """Validate the submitted {name: value} map against the service fields."""
    raw = body.get("values")
    if not isinstance(raw, dict) or not raw:
        raise _bad_request("body must carry a non-empty 'values' object")
    allowed = {field.name for field in service.fields}
    values: Dict[str, str] = {}
    for name, value in raw.items():
        if name not in allowed:
            raise _bad_request(f"{name} is not a field of {service.id}")
        if not isinstance(value, str) or not value.strip():
            raise _bad_request(f"{name} must be a non-empty string")
        values[name] = value.strip()
    return values


def _merged_values(
    service, submitted: Dict[str, str], store: SecretStore
) -> Dict[str, str]:
    """Submitted wins over stored wins over env, per field."""
    merged: Dict[str, str] = {}
    for field in service.fields:
        if field.name in submitted:
            merged[field.name] = submitted[field.name]
            continue
        try:
            stored_value = store.get_secret(field.name)
        except SecretStoreError as exc:
            # A corrupted sibling row must not 500 rotation of a healthy
            # field (R-538): treat it as absent and fall through to env.
            logger.warning(
                "credential merge: skipping unreadable row %s: %s",
                field.name,
                exc,
            )
            stored_value = None
        if stored_value:
            merged[field.name] = stored_value
            continue
        env_value = os.environ.get(field.name, "").strip()
        if env_value:
            merged[field.name] = env_value
    return merged


@router.get("/credentials")
async def list_credentials(request: Request):
    """Every registry service with masked stored state. Never plaintext."""
    store = _open_store()
    try:
        stored = await asyncio.to_thread(_stored_by_name, store)
    except SecretStoreError as exc:
        logger.warning("credential list failed: %s", exc)
        raise _store_unavailable(exc)
    return {
        # R-621: an unopenable store at boot exports nothing and leaves every
        # consumer on its .env fallback. Say so here rather than letting the
        # per-field `env_fallback` flags read as a deliberate configuration.
        "bootstrap_error": bootstrap_failure(),
        "services": [
            _service_entry(service, stored)
            for service in credentials_registry.SERVICES
        ],
        "groups": list(credentials_registry.GROUP_ORDER),
        "generated_at": _generated_at(),
    }


@router.put("/credentials/{service_id}")
async def put_credentials(service_id: str, request: Request):
    """Validate against the vendor, then store + apply. Invalid saves nothing."""
    service = credentials_registry.service_by_id(service_id)
    if service is None:
        raise _unknown_service(service_id)
    body = await _json_object(request)
    submitted = _clean_values(service, body)
    actor = _actor(request, body)
    store = _open_store()

    merged = await asyncio.to_thread(_merged_values, service, submitted, store)
    verdict = await _run_validator(service_id, merged)
    if verdict.blocks_save:
        raise HTTPException(
            status_code=422,
            detail=credential_validators.scrub_validation_message(
                {
                    "code": "CREDENTIAL_REJECTED",
                    "service": service_id,
                    "status": verdict.status,
                    "message": verdict.message,
                }
            ),
        )

    def _save() -> None:
        # One transaction for every field, and export to os.environ only
        # after the store write succeeded — a mid-save failure must not
        # leave a half-stored, half-exported service (R-539).
        store.set_secrets(submitted, actor=actor)
        for name, value in submitted.items():
            os.environ[name] = value
            _SESSION_EXPORTED.add(name)

    try:
        await asyncio.to_thread(_save)
    except SecretStoreError as exc:
        logger.warning("credential store write failed for %s: %s", service_id, exc)
        note_validation_failed(service_id)  # R-630
        raise _store_unavailable(exc) from exc

    stored = await asyncio.to_thread(_stored_by_name, store)
    return {
        "service": _service_entry(service, stored),
        "validation": credential_validators.scrub_validation_message(verdict.to_dict()),
    }


@router.post("/credentials/{service_id}/validate")
async def validate_credentials(service_id: str, request: Request):
    """Dry-run vendor check with submitted + stored values. No writes."""
    service = credentials_registry.service_by_id(service_id)
    if service is None:
        raise _unknown_service(service_id)
    body = await _json_object(request)
    submitted: Dict[str, str] = {}
    if body.get("values"):
        submitted = _clean_values(service, body)
    store = _open_store()
    merged = await asyncio.to_thread(_merged_values, service, submitted, store)
    verdict = await _run_validator(service_id, merged)
    return {"validation": credential_validators.scrub_validation_message(verdict.to_dict())}


@router.delete("/credentials/{service_id}/{name}")
async def delete_credential(service_id: str, name: str, request: Request):
    """Remove one stored field. The exported env value survives until restart."""
    service = credentials_registry.service_by_id(service_id)
    if service is None:
        raise _unknown_service(service_id)
    if name not in {field.name for field in service.fields}:
        raise _bad_request(f"{name} is not a field of {service.id}")
    store = _open_store()
    try:
        removed = await asyncio.to_thread(
            store.delete_secret, name, _actor(request)
        )
    except SecretStoreError as exc:
        logger.warning("credential delete failed for %s: %s", name, exc)
        raise _store_unavailable(exc) from exc
    stored = await asyncio.to_thread(_stored_by_name, store)
    return {"removed": removed, "service": _service_entry(service, stored)}


#: Set when the store could not be OPENED at boot (R-621). None means the
#: last bootstrap reached the store, whatever it found there.
_BOOTSTRAP_FAILURE: str | None = None


def bootstrap_failure() -> str | None:
    """The last boot-time store-open failure, or None."""
    return _BOOTSTRAP_FAILURE


def note_validation_failed(service_id: str) -> None:
    """Release the validation cooldown a failed save consumed (R-630).

    `_run_validator` stamps the window before the store write, and the PUT
    path raises its 429 ahead of `store.set_secrets`, so a save that 503'd on
    the store — or one the operator is retrying after a typo — came back as
    VALIDATION_COOLDOWN and stored nothing. A save that did not happen has not
    consumed a validation window.
    """
    _validator_last_run.pop(service_id, None)


def bootstrap_exported_names() -> list:
    """Export stored secrets into os.environ (store wins over .env).

    Called from the FastAPI lifespan, mirroring app_preferences.bootstrap():
    subprocesses inherit the operator's credentials without a restart chain.
    Only registry names are ever touched.
    """
    global _BOOTSTRAP_FAILURE
    exported = []
    try:
        store = _store()
        stored = _stored_by_name(store)
    except Exception as exc:  # noqa: BLE001 - startup must not die on this
        # R-621: this used to be a warning and an empty list, indistinguishable
        # from "the store is empty". The API then booted clean, exported
        # nothing, and every consumer fell back to whatever `.env` held —
        # silently inverting the documented store-wins contract toward a
        # rotated-out key, surfacing later as unrelated vendor 401s. Record
        # it so the operator has one place that says the store did not open.
        _BOOTSTRAP_FAILURE = f"{type(exc).__name__}: {exc}"
        logger.error(
            "credential store did not open at boot; NOTHING was exported and "
            "every consumer is on its .env fallback: %s",
            exc,
        )
        return exported
    _BOOTSTRAP_FAILURE = None
    for name in credentials_registry.all_field_names():
        if name not in stored:
            continue
        try:
            value = store.get_secret(name)
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the rest (R-521)
            logger.warning(
                "credential bootstrap: skipping undecryptable row %s: %s",
                name,
                exc,
            )
            continue
        if value:
            os.environ[name] = value
            _SESSION_EXPORTED.add(name)
            exported.append(name)
    return exported

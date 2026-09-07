"""Bounded Turso access for the FastAPI process — libSQL HTTP pipeline only.

The radon-api event loop must NEVER touch sync libsql
(``db.client.get_db`` / ``db.execute`` / ``.commit``): the
``libsql_experimental`` native calls hold the GIL while blocked, so even
``asyncio.to_thread`` cannot bound them — a hung Turso call starves the
single uvicorn loop from any thread (commits c9e518a + 2647c93,
``feedback_no_sync_libsql_on_fastapi_event_loop``). This module replaces
those touch points with the libSQL HTTP ("hrana") pipeline over stdlib
urllib: the socket timeout is a REAL bound, and urllib releases the GIL
during network waits, so ``asyncio.to_thread(hrana_execute, ...)`` is
genuinely bounded.

Enforced by ``scripts/tests/test_no_sync_libsql_in_api.py`` — importing
``db.client`` / ``db.writer`` / ``libsql*`` anywhere under ``scripts/api``
fails CI.

The wire protocol is adapted from ``scripts/health_service/turso_http.py``
(the stdlib hrana reader built for the isolated health daemon — importing
FROM it is fine; its isolation contract only restricts what IT imports).
This variant adds positional-arg support and raises instead of degrading,
because each API call site owns its own fallback (JSON file / empty
payload / swallow).
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Sequence

from health_service.turso_http import _cell_value, http_url_from_libsql, read_env

# Default per-statement bound. Direct-to-cloud Turso reads are 30-60 ms in
# steady state; 4 s absorbs a slow tail while keeping a wedged request far
# below FastAPI route timeouts.
HRANA_TIMEOUT_S = 4.0

_MAX_RESPONSE_BYTES = 8_388_608  # hard ceiling; these result sets are tiny


class DbHttpError(RuntimeError):
    """Raised on ANY hrana failure: missing creds, HTTP/socket error,
    timeout, malformed response, or a statement-level error."""


def _encode_arg(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        # hrana carries integers as strings to survive 64-bit values in JSON.
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    return {"type": "text", "value": str(value)}


def _post_pipeline(
    http_origin: str, token: str, sql: str, args: Sequence[Any], timeout: float
) -> dict:
    payload = json.dumps(
        {
            "requests": [
                {
                    "type": "execute",
                    "stmt": {"sql": sql, "args": [_encode_arg(a) for a in args]},
                },
                {"type": "close"},
            ]
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        http_origin.rstrip("/") + "/v2/pipeline",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(_MAX_RESPONSE_BYTES)
    return json.loads(raw.decode("utf-8"))


def _execute_result(body: dict) -> dict:
    results = body.get("results") or []
    if not results:
        raise DbHttpError("empty pipeline response")
    first = results[0]
    if first.get("type") != "ok":
        error = first.get("error") or {}
        raise DbHttpError(str(error.get("message") or "statement failed"))
    response = first.get("response") or {}
    if response.get("type") != "execute":
        raise DbHttpError("unexpected pipeline response type")
    return response.get("result") or {}


def _rows_as_tuples(result: dict) -> list[tuple]:
    return [
        tuple(_cell_value(cell) for cell in raw_row)
        for raw_row in result.get("rows", [])
    ]


def hrana_execute(
    sql: str, args: Sequence[Any] = (), timeout: float = HRANA_TIMEOUT_S
) -> list[tuple]:
    """Execute one bounded statement against Turso over HTTP.

    Rows come back as positional tuples (drop-in for libsql
    ``cursor.fetchall()``) so call sites keep their ``row[i]`` indexing.
    Writes autocommit per pipeline — no separate ``commit()``.

    Raises :class:`DbHttpError` on any failure; the caller owns the
    fallback. Call via ``asyncio.to_thread`` from route handlers.
    """
    db_url, token = read_env()
    http_origin = http_url_from_libsql(db_url)
    if not http_origin or not token:
        raise DbHttpError("TURSO_DB_URL / TURSO_AUTH_TOKEN not configured")
    try:
        body = _post_pipeline(http_origin, token, sql, tuple(args), timeout)
        return _rows_as_tuples(_execute_result(body))
    except DbHttpError:
        raise
    except Exception as exc:
        raise DbHttpError(f"{type(exc).__name__}: {exc}") from exc


def hrana_transaction(statements: Sequence[tuple[str, Sequence[Any]]], timeout: float = HRANA_TIMEOUT_S) -> None:
    """Commit a bounded batch atomically, rolling back after any failed step.

    Mirrors @libsql/client executeHranaBatch's conditional steps. A lost
    response is ambiguous; callers must retry using stable idempotency keys.
    """
    if not statements or len(statements) > 100:
        raise ValueError("transaction requires 1..100 statements")
    db_url, token = read_env()
    origin = http_url_from_libsql(db_url)
    if not origin or not token:
        raise DbHttpError("TURSO_DB_URL / TURSO_AUTH_TOKEN not configured")
    steps = [{"stmt": {"sql": "BEGIN IMMEDIATE", "args": [], "want_rows": False}}]
    for sql, args in [*statements, ("COMMIT", ())]:
        steps.append({"condition": {"type": "ok", "step": len(steps) - 1},
                      "stmt": {"sql": sql, "args": [_encode_arg(a) for a in args], "want_rows": False}})
    commit_index = len(steps) - 1
    steps.append({"condition": {"type": "not", "cond": {"type": "ok", "step": commit_index}},
                  "stmt": {"sql": "ROLLBACK", "args": [], "want_rows": False}})
    payload = json.dumps({"requests": [{"type": "batch", "batch": {"steps": steps}}, {"type": "close"}]}).encode()
    if len(payload) > 2_097_152:
        raise ValueError("transaction payload exceeds 2 MiB")
    req = urllib.request.Request(origin.rstrip("/") + "/v2/pipeline", data=payload, method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise DbHttpError("transaction response exceeds limit")
        body = json.loads(raw)
        first = (body.get("results") or [{}])[0]
        result = (first.get("response") or {}).get("result") or {}
        if first.get("type") != "ok" or (first.get("response") or {}).get("type") != "batch":
            raise DbHttpError("transaction pipeline failed")
        errors = result.get("step_errors", [])
        results = result.get("step_results", [])
        if any(errors[:commit_index + 1]) or len(results) <= commit_index or results[commit_index] is None:
            raise DbHttpError("transaction failed or commit was skipped")
    except DbHttpError:
        raise
    except Exception as exc:
        # Do not include transport URLs, headers, or source contents in errors.
        raise DbHttpError(f"research transaction transport failed: {type(exc).__name__}") from exc

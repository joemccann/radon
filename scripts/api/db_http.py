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

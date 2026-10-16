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

import http.client
import io
import json
import ssl
import threading
import urllib.request
from typing import Any, Sequence
from urllib.parse import urlsplit

from health_service.turso_http import _cell_value, http_url_from_libsql, read_env
from utils.outcome_metrics import measure_operation

# Default per-statement bound. Direct-to-cloud Turso reads are 30-60 ms in
# steady state; 4 s absorbs a slow tail while keeping a wedged request far
# below FastAPI route timeouts.
HRANA_TIMEOUT_S = 4.0

_MAX_RESPONSE_BYTES = 8_388_608  # hard ceiling; these result sets are tiny

# urllib.request.urlopen opens a fresh TCP/TLS socket per POST. The FastAPI
# process issues Hrana pipelines on a hot path (/health/lite every 5s, plus
# every route). Thread-local keep-alive reuses one socket per worker thread.
# Tests that replace urllib.request.urlopen still take that seam.
_STDLIB_URLOPEN = urllib.request.urlopen
_local = threading.local()


class _PooledResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _close_keepalive() -> None:
    conn = getattr(_local, "conn", None)
    _local.conn = None
    _local.key = None
    if conn is None:
        return
    try:
        conn.close()
    except OSError:
        pass


def _pooled_open(req: urllib.request.Request, timeout: float) -> _PooledResponse:
    parts = urlsplit(req.full_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise OSError("unsupported Turso origin")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    key = (parts.scheme, parts.hostname, port)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    headers = {k: v for k, v in req.header_items()}
    headers.setdefault("Connection", "keep-alive")
    body = req.data or b""
    method = req.get_method() or "POST"

    def connect() -> http.client.HTTPConnection:
        if parts.scheme == "https":
            conn = http.client.HTTPSConnection(
                parts.hostname,
                port,
                timeout=timeout,
                context=ssl.create_default_context(),
            )
        else:
            conn = http.client.HTTPConnection(parts.hostname, port, timeout=timeout)
        _local.conn = conn
        _local.key = key
        return conn

    reused = getattr(_local, "key", None) == key and getattr(_local, "conn", None) is not None
    conn = getattr(_local, "conn", None) if reused else None
    if conn is None:
        conn = connect()
    else:
        conn.timeout = timeout

    try:
        conn.request(method, path, body, headers)
    except OSError:
        _close_keepalive()
        if not reused:
            raise
        # Stale keep-alive: the POST never left this socket. One fresh retry.
        conn = connect()
        conn.request(method, path, body, headers)

    try:
        resp = conn.getresponse()
        # A short read leaves the remainder on this socket. The next pipeline
        # is then sent and discarded (ResponseNotReady), which reports a
        # committed write as a transport failure. REL-326.
        raw = resp.read(_MAX_RESPONSE_BYTES + 1)
        over_cap = len(raw) > _MAX_RESPONSE_BYTES or not resp.isclosed()
        if over_cap or resp.status != 200:
            _close_keepalive()
            if resp.status != 200:
                raise OSError(f"HTTP {resp.status}")
            raise OSError("response exceeds limit")
        return _PooledResponse(raw)
    except Exception:
        _close_keepalive()
        raise


def _open_request(req: urllib.request.Request, timeout: float):
    """POST the pipeline. Honor a patched urlopen so budget tests stay on the wire fake."""
    current = urllib.request.urlopen
    if current is not _STDLIB_URLOPEN:
        return current(req, timeout=timeout)
    return _pooled_open(req, timeout)


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
    return _post_statements(http_origin, token, [(sql, args)], timeout)


def _post_statements(
    http_origin: str,
    token: str,
    statements: Sequence[tuple[str, Sequence[Any]]],
    timeout: float,
) -> dict:
    requests: list[dict[str, Any]] = [
        {
            "type": "execute",
            "stmt": {"sql": sql, "args": [_encode_arg(a) for a in args]},
        }
        for sql, args in statements
    ]
    requests.append({"type": "close"})
    payload = json.dumps({"requests": requests}).encode("utf-8")
    req = urllib.request.Request(
        http_origin.rstrip("/") + "/v2/pipeline",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
            "Connection": "keep-alive",
        },
    )
    with _open_request(req, timeout) as resp:
        raw = resp.read(_MAX_RESPONSE_BYTES)
    return json.loads(raw.decode("utf-8"))


def _execute_result(body: dict, index: int = 0) -> dict:
    results = body.get("results") or []
    if len(results) <= index:
        raise DbHttpError("empty pipeline response")
    first = results[index]
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


@measure_operation("database")
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


@measure_operation("database")
def hrana_execute_many(
    statements: Sequence[tuple[str, Sequence[Any]]],
    timeout: float = HRANA_TIMEOUT_S,
) -> list[list[tuple] | DbHttpError]:
    """Run independent read statements in ONE /v2/pipeline round-trip.

    Not a transaction: each statement succeeds or fails on its own, so the
    result list holds rows (as in :func:`hrana_execute`) or the
    :class:`DbHttpError` for that statement, in input order. Missing creds
    or a transport/protocol failure raise :class:`DbHttpError`.
    """
    if not statements or len(statements) > 100:
        raise ValueError("hrana_execute_many requires 1..100 statements")
    db_url, token = read_env()
    http_origin = http_url_from_libsql(db_url)
    if not http_origin or not token:
        raise DbHttpError("TURSO_DB_URL / TURSO_AUTH_TOKEN not configured")
    try:
        body = _post_statements(
            http_origin, token, [(sql, tuple(args)) for sql, args in statements], timeout
        )
    except Exception as exc:
        raise DbHttpError(f"{type(exc).__name__}: {exc}") from exc
    out: list[list[tuple] | DbHttpError] = []
    for index in range(len(statements)):
        try:
            out.append(_rows_as_tuples(_execute_result(body, index)))
        except DbHttpError as exc:
            out.append(exc)
        except Exception as exc:
            out.append(DbHttpError(f"{type(exc).__name__}: {exc}"))
    return out


@measure_operation("database")
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
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + token,
                                          "Connection": "keep-alive"})
    try:
        with _open_request(req, timeout) as response:
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

"""Bounded stdlib Turso access for non-API daemons — libSQL HTTP pipeline.

Sister transport to ``scripts/api/db_http.py`` (same hrana wire protocol,
both adapted from ``scripts/health_service/turso_http.py``). This copy
lives under ``scripts/db/`` because oneshot daemons like the IB watchdog
must not import from ``scripts/api`` and must not hold sync libsql either:
``libsql_experimental``'s native ``execute()``/``commit()`` has NO timeout
and holds the GIL while blocked, so a slow Turso turned the watchdog's
"best-effort heartbeat" into 60s SIGTERM kills (148 timeout results in 7
days). ``http.client`` keep-alive reuses one socket per thread; the socket
timeout is a REAL bound.

The ``service_health`` upsert statement stays single-source in
``scripts/db/service_health_sql.py``; this module only supplies the
transport plus the :func:`write_service_health_http` convenience command.

Also used by hang-risk writers in ``db.writer`` (``record_service_health``,
``upsert_journal_entry``, ``upsert_portfolio_snapshot``, portfolio DELETE)
and by ``db.retention`` / host_metrics / ib_watchdog. Inventory of bounded
vs process-bound paths: ``scripts/db/client.py`` module docstring.
"""

from __future__ import annotations

try:
    from ..utils.outcome_metrics import measure_operation
except ImportError:  # flat script imports
    from utils.outcome_metrics import measure_operation

import base64
import http.client
import io
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Optional, Sequence
from urllib.parse import urlsplit

try:  # scripts/ on sys.path (watchdog, pytest)
    from health_service.turso_http import http_url_from_libsql, read_env
except ImportError:  # imported as scripts.db.hrana_http from the repo root
    from scripts.health_service.turso_http import http_url_from_libsql, read_env

# Direct-to-cloud Turso writes are 30-60 ms in steady state; 4 s absorbs a
# slow tail while staying far below every caller's cycle ceiling.
HRANA_TIMEOUT_S = 4.0
_KEEPALIVE_IDLE_S = 5.0

_MAX_RESPONSE_BYTES = 1_048_576  # writes return tiny bodies

# urllib.request.urlopen opens a fresh TCP/TLS socket per POST. Watchdog
# heartbeats and sequential writer pipelines paid a handshake each time.
# Thread-local keep-alive reuses one socket per worker thread. Tests that
# replace urllib.request.urlopen still take that seam.
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
    _local.last_success = None
    if conn is None:
        return
    try:
        conn.close()
    except OSError:
        pass


def _retry_safe_read(req: urllib.request.Request) -> bool:
    """Only explicit SELECT pipelines may be resent after an unknown outcome.

    hrana_query also accepts row-returning writes, so its caller name cannot
    establish replay safety. Refuse unparsed, mixed and CTE pipelines rather
    than guessing whether a statement has already committed.
    """
    try:
        operations = json.loads(req.data or b"")["requests"]
        if not isinstance(operations, list) or not operations:
            return False
        reads = 0
        for operation in operations:
            if operation.get("type") == "close":
                continue
            if operation.get("type") != "execute":
                return False
            sql = operation["stmt"]["sql"]
            if not isinstance(sql, str):
                return False
            sql = sql.strip().removesuffix(";").strip()
            if not re.match(r"SELECT\b", sql, re.IGNORECASE) or ";" in sql:
                return False
            reads += 1
        return reads > 0
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


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
        _local.last_success = None
        return conn

    last_success = getattr(_local, "last_success", None)
    if getattr(_local, "conn", None) is not None and (
        getattr(_local, "key", None) != key
        or last_success is None
        or time.monotonic() - last_success >= _KEEPALIVE_IDLE_S
    ):
        # Retire before sending: an idle write must never discover a dead
        # socket after its outcome has become uncertain.
        _close_keepalive()
    reused = getattr(_local, "key", None) == key and getattr(_local, "conn", None) is not None
    conn = getattr(_local, "conn", None) if reused else None
    if conn is None:
        conn = connect()
    else:
        conn.timeout = timeout

    try:
        conn.request(method, path, body, headers)
        resp = conn.getresponse()
    except (OSError, http.client.HTTPException):
        # A disconnected peer may have committed the POST before losing its
        # response. Close every poisoned socket, but resend only proven reads.
        _close_keepalive()
        if not reused or not _retry_safe_read(req):
            raise
        conn = connect()
        try:
            conn.request(method, path, body, headers)
            resp = conn.getresponse()
        except Exception:
            _close_keepalive()
            raise
    except Exception:
        _close_keepalive()
        raise

    try:
        # A short read leaves the remainder on this socket. The next pipeline
        # is then sent and discarded (ResponseNotReady), which reports a
        # committed write as a transport failure. REL-326.
        raw = resp.read(_MAX_RESPONSE_BYTES + 1)
        over_cap = len(raw) > _MAX_RESPONSE_BYTES or not resp.isclosed()
        if over_cap or resp.status != 200:
            _close_keepalive()
            if resp.status != 200:
                raise HttpStatusError(resp.status)
            raise OSError("response exceeds limit")
        _local.last_success = time.monotonic()
        return _PooledResponse(raw)
    except Exception:
        _close_keepalive()
        raise


def _open_request(req: urllib.request.Request, timeout: float):
    current = urllib.request.urlopen
    if current is not _STDLIB_URLOPEN:
        return current(req, timeout=timeout)
    return _pooled_open(req, timeout)


class HttpStatusError(OSError):
    """Non-200 HTTP status from the Hrana pipeline. ``.status`` is the code."""

    def __init__(self, status: int):
        self.status = int(status)
        super().__init__(f"HTTP {self.status}")


class HranaHttpError(RuntimeError):
    """Raised on ANY hrana failure: missing creds, HTTP/socket error,
    timeout, malformed response, or a statement-level error."""


_TRANSIENT_HTTP = frozenset({502, 503, 504})
_TRANSPORT_CAUSES = (
    TimeoutError,
    ConnectionResetError,
    BrokenPipeError,
    ssl.SSLEOFError,
)


def is_transport_error(exc: BaseException) -> bool:
    """True for a Turso wire blip. Inspects types, never message text.

    Statement errors and missing credentials are ``HranaHttpError`` with
    no ``__cause__`` and must stay fatal for the grok page responder.
    """
    if isinstance(exc, HranaHttpError):
        if exc.__cause__ is None:
            return False
        return is_transport_error(exc.__cause__)
    if isinstance(exc, HttpStatusError):
        return exc.status in _TRANSIENT_HTTP
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _TRANSIENT_HTTP
    if isinstance(exc, urllib.error.URLError):
        return True
    if isinstance(exc, http.client.RemoteDisconnected):
        return True
    return isinstance(exc, _TRANSPORT_CAUSES)


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
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"type": "blob", "base64": base64.b64encode(bytes(value)).decode("ascii")}
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
            "Connection": "keep-alive",
        },
    )
    with _open_request(req, timeout) as resp:
        raw = resp.read(_MAX_RESPONSE_BYTES)
    return json.loads(raw.decode("utf-8"))


def _check_statement_ok(body: dict) -> None:
    results = body.get("results") or []
    if not results:
        raise HranaHttpError("empty pipeline response")
    first = results[0]
    if first.get("type") != "ok":
        error = first.get("error") or {}
        raise HranaHttpError(str(error.get("message") or "statement failed"))


def _execute_result(body: dict) -> dict:
    results = body.get("results") or []
    if not results:
        raise HranaHttpError("empty pipeline response")
    first = results[0]
    if first.get("type") != "ok":
        error = first.get("error") or {}
        raise HranaHttpError(str(error.get("message") or "statement failed"))
    response = first.get("response") or {}
    if response.get("type") != "execute":
        raise HranaHttpError("unexpected pipeline response type")
    return response.get("result") or {}


def _cell_value(cell: Any) -> Any:
    if not isinstance(cell, dict):
        return cell
    if cell.get("type") == "null":
        return None
    return cell.get("value")


def _rows_as_tuples(result: dict) -> list[tuple]:
    return [
        tuple(_cell_value(cell) for cell in raw_row)
        for raw_row in (result.get("rows") or [])
    ]


def _refuse_pytest_pollution() -> None:
    """Same guard as ``db.client``: a test that forgets to mock this
    transport must never reach production Turso
    (feedback_test_pollution_to_production)."""
    if (
        os.environ.get("PYTEST_CURRENT_TEST")
        and os.environ.get("RADON_DB_TEST_WRITE_OK") != "1"
    ):
        raise HranaHttpError(
            "refusing a real Turso connection under pytest — mock "
            "db.hrana_http or set RADON_DB_TEST_WRITE_OK=1 for explicit "
            "integration tests"
        )


@measure_operation("database")
def hrana_execute(
    sql: str, args: Sequence[Any] = (), timeout: float = HRANA_TIMEOUT_S
) -> None:
    """Execute one bounded statement against Turso over HTTP.

    Writes autocommit per pipeline — no separate ``commit()``. Raises
    :class:`HranaHttpError` on any failure; callers own the fallback
    (the watchdog's heartbeat is best-effort by contract).
    """
    _refuse_pytest_pollution()
    db_url, token = read_env()
    http_origin = http_url_from_libsql(db_url)
    if not http_origin or not token:
        raise HranaHttpError("TURSO_DB_URL / TURSO_AUTH_TOKEN not configured")
    try:
        _check_statement_ok(_post_pipeline(http_origin, token, sql, tuple(args), timeout))
    except HranaHttpError:
        raise
    except Exception as exc:
        raise HranaHttpError(f"{type(exc).__name__}: {exc}") from exc


@measure_operation("database")
def hrana_query(
    sql: str, args: Sequence[Any] = (), timeout: float = HRANA_TIMEOUT_S
) -> list[tuple]:
    """Bounded SELECT (or any row-returning statement) over Hrana HTTP.

    Returns rows as positional tuples. Same timeout/error contract as
    :func:`hrana_execute`. Prefer this over ``libsql_experimental`` for any
    call that must not hang the process indefinitely.
    """
    _refuse_pytest_pollution()
    db_url, token = read_env()
    http_origin = http_url_from_libsql(db_url)
    if not http_origin or not token:
        raise HranaHttpError("TURSO_DB_URL / TURSO_AUTH_TOKEN not configured")
    try:
        body = _post_pipeline(http_origin, token, sql, tuple(args), timeout)
        return _rows_as_tuples(_execute_result(body))
    except HranaHttpError:
        raise
    except Exception as exc:
        raise HranaHttpError(f"{type(exc).__name__}: {exc}") from exc


def write_service_health_http(
    service: str,
    state: str,
    *,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    error: Optional[dict[str, Any]] = None,
    timeout: float = HRANA_TIMEOUT_S,
) -> None:
    """The canonical ``service_health`` upsert over the bounded transport.

    Statement + arg serialization come from ``db.service_health_sql`` so
    this stays in lockstep with the sync writer and the FastAPI heal path.
    """
    try:
        from db.service_health_sql import (
            SERVICE_HEALTH_UPSERT_SQL,
            service_health_upsert_args,
        )
    except ImportError:
        from scripts.db.service_health_sql import (
            SERVICE_HEALTH_UPSERT_SQL,
            service_health_upsert_args,
        )

    hrana_execute(
        SERVICE_HEALTH_UPSERT_SQL,
        service_health_upsert_args(
            service,
            state,
            started_at=started_at,
            finished_at=finished_at,
            error=error,
        ),
        timeout=timeout,
    )

"""Bounded stdlib Turso access for non-API daemons — libSQL HTTP pipeline.

Sister transport to ``scripts/api/db_http.py`` (same hrana wire protocol,
both adapted from ``scripts/health_service/turso_http.py``). This copy
lives under ``scripts/db/`` because oneshot daemons like the IB watchdog
must not import from ``scripts/api`` and must not hold sync libsql either:
``libsql_experimental``'s native ``execute()``/``commit()`` has NO timeout
and holds the GIL while blocked, so a slow Turso turned the watchdog's
"best-effort heartbeat" into 60s SIGTERM kills (148 timeout results in 7
days). urllib's socket timeout is a REAL bound.

The ``service_health`` upsert statement stays single-source in
``scripts/db/service_health_sql.py``; this module only supplies the
transport plus the :func:`write_service_health_http` convenience command.

Also used by hang-risk writers in ``db.writer`` (``record_service_health``,
``upsert_journal_entry``, ``upsert_portfolio_snapshot``, portfolio DELETE)
and by ``db.retention`` / host_metrics / ib_watchdog. Inventory of bounded
vs process-bound paths: ``scripts/db/client.py`` module docstring.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Optional, Sequence

try:  # scripts/ on sys.path (watchdog, pytest)
    from health_service.turso_http import http_url_from_libsql, read_env
except ImportError:  # imported as scripts.db.hrana_http from the repo root
    from scripts.health_service.turso_http import http_url_from_libsql, read_env

# Direct-to-cloud Turso writes are 30-60 ms in steady state; 4 s absorbs a
# slow tail while staying far below every caller's cycle ceiling.
HRANA_TIMEOUT_S = 4.0

_MAX_RESPONSE_BYTES = 1_048_576  # writes return tiny bodies


class HranaHttpError(RuntimeError):
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


SERVICE_HEALTH_SELECT_SQL = (
    "SELECT service, state, last_attempt_started_at, last_attempt_finished_at, "
    "last_error, updated_at FROM service_health WHERE service = ?"
)
_SERVICE_HEALTH_COLUMNS = (
    "service",
    "state",
    "last_attempt_started_at",
    "last_attempt_finished_at",
    "last_error",
    "updated_at",
)


def read_service_health_http(
    service: str, timeout: float = HRANA_TIMEOUT_S
) -> Optional[dict[str, Any]]:
    """Single ``service_health`` row over the bounded transport, keyed by
    column; ``None`` when the service has no row. Raises
    :class:`HranaHttpError` on any transport failure — the caller owns the
    fallback, same contract as :func:`write_service_health_http`.
    """
    rows = hrana_query(SERVICE_HEALTH_SELECT_SQL, (service,), timeout=timeout)
    if not rows:
        return None
    return dict(zip(_SERVICE_HEALTH_COLUMNS, rows[0]))

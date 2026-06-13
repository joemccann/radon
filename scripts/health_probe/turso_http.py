"""Write to Turso over the libsql HTTP (Hrana-over-HTTP) API — stdlib only.

The on-box code uses libsql_experimental (a native client), but the Tier-3
prober runs on a generic GitHub Actions runner with NO native libsql and a
hard stdlib-only / zero-shared-fate contract. libsql exposes a plain HTTP
JSON endpoint — POST /v2/pipeline with a Bearer token — that we drive with
urllib alone. https://docs.turso.tech/sql-over-http

Env (verified against scripts/db/client.py):
  TURSO_DB_URL     — e.g. libsql://radon-xxx.turso.io  (we rewrite the scheme)
  TURSO_AUTH_TOKEN — Bearer JWT (GitHub secret on the runner)
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

PIPELINE_PATH = "/v2/pipeline"
HTTP_TIMEOUT_SECONDS = 10.0


class TursoHttpError(RuntimeError):
    """Raised when the HTTP write to Turso fails or the API returns an error."""


def _read_env() -> tuple[str, str]:
    url = os.environ.get("TURSO_DB_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")
    if not url or not token:
        raise TursoHttpError(
            "TURSO_DB_URL and TURSO_AUTH_TOKEN must be set "
            "(GitHub secret TURSO_AUTH_TOKEN on the runner)."
        )
    return url, token


def http_base_url(db_url: str) -> str:
    """Rewrite a libsql:// (or wss://) DB URL to the https:// HTTP endpoint.

    libsql://host        -> https://host
    https://host         -> https://host (already fine)
    http://host          -> http://host  (local sqld dev)
    """
    if db_url.startswith("libsql://"):
        return "https://" + db_url[len("libsql://"):]
    if db_url.startswith("wss://"):
        return "https://" + db_url[len("wss://"):]
    if db_url.startswith("ws://"):
        return "http://" + db_url[len("ws://"):]
    return db_url


def build_upsert_pipeline(row: dict) -> dict:
    """Build the /v2/pipeline request body for an idempotent UPSERT.

    INSERT ... ON CONFLICT(source) DO UPDATE keeps exactly one row per source
    (latest-per-source) — the UNIQUE(source) / PRIMARY KEY in 0008 is the hook.
    Named args avoid positional drift. close=True so the connection isn't held.
    """
    sql = (
        "INSERT INTO external_probe "
        "(source, ok, http_status, latency_ms, detail, checked_at) "
        "VALUES (:source, :ok, :http_status, :latency_ms, :detail, :checked_at) "
        "ON CONFLICT(source) DO UPDATE SET "
        "ok=excluded.ok, http_status=excluded.http_status, "
        "latency_ms=excluded.latency_ms, detail=excluded.detail, "
        "checked_at=excluded.checked_at"
    )
    return {
        "requests": [
            {
                "type": "execute",
                "stmt": {
                    "sql": sql,
                    "named_args": [
                        {"name": "source", "value": _arg(row.get("source"))},
                        {"name": "ok", "value": _arg(row.get("ok"))},
                        {"name": "http_status", "value": _arg(row.get("http_status"))},
                        {"name": "latency_ms", "value": _arg(row.get("latency_ms"))},
                        {"name": "detail", "value": _arg(row.get("detail"))},
                        {"name": "checked_at", "value": _arg(row.get("checked_at"))},
                    ],
                },
            },
            {"type": "close"},
        ]
    }


# DUR-16: keep the append-only history bounded without a separate janitor —
# the prober itself deletes anything older than this on every run (the
# 0008-era in-probe prune style; same precedent as host_metrics).
RUNS_RETENTION_DAYS = 30

_RUNS_PRUNE_SQL = (
    "DELETE FROM external_probe_runs "
    "WHERE run_at < datetime('now', '-%d days')" % RUNS_RETENTION_DAYS
)


def build_insert_run_pipeline(row: dict) -> dict:
    """Build the /v2/pipeline body for one append-only external_probe_runs row
    (DUR-16 history) plus the bounded 30-day prune. close=True as above."""
    sql = (
        "INSERT INTO external_probe_runs "
        "(run_at, edge_ok, user_path_ok, freshness_ok, tick_fresh, scan_fresh, detail, latency_ms) "
        "VALUES (:run_at, :edge_ok, :user_path_ok, :freshness_ok, :tick_fresh, :scan_fresh, :detail, :latency_ms)"
    )
    columns = ("run_at", "edge_ok", "user_path_ok", "freshness_ok",
               "tick_fresh", "scan_fresh", "detail", "latency_ms")
    return {
        "requests": [
            {
                "type": "execute",
                "stmt": {
                    "sql": sql,
                    "named_args": [
                        {"name": column, "value": _arg(row.get(column))} for column in columns
                    ],
                },
            },
            {"type": "execute", "stmt": {"sql": _RUNS_PRUNE_SQL}},
            {"type": "close"},
        ]
    }


def _arg(value) -> dict:
    """Encode a Python scalar as a Hrana typed value."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": float(value)}
    return {"type": "text", "value": str(value)}


def _raise_on_pipeline_error(parsed: dict) -> None:
    """The pipeline endpoint returns 200 even when a statement errored — the
    error rides inside results[].type == 'error'. Surface it as a failure so
    the prober's exit code reflects a real write failure."""
    for result in parsed.get("results", []) or []:
        if isinstance(result, dict) and result.get("type") == "error":
            err = result.get("error", {})
            raise TursoHttpError("pipeline error: " + str(err.get("message", err)))


def upsert_external_probe(row: dict) -> None:
    """POST the UPSERT pipeline to Turso. Raises TursoHttpError on any failure."""
    _post_pipeline(build_upsert_pipeline(row))


def insert_external_probe_run(row: dict) -> None:
    """POST one history row (+ prune) to Turso. Raises TursoHttpError on failure."""
    _post_pipeline(build_insert_run_pipeline(row))


def _post_pipeline(pipeline: dict) -> None:
    db_url, token = _read_env()
    endpoint = http_base_url(db_url).rstrip("/") + PIPELINE_PATH
    body = json.dumps(pipeline).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "User-Agent": "radon-tier3-probe/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            raw = resp.read(65536)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", "replace") if hasattr(exc, "read") else ""
        raise TursoHttpError("HTTP %s from Turso: %s" % (exc.code, detail[:300])) from exc
    except urllib.error.URLError as exc:
        raise TursoHttpError("transport error to Turso: %s" % exc.reason) from exc
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError as exc:
        raise TursoHttpError("non-JSON response from Turso pipeline") from exc
    _raise_on_pipeline_error(parsed)

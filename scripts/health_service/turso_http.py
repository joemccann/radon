"""stdlib-only libSQL/Turso HTTP client for the standalone health daemon.

Reads the Turso `service_health` table over the libSQL HTTP "pipeline" API
(POST {db}/v2/pipeline) using ONLY urllib.request + json, so the daemon never
imports `scripts.db.client` / `libsql_experimental` and the stdlib-only
isolation contract (test_health_service.TestStdlibOnlyIsolation) stays intact.

Everything here is bounded + fully wrapped: a Turso outage, timeout, missing
creds, or malformed response degrades the section to state "unknown" and NEVER
raises. The daemon runs fine with NO Turso creds configured.

Credential env var names mirror scripts/db/client.py:_read_env exactly:
  TURSO_DB_URL      e.g. libsql://radon-xxx.turso.io
  TURSO_AUTH_TOKEN  bearer token for the HTTP API
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


SERVICE_HEALTH_COLUMNS = (
    "service",
    "state",
    "last_attempt_started_at",
    "last_attempt_finished_at",
    "last_error",
    "updated_at",
)

_SERVICE_HEALTH_SQL = (
    "SELECT service, state, last_attempt_started_at, last_attempt_finished_at, "
    "last_error, updated_at FROM service_health ORDER BY service"
)

# Freshest off-box probe row (Tier-3). external_probe is latest-per-source; we
# surface the most recently-checked source for the admin Off-box tile.
_EXTERNAL_PROBE_SQL = (
    "SELECT source, ok, http_status, latency_ms, checked_at, detail "
    "FROM external_probe ORDER BY checked_at DESC LIMIT 1"
)


def _external_probe_sql(source: str | None = None) -> str:
    if source is None:
        return _EXTERNAL_PROBE_SQL
    literal = source.replace("'", "''")
    return (
        "SELECT source, ok, http_status, latency_ms, checked_at, detail "
        f"FROM external_probe WHERE source = '{literal}' "
        "ORDER BY checked_at DESC LIMIT 1"
    )


def http_url_from_libsql(url: str) -> str:
    """Convert a libsql:// (or ws://) DB URL to the https:// HTTP-API origin.

    The libSQL HTTP pipeline API is served over https on the same host. Already-
    https/http URLs pass through unchanged. Returns "" for falsy input so callers
    can treat "no creds" uniformly.
    """
    if not url:
        return ""
    if url.startswith("libsql://"):
        return "https://" + url[len("libsql://"):]
    if url.startswith("wss://"):
        return "https://" + url[len("wss://"):]
    if url.startswith("ws://"):
        return "http://" + url[len("ws://"):]
    return url


def read_env() -> tuple[str, str]:
    """Return (db_url, auth_token) from the same env vars scripts/db/client.py
    uses. Missing values come back as empty strings (no raise)."""
    return (
        os.environ.get("TURSO_DB_URL", "") or "",
        os.environ.get("TURSO_AUTH_TOKEN", "") or "",
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cell_value(cell):
    """libSQL Hrana cells are {"type": "...", "value": ...} (or null/blob/etc.).
    Return the Python-friendly scalar; None for SQL NULL."""
    if cell is None:
        return None
    if not isinstance(cell, dict):
        return cell
    kind = cell.get("type")
    if kind in ("null", None):
        return None
    value = cell.get("value")
    if kind == "integer":
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if kind == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return value


def _parse_age_secs(updated_at, now_secs: float):
    """Seconds since `updated_at` (ISO-8601, naive treated as UTC). None on parse
    failure — we expose raw age only; staleness judgement is the consumer's job
    (see web/lib/serviceHealthWindows.ts), deliberately NOT reimplemented here."""
    if not updated_at or not isinstance(updated_at, str):
        return None
    text = updated_at.strip().replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return round(now_secs - dt.timestamp(), 1)


def _rows_from_pipeline(body: dict) -> list[dict]:
    """Pull the first execute result's rows out of a /v2/pipeline response and
    shape them into {column: value, age_secs}. Raises on an unexpected shape so
    the caller degrades to 'unknown'."""
    results = body["results"]
    first = next(r for r in results if r.get("type") == "ok"
                 and r.get("response", {}).get("type") == "execute")
    result = first["response"]["result"]
    col_names = [c.get("name") for c in result.get("cols", [])]
    now_secs = time.time()
    rows = []
    for raw_row in result.get("rows", []):
        record = {col_names[i]: _cell_value(cell) for i, cell in enumerate(raw_row)
                  if i < len(col_names)}
        record["age_secs"] = _parse_age_secs(record.get("updated_at"), now_secs)
        rows.append(record)
    return rows


def _post_pipeline(http_origin: str, token: str, sql: str, timeout: float) -> dict:
    payload = json.dumps({
        "requests": [
            {"type": "execute", "stmt": {"sql": sql}},
            {"type": "close"},
        ]
    }).encode("utf-8")
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
        raw = resp.read(1_048_576)
    return json.loads(raw.decode("utf-8"))


def fetch_service_health(timeout: float = 2.5) -> dict:
    """Read the service_health table over stdlib HTTP. ALWAYS returns a dict;
    never raises.

    Shapes:
      ok       -> {"state": "ok", "rows": [...], "fetched_at": iso, "row_count": n}
      no creds -> {"state": "unknown", "detail": "no_creds", "rows": []}
      failure  -> {"state": "unknown", "detail": "<reason>", "rows": []}

    Each row is {service, state, last_attempt_started_at, last_attempt_finished_at,
    last_error, updated_at, age_secs}. `age_secs` is the raw seconds since the
    last write — consumers judge staleness, this module does not.
    """
    db_url, token = read_env()
    http_origin = http_url_from_libsql(db_url)
    if not http_origin or not token:
        return {"state": "unknown", "detail": "no_creds", "rows": []}

    try:
        body = _post_pipeline(http_origin, token, _SERVICE_HEALTH_SQL, timeout)
        rows = _rows_from_pipeline(body)
        return {
            "state": "ok",
            "rows": rows,
            "row_count": len(rows),
            "fetched_at": _now_iso(),
        }
    except urllib.error.HTTPError as exc:
        return {"state": "unknown", "detail": "http_%s" % exc.code, "rows": []}
    except urllib.error.URLError as exc:
        return {"state": "unknown", "detail": str(getattr(exc, "reason", exc))[:80], "rows": []}
    except (TimeoutError, OSError) as exc:
        return {"state": "unknown", "detail": exc.__class__.__name__, "rows": []}
    except (KeyError, ValueError, TypeError, StopIteration, IndexError):
        return {"state": "unknown", "detail": "bad_response", "rows": []}
    except Exception:  # noqa: BLE001 - last-resort guard; section must never raise
        return {"state": "unknown", "detail": "fetch_error", "rows": []}


def fetch_external_probe(timeout: float = 2.5, source: str | None = None):
    """Read the freshest external_probe row (the Tier-3 off-box witness) over
    stdlib HTTP. Returns a dict {source, ok, http_status, latency_ms, checked_at,
    detail} or None (no creds / no row / any failure). When ``source`` is set,
    only that independent observer can satisfy the read. NEVER raises."""
    db_url, token = read_env()
    http_origin = http_url_from_libsql(db_url)
    if not http_origin or not token:
        return None
    try:
        body = _post_pipeline(http_origin, token, _external_probe_sql(source), timeout)
        rows = _rows_from_pipeline(body)
        if not rows:
            return None
        row = rows[0]
        row.pop("age_secs", None)  # external_probe uses checked_at, not updated_at
        return row
    except Exception:  # noqa: BLE001 - off-box tile must never crash /status
        return None


_CACHE_UNSET = object()


class ServiceHealthCache:
    """~5s TTL cache around fetch_service_health, mirroring UnitStateCache's
    keep-last-value-on-failure ethos. Fetched lazily on read (bounded), so a slow
    Turso never blocks the request hot path for more than `timeout` and otherwise
    serves the cached snapshot."""

    def __init__(self, ttl: float = 5.0, timeout: float = 2.5, fetch_fn=fetch_service_health,
                 default=_CACHE_UNSET):
        self._ttl = ttl
        self._timeout = timeout
        self._fetch_fn = fetch_fn
        # service_health default is the "unknown" envelope; callers wrapping a
        # differently-shaped fetch (e.g. fetch_external_probe -> dict|None) pass
        # their own default.
        self._default = (
            {"state": "unknown", "detail": "uninitialized", "rows": []}
            if default is _CACHE_UNSET
            else default
        )
        self._value = self._default
        self._fetched_monotonic = None
        # ThreadingHTTPServer dispatches /status concurrently; serialize the
        # TTL-gated fetch so two requests can't race _value/_fetched_monotonic
        # or stampede Turso on a simultaneous cache-miss.
        self._lock = threading.Lock()

    def snapshot(self):
        with self._lock:
            now = time.monotonic()
            if self._fetched_monotonic is None or (now - self._fetched_monotonic) >= self._ttl:
                try:
                    self._value = self._fetch_fn(timeout=self._timeout)
                except Exception:  # noqa: BLE001 - fetch_fn is contracted not to raise; belt + braces
                    self._value = (
                        {"state": "unknown", "detail": "fetch_error", "rows": []}
                        if isinstance(self._default, dict)
                        else self._default
                    )
                self._fetched_monotonic = now
            # Copy dict values so callers can't mutate the cache; pass other
            # shapes (e.g. external_probe -> dict|None) through as-is.
            return dict(self._value) if isinstance(self._value, dict) else self._value

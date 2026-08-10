"""Tier-3 OFF-BOX prober: GET the public edge, classify, write to Turso.

stdlib-only by contract (see package docstring). The pure parse/classify/
row-construction logic is broken out so it is testable without sockets:

  * probe_endpoint()       — bounded urllib GET -> raw probe dict (one HTTP call)
  * probe_user_path()      — unauthenticated GET /dashboard, redirects NOT followed
  * probe_freshness()      — bearer-auth GET /api/probe/freshness (DUR-16)
  * classify_probes()      — combine ping + status probes -> ok/detail
  * classify_user_path()   — Clerk-wall verdict for the user path
  * classify_freshness()   — data freshness plus fail-closed endpoint availability
  * build_probe_row()      — assemble the external_probe UPSERT row
  * build_runs_row()       — assemble the append-only external_probe_runs row
  * exit_code_for()        — the alerting policy (see below)
  * run_probe()            — orchestrate: probe all, classify, write (impure)

Usage (off-box, e.g. GitHub Actions):
    TURSO_DB_URL=... TURSO_AUTH_TOKEN=... RADON_PROBE_FRESHNESS_TOKEN=... \
        python -m health_probe.probe

Exit-code policy (DUR-16, closing the DUR-04 residual — GitHub emails the
operator on workflow FAILURE, so a nonzero exit IS the stopgap alert):
  * 1              — could not write to Turso (the run recorded nothing)
  * EXIT_UNHEALTHY — edge/user path down; freshness endpoint/database unavailable;
                     or valid data explicitly stale during market_state == "open"
  * 0              — otherwise; the row still records any soft degradation
"""
from __future__ import annotations

import errno
import json
import math
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from health_probe.turso_http import (
    TursoHttpError,
    insert_external_probe_run,
    upsert_external_probe,
)

# Identity recorded in external_probe.source. Stable so the row UPSERTs in
# place and the dead-man's-switch reader can look it up by name.
PROBE_SOURCE = os.environ.get("EXTERNAL_PROBE_SOURCE", "github-actions/edge")

EDGE_BASE = os.environ.get("EXTERNAL_PROBE_EDGE_BASE", "https://app.radon.run")
PING_PATH = "/edge-health/ping"
STATUS_PATH = "/edge-health/status"
USER_PATH = "/dashboard"
FRESHNESS_PATH = "/api/probe/freshness"

# Healthy user-path redirect targets, from live production evidence
# (2026-06-12): Clerk 307s to the on-domain /sign-in; the hosted fallback
# lives on clerk.radon.run. Anything else is not the Clerk wall.
SIGN_IN_LOCATION_MARKERS = ("/sign-in", "clerk.")

# Nonzero-but-distinct from the write-failure exit (1) so a red workflow run
# can be triaged from the email subject line alone.
EXIT_UNHEALTHY = 2

# Bounded so a hung edge can't wedge the runner. GH cron already lags; keep the
# whole probe comfortably under a minute even with both endpoints retried once.
HTTP_TIMEOUT_SECONDS = 8.0
MAX_RESPONSE_BYTES = 65536
PROBE_RETRY_DELAY_SECONDS = 0.5


def _now_iso() -> str:
    """ISO-8601 UTC, second precision, trailing Z. The dead-man's-switch input."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _classify_transport_error(exc: Exception) -> str:
    """Map a transport failure to a short machine-readable reason. A timeout or
    unreachable host is NOT proof the box is down (could be a runner-side blip),
    but for the OUTERMOST ring we still record ok=0 — the edge did not answer."""
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    if getattr(exc, "errno", None) == errno.ECONNREFUSED:
        return "refused"
    return "unreachable"


def probe_endpoint(url: str, timeout: float = HTTP_TIMEOUT_SECONDS, headers: dict | None = None) -> dict:
    """GET one URL with a bounded timeout. Returns a raw probe dict:

      reachable=True  -> {reachable, http_status, latency_ms, payload}
      reachable=False -> {reachable, http_status?, latency_ms?, detail}

    An HTTP error response (4xx/5xx) is still 'reachable' — the edge answered —
    but carries its status so classify_probes() can decide healthiness. A
    transport failure (timeout / refused / DNS) is not reachable.
    """
    started = time.monotonic()
    request_headers = {"User-Agent": "radon-tier3-probe/1", **(headers or {})}
    request = urllib.request.Request(url, method="GET", headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES)
            latency_ms = int((time.monotonic() - started) * 1000)
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                payload = {}
            return {
                "reachable": True,
                "http_status": int(getattr(resp, "status", 200)),
                "latency_ms": latency_ms,
                "payload": payload,
            }
    except urllib.error.HTTPError as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "reachable": True,
            "http_status": int(exc.code),
            "latency_ms": latency_ms,
            "payload": {},
        }
    except urllib.error.URLError as exc:
        reason = exc.reason
        detail = _classify_transport_error(reason) if isinstance(reason, Exception) else "unreachable"
        return {"reachable": False, "detail": detail}
    except (socket.timeout, TimeoutError):
        return {"reachable": False, "detail": "timeout"}
    except OSError as exc:
        return {"reachable": False, "detail": _classify_transport_error(exc)}


def probe_endpoint_with_retry(
    url: str,
    timeout: float = HTTP_TIMEOUT_SECONDS,
    headers: dict | None = None,
    *,
    probe_fn=None,
    sleep_fn=time.sleep,
) -> dict:
    """Retry one transient transport/5xx failure exactly once.

    Authentication and other ordinary 4xx responses are deterministic and are
    returned immediately. Two bounded attempts keep the full scheduled probe
    under its one-minute budget while filtering a runner/DNS blip.
    """
    endpoint = probe_fn or probe_endpoint
    first = endpoint(url, timeout=timeout, headers=headers)
    status = int(first.get("http_status", 0)) if first.get("reachable") else 0
    retryable = (
        not first.get("reachable")
        or status in {408, 425, 429}
        or status >= 500
    )
    if not retryable:
        return first
    sleep_fn(PROBE_RETRY_DELAY_SECONDS)
    return endpoint(url, timeout=timeout, headers=headers)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Surface 3xx as the result instead of following it — the redirect IS the
    signal for the user-path check (following it would also hit Clerk's hosted
    pages and measure their health, not ours)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _header(headers, name: str):
    """Case-insensitive header lookup over email.message.Message or dict."""
    if headers is None:
        return None
    value = headers.get(name)
    if value is not None:
        return value
    lowered = name.lower()
    try:
        items = headers.items()
    except AttributeError:
        return None
    for key, val in items:
        if str(key).lower() == lowered:
            return val
    return None


def _user_path_result(status: int, headers, started: float) -> dict:
    return {
        "reachable": True,
        "http_status": int(status),
        "location": _header(headers, "Location"),
        "clerk_auth_status": _header(headers, "x-clerk-auth-status"),
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


def probe_user_path(url: str, timeout: float = HTTP_TIMEOUT_SECONDS) -> dict:
    """Unauthenticated browser-shaped GET of a Clerk-protected page, redirects
    NOT followed. Accept: text/html is load-bearing: without it Clerk serves a
    protect-rewrite 404 instead of the 307 (verified live 2026-06-12); we send
    it to exercise the same path a real signed-out browser hits."""
    started = time.monotonic()
    request = urllib.request.Request(url, method="GET", headers={
        "User-Agent": "radon-tier3-probe/1",
        "Accept": "text/html,application/xhtml+xml",
    })
    opener = urllib.request.build_opener(_NoRedirectHandler)
    try:
        with opener.open(request, timeout=timeout) as resp:
            resp.read(MAX_RESPONSE_BYTES)
            return _user_path_result(int(getattr(resp, "status", 200)), getattr(resp, "headers", None), started)
    except urllib.error.HTTPError as exc:
        return _user_path_result(exc.code, exc.headers, started)
    except urllib.error.URLError as exc:
        reason = exc.reason
        detail = _classify_transport_error(reason) if isinstance(reason, Exception) else "unreachable"
        return {"reachable": False, "detail": detail}
    except (socket.timeout, TimeoutError):
        return {"reachable": False, "detail": "timeout"}
    except OSError as exc:
        return {"reachable": False, "detail": _classify_transport_error(exc)}


def classify_user_path(raw: dict) -> dict:
    """The Clerk wall answered = healthy. Live evidence (2026-06-12):
    Accept: text/html -> 307 Location: https://app.radon.run/sign-in?...;
    bare GET -> protect-rewrite 404, both with x-clerk-auth-status: signed-out.
    A 200 WITHOUT any Clerk header means the perimeter did not run — that is a
    failure (the middleware-is-the-perimeter class), not a pass."""
    if not raw.get("reachable"):
        return {"ok": 0, "detail": "user_path_unreachable:" + str(raw.get("detail", "?"))}
    status = int(raw.get("http_status", 0))
    if 300 <= status < 400:
        location = raw.get("location") or ""
        if any(marker in location for marker in SIGN_IN_LOCATION_MARKERS):
            return {"ok": 1, "detail": "clerk_redirect"}
        return {"ok": 0, "detail": "user_path_redirect_unexpected"}
    if (
        raw.get("clerk_auth_status") == "signed-out"
        and status in {200, 401, 404}
    ):
        return {"ok": 1, "detail": "clerk_protect_%d" % status}
    if status == 200:
        return {"ok": 0, "detail": "user_path_http_200_no_clerk"}
    return {"ok": 0, "detail": "user_path_http_%d" % status}


def probe_freshness(url: str, token: str, timeout: float = HTTP_TIMEOUT_SECONDS) -> dict:
    """Bearer-auth GET of /api/probe/freshness. No token -> skip the request
    entirely (a tokenless 401 would be indistinguishable from a broken edge)."""
    if not token:
        return {"reachable": False, "detail": "no_token", "skipped": True}
    return probe_endpoint_with_retry(
        url,
        timeout=timeout,
        headers={"Authorization": "Bearer " + token},
    )


def _freshness_availability_failure(detail: str, market_state=None) -> dict:
    return {"freshness_ok": 0, "tick_fresh": None, "scan_fresh": None,
            "market_state": market_state, "availability_ok": 0, "detail": detail}


def _check_fresh_flag(check) -> int | None:
    """Null-safe 1/0/None from one {applicable, age_secs, fresh} check."""
    if not isinstance(check, dict):
        return None
    fresh = check.get("fresh")
    return None if fresh is None else int(bool(fresh))


_EXPECTED_FRESHNESS_CHECKS = frozenset(
    {"relay_tick", "vcg_scan", "gex_scan", "journal"}
)
_FRESHNESS_CHECK_FIELDS = frozenset({"applicable", "age_secs", "fresh"})


def _valid_freshness_check(check) -> bool:
    """Validate one fixed-contract freshness check without coercion."""
    if not isinstance(check, dict) or set(check) != _FRESHNESS_CHECK_FIELDS:
        return False

    applicable = check["applicable"]
    age_secs = check["age_secs"]
    fresh = check["fresh"]
    if type(applicable) is not bool:  # bool is intentionally not an integer here
        return False
    if age_secs is not None:
        if (
            isinstance(age_secs, bool)
            or not isinstance(age_secs, (int, float))
            or not math.isfinite(age_secs)
            or age_secs < 0
        ):
            return False

    if applicable:
        return type(fresh) is bool
    return age_secs is None and fresh is None


def _derived_all_fresh(checks: dict) -> bool | None:
    applicable = [check for check in checks.values() if check["applicable"]]
    if not applicable:
        return None
    return all(check["fresh"] is True for check in applicable)


def _combined_scan_flag(*flags) -> int | None:
    """AND across the scan pair, null-safe: any 0 poisons, all-None stays None."""
    known = [flag for flag in flags if flag is not None]
    if not known:
        return None
    return 0 if 0 in known else 1


def classify_freshness(raw: dict) -> dict:
    """Freshness verdict per the DUR-16 contract: healthy = HTTP 200 AND
    all_fresh in (true, null) — null means a quiet market, not a failure.
    Endpoint/transport/database availability failures are distinct from valid
    data staleness so they can page at any hour. Only a valid 200 payload's data
    freshness remains market-gated."""
    if raw.get("skipped"):
        return _freshness_availability_failure("freshness_no_token")
    if not raw.get("reachable"):
        return _freshness_availability_failure(
            "freshness_unreachable:" + str(raw.get("detail", "?")))
    status = int(raw.get("http_status", 0))
    if not (200 <= status < 300):
        return _freshness_availability_failure("freshness_http_%d" % status)

    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
    market_state = payload.get("market_state")
    all_fresh = payload.get("all_fresh")
    database_ok = payload.get("database_ok")
    database_failures = payload.get("database_failures")
    checks_valid = (
        set(checks) == _EXPECTED_FRESHNESS_CHECKS
        and all(_valid_freshness_check(checks[name]) for name in _EXPECTED_FRESHNESS_CHECKS)
    )
    failures_valid = (
        isinstance(database_failures, list)
        and all(
            type(name) is str and name in _EXPECTED_FRESHNESS_CHECKS
            for name in database_failures
        )
        and len(database_failures) == len(set(database_failures))
    )
    derived_all_fresh = _derived_all_fresh(checks) if checks_valid else None
    malformed = (
        not isinstance(payload.get("generated_at"), str)
        or not payload.get("generated_at", "").strip()
        or "all_fresh" not in payload
        or not isinstance(market_state, str)
        or market_state not in {"open", "extended", "closed"}
        or not checks_valid
        or (all_fresh is not None and type(all_fresh) is not bool)
        or (checks_valid and all_fresh is not derived_all_fresh)
        or type(database_ok) is not bool
        or not failures_valid
        or (
            type(database_ok) is bool
            and failures_valid
            and database_ok is not (len(database_failures) == 0)
        )
    )
    if malformed:
        return _freshness_availability_failure("freshness_malformed_payload", market_state)
    if database_ok is False:
        return _freshness_availability_failure(
            "freshness_database_unavailable", market_state)
    healthy = derived_all_fresh in (True, None)
    return {
        "freshness_ok": 1 if healthy else 0,
        "tick_fresh": _check_fresh_flag(checks.get("relay_tick")),
        "scan_fresh": _combined_scan_flag(_check_fresh_flag(checks.get("vcg_scan")),
                                          _check_fresh_flag(checks.get("gex_scan"))),
        "market_state": market_state,
        "availability_ok": 1,
        "detail": "fresh" if healthy else "stale",
    }


def _legacy_status_payload_healthy(payload: dict) -> bool:
    """Validate and classify the immediately preceding health schema.

    The off-box consumer can run from ``main`` before the on-box producer has
    restarted. Supporting one explicit predecessor avoids rollout alarms while
    still deriving the verdict from current direct evidence. Arbitrary opaque
    HTTP-200 bodies remain failures.
    """
    probes = payload.get("probes")
    units = payload.get("units")
    units_age_secs = payload.get("units_age_secs")
    if (
        payload.get("health_service") != "ok"
        or not isinstance(payload.get("generated_at"), str)
        or not payload["generated_at"].strip()
        or not isinstance(probes, dict)
        or not probes
        or not isinstance(units, dict)
        or not units
        or isinstance(units_age_secs, bool)
        or not isinstance(units_age_secs, (int, float))
        or not math.isfinite(units_age_secs)
        or not 0 <= units_age_secs <= 30
    ):
        return False

    states = []
    for collection in (probes, units):
        for value in collection.values():
            if not isinstance(value, dict):
                return False
            state = value.get("state")
            if not isinstance(state, str):
                return False
            states.append(state.lower())

    api_probe = probes.get("radon-api")
    if not isinstance(api_probe, dict) or api_probe.get("state") != "up":
        return False
    api_payload = api_probe.get("payload")
    if not isinstance(api_payload, dict):
        return False
    if (
        api_payload.get("service_state") not in {"healthy", "up", "ok"}
        or api_payload.get("auth_state") != "authenticated"
        or api_payload.get("upstream_dead") is not False
        or api_payload.get("port_listening") is not True
    ):
        return False

    return bool(states) and all(state in {"up", "ok", "healthy"} for state in states)


def _classify_status_payload(payload: dict) -> str:
    """Classify the Tier-2 aggregate as ``healthy``, ``down``, or ``invalid``.

    Schema v2 publishes ``ok`` plus ``overall_state``. Exactly one validated
    predecessor is accepted during producer-first rolling deploys; unknown
    versions, contradictory fields, and opaque bodies are invalid public
    contracts rather than recoverable aggregate-down observations.
    """
    if not isinstance(payload, dict) or not payload:
        return "invalid"
    schema_version = payload.get("schema_version")
    if schema_version == 2:
        state = payload.get("overall_state")
        if type(payload.get("ok")) is not bool or not isinstance(state, str):
            return "invalid"
        normalized = state.lower()
        if normalized not in {"up", "down", "unknown", "starting", "degraded"}:
            return "invalid"
        state_healthy = normalized == "up"
        if payload["ok"] is not state_healthy:
            return "invalid"
        if normalized == "degraded":
            # Broker dependency down, serving path up (2026-08-09): not an
            # edge outage — surfaced distinctly so classify_probes can keep
            # the off-box row ok while on-box alerting owns the dependency.
            return "degraded"
        return "healthy" if state_healthy else "down"
    if schema_version is not None:
        return "invalid"

    # Validated predecessor used during producer-first rollout. A legacy
    # failure remains fail-closed/invalid because only schema v2 has an exact
    # aggregate-down contract that the on-box recovery path may supersede.
    if "ok" not in payload and "overall_state" not in payload and "state" not in payload:
        return "healthy" if _legacy_status_payload_healthy(payload) else "invalid"
    state = payload.get("overall_state", payload.get("state"))
    state_healthy = (
        isinstance(state, str) and state.lower() in {"up", "ok", "healthy"}
    )
    if isinstance(payload.get("ok"), bool):
        return "healthy" if payload["ok"] is True and state_healthy else "invalid"
    return "healthy" if state_healthy else "invalid"


def _is_status_payload_healthy(payload: dict) -> bool:
    """Backward-compatible boolean wrapper for callers and tests."""
    return _classify_status_payload(payload) == "healthy"


def classify_probes(ping: dict, status: dict) -> dict:
    """Combine the ping + status probe dicts into {ok, detail}.

    ok=1 requires BOTH: the static ping returned 2xx AND the aggregate status
    returned 2xx with a non-error payload. The static ping isolates 'is the
    edge serving at all' from 'is the daemon's aggregate happy', so the detail
    string says which ring failed.
    """
    if not ping.get("reachable"):
        return {"ok": 0, "detail": "ping_unreachable:" + str(ping.get("detail", "?"))}
    ping_status = int(ping.get("http_status", 0))
    if not (200 <= ping_status < 300):
        return {"ok": 0, "detail": "ping_http_%d" % ping_status}

    if not status.get("reachable"):
        return {"ok": 0, "detail": "status_unreachable:" + str(status.get("detail", "?"))}
    status_code = int(status.get("http_status", 0))
    if not (200 <= status_code < 300):
        return {"ok": 0, "detail": "status_http_%d" % status_code}

    aggregate = _classify_status_payload(status.get("payload", {}))
    if aggregate == "down":
        return {"ok": 0, "detail": "aggregate_down"}
    if aggregate == "degraded":
        return {"ok": 1, "detail": "edge_ok:aggregate_degraded"}
    if aggregate != "healthy":
        return {"ok": 0, "detail": "aggregate_invalid"}

    return {"ok": 1, "detail": "edge_ok"}


def build_probe_row(source: str, ping: dict, status: dict, checked_at: str) -> dict:
    """Assemble the external_probe UPSERT row from the two probe dicts.

    http_status is the aggregate /status code (the meaningful one — it carries
    the daemon's verdict). latency_ms is the SLOWER of the two reachable
    endpoints (worst-case round-trip). Both are None on transport failure so a
    NULL in the row unambiguously means 'never got an HTTP answer'.
    """
    classification = classify_probes(ping, status)
    http_status = status.get("http_status") if status.get("reachable") else None
    latencies = [p.get("latency_ms") for p in (ping, status) if p.get("reachable") and p.get("latency_ms") is not None]
    latency_ms = max(latencies) if latencies else None
    return {
        "source": source,
        "ok": int(classification["ok"]),
        "http_status": http_status,
        "latency_ms": latency_ms,
        "detail": classification["detail"],
        "checked_at": checked_at,
    }


def build_runs_row(edge_row: dict, user_path: dict, freshness: dict,
                   run_at: str, latency_ms: float | None) -> dict:
    """Assemble one append-only external_probe_runs row (DUR-16 history)."""
    detail = json.dumps({
        "edge": edge_row["detail"],
        "user_path": user_path["detail"],
        "freshness": freshness["detail"],
        "market_state": freshness["market_state"],
    })
    return {
        "run_at": run_at,
        "edge_ok": int(edge_row["ok"]),
        "user_path_ok": int(user_path["ok"]),
        "freshness_ok": freshness["freshness_ok"],
        "tick_fresh": freshness["tick_fresh"],
        "scan_fresh": freshness["scan_fresh"],
        "detail": detail,
        "latency_ms": latency_ms,
    }


def exit_code_for(edge_ok: int, user_path_ok: int, freshness_ok, market_state,
                  availability_ok=None) -> int:
    """Page for control-plane availability at any hour; market-gate only data.

    GitHub emails on a red workflow run. A valid endpoint with stale data pages
    only during regular trading hours; endpoint, auth, transport, schema, or DB
    availability failure pages regardless of market state.
    """
    if not edge_ok or not user_path_ok or availability_ok == 0:
        return EXIT_UNHEALTHY
    if freshness_ok == 0 and market_state == "open":
        return EXIT_UNHEALTHY
    return 0


def _isolated(check_name: str, fn) -> dict:
    """Run one probe callable; an unexpected internal error becomes an
    unreachable-shaped result instead of killing the whole run (each check is
    failure-isolated so the edge write always lands)."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — isolation boundary by design
        sys.stderr.write("[health_probe] %s check crashed: %r\n" % (check_name, exc))
        return {"reachable": False, "detail": "internal:%s" % type(exc).__name__}


def _worst_latency_ms(*raws) -> float | None:
    latencies = [raw.get("latency_ms") for raw in raws
                 if raw.get("reachable") and raw.get("latency_ms") is not None]
    return float(max(latencies)) if latencies else None


def run_probe(source: str = PROBE_SOURCE) -> dict:
    """Probe the edge pair + user path + freshness endpoint, classify, UPSERT
    the latest-state row, and append one history row. Impure (network + DB).
    Returns {edge_row, runs_row, exit_code}. Raises TursoHttpError if either
    write fails."""
    base = EDGE_BASE.rstrip("/")
    ping = probe_endpoint_with_retry(base + PING_PATH)
    status = probe_endpoint_with_retry(base + STATUS_PATH)
    user_raw = _isolated("user_path", lambda: probe_user_path(base + USER_PATH))
    freshness_raw = _isolated("freshness", lambda: probe_freshness(
        base + FRESHNESS_PATH, os.environ.get("RADON_PROBE_FRESHNESS_TOKEN", "")))

    checked_at = _now_iso()
    edge_row = build_probe_row(source, ping, status, checked_at)
    user_path = classify_user_path(user_raw)
    freshness = classify_freshness(freshness_raw)
    runs_row = build_runs_row(edge_row, user_path, freshness, checked_at,
                              _worst_latency_ms(ping, status, user_raw, freshness_raw))

    write_errors = []
    for label, writer, row in (
        ("latest", upsert_external_probe, edge_row),
        ("history", insert_external_probe_run, runs_row),
    ):
        try:
            writer(row)
        except Exception as exc:  # ledger persistence must not replace the observed verdict
            write_errors.append("%s: %s" % (label, exc))
    return {
        "edge_row": edge_row,
        "runs_row": runs_row,
        "write_errors": write_errors,
        "exit_code": exit_code_for(edge_row["ok"], user_path["ok"],
                                   freshness["freshness_ok"], freshness["market_state"],
                                   freshness.get("availability_ok")),
    }


def main() -> int:
    outcome = run_probe()
    if outcome["write_errors"]:
        sys.stderr.write("[health_probe] probe ledger degraded: %s\n" % "; ".join(outcome["write_errors"]))
    sys.stdout.write(json.dumps({"edge": outcome["edge_row"], "run": outcome["runs_row"]}) + "\n")
    if outcome["exit_code"] != 0:
        sys.stderr.write("[health_probe] UNHEALTHY (arming the workflow-failure email): %s\n"
                         % outcome["runs_row"]["detail"])
    return outcome["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())

"""Pure probe + parsing logic for the standalone health daemon.

stdlib-only by contract (see package docstring). Functions here are
side-effect-free and individually testable; the HTTP/server wiring lives in
serve.py.
"""
from __future__ import annotations

import errno
import json
import math
import socket
import urllib.error
import urllib.request


# Three-valued state vocabulary used everywhere:
#   "up"      — confirmed reachable / running
#   "down"    — confirmed absent (peer refused, unit failed/inactive)
#   "unknown" — could not determine (timeout, unreachable, probe error) — NOT
#               proof of death; a bounded-probe timeout must never read as down
#   "starting"— unit is activating/reloading
def classify_conn_error(exc) -> str:
    """Map a connection failure to 'down' (refused) or 'unknown' (everything
    else, including timeouts — a timeout is not proof the service is dead)."""
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "unknown"
    if isinstance(exc, ConnectionRefusedError):
        return "down"
    if getattr(exc, "errno", None) == errno.ECONNREFUSED:
        return "down"
    return "unknown"


def probe_tcp(host: str, port: int, timeout: float = 1.5) -> dict:
    """Liveness-only TCP connect probe. {state, [detail]}.

    A successful connect proves the port is bound, NOT that the process behind
    it is serving — relay/Next.js are liveness-only by design here.
    """
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return {"state": "up"}
    except OSError as exc:
        return {"state": classify_conn_error(exc), "detail": exc.__class__.__name__}


def probe_http_json(url: str, timeout: float = 2.0, max_bytes: int = 65536) -> dict:
    """GET a JSON endpoint with a bounded timeout.

    2xx -> {state:'up', http_status, payload}; HTTP error -> 'down'; connection
    refused -> 'down'; timeout/unreachable -> 'unknown'.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read(max_bytes)
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                payload = {}
            return {"state": "up", "http_status": getattr(resp, "status", 200), "payload": payload}
    except urllib.error.HTTPError as exc:
        return {"state": "down", "http_status": exc.code, "detail": "http_error"}
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, OSError):
            return {"state": classify_conn_error(reason), "detail": reason.__class__.__name__}
        if "timed out" in str(reason).lower():
            return {"state": "unknown", "detail": "timeout"}
        return {"state": "unknown", "detail": str(reason)[:80]}
    except (socket.timeout, TimeoutError):
        return {"state": "unknown", "detail": "timeout"}
    except OSError as exc:
        return {"state": classify_conn_error(exc), "detail": exc.__class__.__name__}


def unit_coarse_state(active_state: str, sub_state: str) -> str:
    """Collapse systemd ActiveState/SubState into the three-valued vocabulary.

    `active` is "up" regardless of SubState: a docker-wrapper / oneshot unit
    (radon-ib-gateway.service) settles at active+exited, and a normal long-lived
    unit at active+running — both mean the unit succeeded, so neither should read
    as 'unknown'.
    """
    if active_state == "active":
        return "up"
    if active_state == "failed":
        return "down"
    if active_state in ("activating", "reloading"):
        return "starting"
    if active_state in ("inactive", "deactivating"):
        return "down"
    return "unknown"


def parse_unit_states(raw: str) -> dict:
    """Parse `systemctl show <units> -p Id -p ActiveState -p SubState -p Result`.

    systemd separates each unit's property block with a blank line. Returns
    {unit_id: {active_state, sub_state, result, state}}.
    """
    units: dict = {}
    for block in (raw or "").strip().split("\n\n"):
        props: dict = {}
        for line in block.splitlines():
            key, sep, val = line.partition("=")
            if sep:
                props[key.strip()] = val.strip()
        uid = props.get("Id")
        if not uid:
            continue
        active = props.get("ActiveState", "")
        sub = props.get("SubState", "")
        units[uid] = {
            "active_state": active,
            "sub_state": sub,
            "result": props.get("Result", ""),
            "state": unit_coarse_state(active, sub),
        }
    return units


UNIT_STATE_MAX_AGE_SECS = 30.0
STATUS_SCHEMA_VERSION = 2


def _unit_evidence_current(units_age_secs) -> bool:
    return (
        not isinstance(units_age_secs, bool)
        and isinstance(units_age_secs, (int, float))
        and math.isfinite(units_age_secs)
        and 0 <= units_age_secs <= UNIT_STATE_MAX_AGE_SECS
    )


def _nested_api_state(probe_results: dict) -> str | None:
    """Classify the FastAPI payload carried by the HTTP transport probe.

    HTTP 200 proves that FastAPI answered, not that its IB dependency works.
    Older aggregation only inspected the outer transport state, which made an
    ``awaiting_2fa`` / dead-upstream broker session look fully healthy.
    """
    api_probe = (probe_results or {}).get("radon-api")
    if not isinstance(api_probe, dict):
        return None
    payload = api_probe.get("payload")
    if not isinstance(payload, dict):
        return None

    service_state = payload.get("service_state")
    auth_state = payload.get("auth_state")
    upstream_dead = payload.get("upstream_dead")
    port_listening = payload.get("port_listening")

    if upstream_dead is True or port_listening is False:
        return "down"
    if isinstance(service_state, str) and service_state.lower() in {
        "down", "error", "failed", "unhealthy",
    }:
        return "down"
    if isinstance(auth_state, str) and auth_state.lower() in {
        "awaiting_2fa", "down", "error", "failed", "unauthenticated",
    }:
        return "down"
    if upstream_dead is not None and type(upstream_dead) is not bool:
        return "unknown"
    if port_listening is not None and type(port_listening) is not bool:
        return "unknown"
    if isinstance(service_state, str) and service_state.lower() not in {
        # cloud mode reports "reachable" (port probe only; no Docker health).
        "up", "ok", "healthy", "reachable",
    }:
        return "unknown"
    if isinstance(auth_state, str) and auth_state.lower() not in {
        "authenticated", "ok", "healthy",
    }:
        return "unknown"
    return "up"


def aggregate_state(probe_results: dict, units: dict,
                    health_service: str = "ok", units_age_secs=None) -> str:
    """Return the canonical state for this daemon's direct observations.

    The off-box ``external_probe`` row is deliberately excluded: folding an old
    off-box verdict into the endpoint it probes would create a feedback loop.
    """
    # The broker dependency is reported but must not masquerade as an edge
    # outage: IBKR's weekend session shutdown exits the gateway cleanly while
    # every serving-path component stays up, and collapsing the aggregate to
    # "down" made the off-box observer page P1 "edge unhealthy
    # (aggregate_down)" for it (2026-08-09). Gateway-only failure => the NEW
    # "degraded" state; any serving-path failure still wins as "down". Both
    # gateway signals count as dependency: the ib-gateway probe AND the
    # nested FastAPI payload's broker fields (_nested_api_state).
    _DOWNISH = {"down", "error", "failed", "unhealthy"}
    serving_states = []
    dependency_states = []
    for name, value in (probe_results or {}).items():
        if isinstance(value, dict):
            state = str(value.get("state", "unknown")).lower()
            (dependency_states if name == "ib-gateway" else serving_states).append(state)
    for value in (units or {}).values():
        if isinstance(value, dict):
            serving_states.append(str(value.get("state", "unknown")).lower())
    nested_api_state = _nested_api_state(probe_results)
    if nested_api_state is not None:
        dependency_states.append(nested_api_state)
    states = serving_states + dependency_states

    if any(state in _DOWNISH for state in serving_states):
        return "down"
    if (
        health_service != "ok"
        or not states
        or not _unit_evidence_current(units_age_secs)
    ):
        return "unknown"
    if any(state == "unknown" for state in states):
        return "unknown"
    if any(state in _DOWNISH for state in dependency_states):
        return "degraded"
    if any(state == "starting" for state in states):
        return "starting"
    if all(state in {"up", "ok", "healthy"} for state in states):
        return "up"
    return "unknown"


def build_status(probes: dict, units: dict, generated_at: str,
                 health_service: str = "ok", units_age_secs=None,
                 service_health=None, external_probe=None) -> dict:
    """Assemble the always-200 /status body. Degraded sources are fields, never
    error codes (per feedback_http_status_for_real_errors.md).

    `service_health` is the Turso-table section (raw rows + per-row age); a
    Turso outage degrades it to state 'unknown'. `external_probe` is the freshest
    Tier-3 off-box probe row (dict) or None when there is none / no creds. Both
    degrade without touching the response code or the rest of the body.
    """
    overall_state = aggregate_state(
        probes,
        units,
        health_service,
        units_age_secs,
    )
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "ok": overall_state == "up",
        "overall_state": overall_state,
        "health_service": health_service,
        "generated_at": generated_at,
        "probes": probes,
        "units": units,
        "units_age_secs": units_age_secs,
        "service_health": service_health
        if service_health is not None
        else {"state": "unknown", "detail": "not_collected", "rows": []},
        "external_probe": external_probe,
    }

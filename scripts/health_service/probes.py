"""Pure probe + parsing logic for the standalone health daemon.

stdlib-only by contract (see package docstring). Functions here are
side-effect-free and individually testable; the HTTP/server wiring lives in
serve.py.
"""
from __future__ import annotations

import errno
import json
from datetime import datetime
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
                payload = json.loads(raw.decode("utf-8")) if raw else None
            except (UnicodeDecodeError, ValueError):
                payload = None
            if not isinstance(payload, dict) or not payload:
                return {
                    "state": "unknown",
                    "http_status": getattr(resp, "status", 200),
                    "detail": "invalid_json_payload",
                }
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
        # `activating` + `auto-restart` is systemd's signature for a crash loop:
        # the unit is not on its way up, it has already failed and is being
        # respawned. A unit with a bad config spends 100% of its life there and
        # never reaches `active`, so reading it as "starting" reported a
        # permanently dead service as a transient. R-397.
        if sub_state == "auto-restart":
            return "down"
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
# Probe evidence gets the same treatment as unit evidence. The refresh interval
# is 5s, so 30s is six missed sweeps. R-401.
PROBE_STATE_MAX_AGE_SECS = 30.0
STATUS_SCHEMA_VERSION = 2

# Non-edge components: reported in /status, but a failure here must not
# collapse the public edge aggregate to "down". Off-box pages P1 on
# aggregate_down; these units already have their own on-box alarms.
# ib-gateway: broker dependency (2026-08-09 weekend clean-exit false P1).
# newsfeed / monitor: sidecars — Restart=always flaps briefly read as unit
# "down" or "starting" and were paging edge-unhealthy (2026-08-29 pages
# 0b7726f8 / 344f0592).
# How long a dependency may sit non-`up` before its failure stops being a flap.
# `degraded` converts off-box to an explicit non-page, so with no dwell bound a
# permanently dead radon-monitor (the fill / order / journal daemon) was edge
# green forever. 15 minutes absorbs every Restart=always flap the 2026-08-29
# pages were about and still catches a death well inside one trading session.
# R-382.
DEPENDENCY_DWELL_LIMIT_SECS = 900.0

DEPENDENCY_PROBES = frozenset({"ib-gateway"})
DEPENDENCY_UNITS = frozenset({
    "radon-ib-gateway.service",
    "radon-newsfeed.service",
    "radon-monitor.service",
})
# R-382 dwell escalates a sidecar that stays non-up past the bound
# (newsfeed / monitor Restart=always deaths). The broker is special but NOT
# exempt (REL-181 / R-478, NF-10): IBKR session shutdown leaves
# radon-ib-gateway inactive/dead Result=success for 40+ hours off-session
# (weekends, and nightly outside the 04:00-20:00 ET equity EXT window) — the
# unconditional exclusion that replaced the dwell recreated permanent
# blindness the other way (a gateway dead Tuesday 10:00 ET could never
# escalate the edge floor). The suppression is now a predicate:
# `Result=success` AND the market is closed. Anything else takes the 900s
# dwell. Holidays are NOT calendared here (stdlib isolation contract: no
# repo imports), so a clean exit on a holiday Monday escalates and pages —
# fail toward paging; on-box ib-gateway-grouped still dedupes.
DWELL_ESCALATE_UNITS = DEPENDENCY_UNITS
GATEWAY_UNIT = "radon-ib-gateway.service"


def _now_et(now_et=None):
    if now_et is not None:
        return now_et
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        from datetime import timedelta, timezone
        return datetime.now(timezone.utc) + timedelta(hours=-5)


def _market_closed_et(dt) -> bool:
    """Weekend, or a weekday outside the 04:00-20:00 ET equity EXT session."""
    if dt.weekday() >= 5:
        return True
    minutes = dt.hour * 60 + dt.minute
    return not (4 * 60 <= minutes < 20 * 60)


def _gateway_dwell_suppressed(value: dict, now_et) -> bool:
    return (
        str(value.get("result", "")).lower() == "success"
        and _market_closed_et(_now_et(now_et))
    )


def _evidence_current(age_secs, bound: float) -> bool:
    return (
        not isinstance(age_secs, bool)
        and isinstance(age_secs, (int, float))
        and math.isfinite(age_secs)
        and 0 <= age_secs <= bound
    )


def _unit_evidence_current(units_age_secs) -> bool:
    return _evidence_current(units_age_secs, UNIT_STATE_MAX_AGE_SECS)


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

    if not (
        isinstance(service_state, str)
        and isinstance(auth_state, str)
        and type(upstream_dead) is bool
        and type(port_listening) is bool
    ):
        return "unknown"

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
                    health_service: str = "ok", units_age_secs=None,
                    probes_age_secs=None, now_et=None) -> str:
    """Return the canonical state for this daemon's direct observations.

    The off-box ``external_probe`` row is deliberately excluded: folding an old
    off-box verdict into the endpoint it probes would create a feedback loop.
    """
    # Non-edge dependencies/sidecars are reported but must not masquerade as
    # an edge outage. Collapsing the aggregate to "down" made the off-box
    # observer page P1 "edge unhealthy (aggregate_down)" for broker-only
    # (2026-08-09) and newsfeed-flap (2026-08-29) failures while api/relay/
    # nextjs stayed up. Dependency-only failure => "degraded"; any serving-
    # path failure still wins as "down". Nested FastAPI broker fields
    # (_nested_api_state) count as dependency too.
    _DOWNISH = {"down", "error", "failed", "unhealthy"}
    serving_states = []
    dependency_states = []
    for name, value in (probe_results or {}).items():
        if isinstance(value, dict):
            state = str(value.get("state", "unknown")).lower()
            target = dependency_states if name in DEPENDENCY_PROBES else serving_states
            target.append(state)
    # Probe evidence is age-gated exactly like unit evidence: `ProbeCache`
    # keeps its last value through every failure, so an hours-old dict would
    # otherwise report `radon-api: up` the moment the unit reads `active`.
    # `None` means the caller has no age to offer (a bare `run_probes`), which
    # is current by construction. R-401.
    probes_current = probes_age_secs is None or _evidence_current(
        probes_age_secs, PROBE_STATE_MAX_AGE_SECS
    )
    units_current = _unit_evidence_current(units_age_secs)
    dependency_stuck = False
    if units_current:
        for name, value in (units or {}).items():
            if isinstance(value, dict):
                state = str(value.get("state", "unknown")).lower()
                is_dependency = name in DEPENDENCY_UNITS
                target = dependency_states if is_dependency else serving_states
                target.append(state)
                # `non_up_secs` is stamped by UnitStateCache: how long this unit
                # has been continuously not-`up`. None means the cache has no
                # dwell evidence, which must never invent an escalation.
                if name in DWELL_ESCALATE_UNITS and state != "up":
                    dwell = value.get("non_up_secs")
                    if isinstance(dwell, (int, float)) and dwell >= DEPENDENCY_DWELL_LIMIT_SECS:
                        if name == GATEWAY_UNIT and _gateway_dwell_suppressed(value, now_et):
                            pass  # weekend/overnight clean exit: REL-181 predicate
                        else:
                            dependency_stuck = True
    nested_api_state = _nested_api_state(probe_results)
    if nested_api_state is not None:
        dependency_states.append(nested_api_state)
    states = serving_states + dependency_states

    if any(state in _DOWNISH for state in serving_states):
        return "down"
    if (
        health_service != "ok"
        or not states
        or not units_current
        or not probes_current
    ):
        return "unknown"
    if any(state == "unknown" for state in states):
        return "unknown"
    if dependency_stuck:
        # Past the dwell bound this is not a flap. Suppressing it forever meant
        # a dead fill/order/journal daemon read as edge-green. R-382.
        return "down"
    # The serving-path verdict is decided BEFORE the dependency suppression: a
    # failed sidecar must never make a serving-path signal invisible. Ordered
    # the other way, `systemctl reload radon-nextjs` wedging in ExecReload was
    # silent whenever radon-newsfeed happened to be failed at the same moment,
    # and pageable when it was not. R-398.
    if any(state == "starting" for state in serving_states):
        return "starting"
    if any(state in _DOWNISH for state in dependency_states):
        return "degraded"
    if any(state == "starting" for state in dependency_states):
        # Sidecar Restart=always spends the flap in activating. Collapsing
        # the aggregate to "starting" made the off-box probe write
        # aggregate_down (page 344f0592) while api/relay/nextjs stayed up.
        return "degraded"
    if all(state in {"up", "ok", "healthy"} for state in states):
        return "up"
    return "unknown"


def degraded_reasons(probe_results: dict, units: dict) -> list:
    """Names of the non-up dependencies behind a degraded aggregate (R-510).

    Always computed; empty when everything dependency-side is up. The off-box
    edge probe folds this into its verdict detail so "gateway down
    (suppressed)", "newsfeed flap" and "2FA lock" stop being the same word.
    """
    _NON_UP = {"down", "error", "failed", "unhealthy", "starting", "unknown"}
    reasons = []
    for name, value in (probe_results or {}).items():
        if isinstance(value, dict) and name in DEPENDENCY_PROBES:
            if str(value.get("state", "unknown")).lower() in _NON_UP:
                reasons.append(name)
    for name, value in (units or {}).items():
        if isinstance(value, dict) and name in DEPENDENCY_UNITS:
            if str(value.get("state", "unknown")).lower() != "up":
                reasons.append(name)
    if _nested_api_state(probe_results) == "down":
        reasons.append("radon-api:broker")
    return sorted(set(reasons))


def build_status(probes: dict, units: dict, generated_at: str,
                 health_service: str = "ok", units_age_secs=None,
                 service_health=None, external_probe=None,
                 probes_age_secs=None, now_et=None) -> dict:
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
        probes_age_secs,
        now_et=now_et,
    )
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "ok": overall_state == "up",
        "overall_state": overall_state,
        "degraded_reasons": degraded_reasons(probes, units),
        "health_service": health_service,
        "generated_at": generated_at,
        "probes": probes,
        "units": units,
        "units_age_secs": units_age_secs,
        "probes_age_secs": probes_age_secs,
        "service_health": service_health
        if service_health is not None
        else {"state": "unknown", "detail": "not_collected", "rows": []},
        "external_probe": external_probe,
    }

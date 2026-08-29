"""The edge-paging downgrade for sidecars is only safe if on-box still pages.

T-274. Two commits this week moved `radon-monitor.service` and
`radon-newsfeed.service` onto `probes.DEPENDENCY_UNITS`
(`scripts/health_service/probes.py:139-144`) and mapped a dependency-only
`starting` to `degraded` (`:257-261`), so a dead or flapping sidecar no
longer collapses the public edge aggregate to `down`. `external_probe`
grants the off-box P1 emergency only to a validated DOWN verdict
(`scripts/watchdog/external_probe.py:262-269`), so after those commits
NOTHING off-box pages for these units.

The justification is a code comment at `probes.py:133-137` — "these units
already have their own on-box alarms". `radon-monitor` is the fill / order /
journal daemon, a money path, so that claim is load-bearing and it was
asserted nowhere: the health tests pin only the DOWNGRADE, and the watchdog
tests exercise only `active="failed"` on unrelated units.

This file owns the COMPOSITION, and is driven off `probes.DEPENDENCY_UNITS`
itself so the frozenset cannot grow without on-box cover coming with it.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from health_service import probes
from watchdog import units

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)

# Equality-pinned: a new member must be added here deliberately, and doing so
# immediately subjects it to the on-box paging assertions below.
PINNED_DEPENDENCY_UNITS = frozenset({
    "radon-ib-gateway.service",
    "radon-newsfeed.service",
    "radon-monitor.service",
})

DEPENDENCY_UNITS = sorted(probes.DEPENDENCY_UNITS)

_HEALTHY_PROBES = {
    "radon-api": {
        "state": "up",
        "payload": {
            "service_state": "reachable",
            "auth_state": "authenticated",
            "upstream_dead": False,
            "port_listening": True,
        },
    },
    "radon-relay": {"state": "up"},
    "radon-nextjs": {"state": "up"},
    "ib-gateway": {"state": "up"},
}

_HEALTHY_UNITS = {
    "radon-api.service": {"state": "up"},
    "radon-relay.service": {"state": "up"},
    "radon-nextjs.service": {"state": "up"},
    "radon-monitor.service": {"state": "up"},
    "radon-newsfeed.service": {"state": "up"},
    "radon-ib-gateway.service": {"state": "up"},
}


def _block(unit_id, active, sub, result="exit-code", nrestarts=3):
    return "\n".join([
        f"Result={result}",
        f"NRestarts={nrestarts}",
        f"Id={unit_id}",
        f"ActiveState={active}",
        f"SubState={sub}",
    ]) + "\n"


def test_the_dependency_unit_set_is_the_pinned_one():
    """Growth here is a paging change. It must be deliberate, and it must
    bring the on-box assertions below with it."""
    assert probes.DEPENDENCY_UNITS == PINNED_DEPENDENCY_UNITS


# ── off-box half: the edge must NOT collapse ─────────────────────────

@pytest.mark.parametrize("unit", DEPENDENCY_UNITS)
@pytest.mark.parametrize("sidecar_state", ["down", "starting"])
def test_a_dependency_unit_alone_degrades_the_edge_rather_than_downing_it(
    unit, sidecar_state
):
    state = probes.aggregate_state(
        _HEALTHY_PROBES,
        {**_HEALTHY_UNITS, unit: {"state": sidecar_state}},
        units_age_secs=0,
    )
    assert state == "degraded"


# ── on-box half: the alarm the downgrade relies on ───────────────────

@pytest.mark.parametrize("unit", DEPENDENCY_UNITS)
def test_the_same_unit_still_pages_p1_on_box_when_failed(unit):
    current = units.parse_show_output(_block(unit, active="failed", sub="failed"))
    outcomes = units.evaluate(current=current, previous={}, now=NOW)
    assert [(o.service, o.severity, o.fired) for o in outcomes] == [(unit, "P1", True)]


@pytest.mark.parametrize("unit", DEPENDENCY_UNITS)
def test_the_same_unit_still_pages_p1_on_box_when_flapping(unit):
    """Restart=always spends the flap in auto-restart, never in failed.

    This is the exact state that produced the edge pages the downgrade was
    added to stop, so it is the state the on-box alarm most has to cover.
    """
    current = units.parse_show_output(
        _block(unit, active="activating", sub="auto-restart", result="signal")
    )
    previous = {unit: {"auto_restart": True, "nrestarts": 2, "active_state": "activating"}}
    outcomes = units.evaluate(current=current, previous=previous, now=NOW)
    fired = [(o.service, o.severity) for o in outcomes if o.fired]
    assert fired == [(unit, "P1")]


@pytest.mark.parametrize("unit", DEPENDENCY_UNITS)
def test_the_start_limit_parked_case_pages_and_says_it_will_not_self_heal(unit):
    """A sidecar that exhausts Restart=always is parked by systemd forever.
    Off-box is degraded-only for these units, so the on-box message is the
    single channel that tells the operator it needs a hand."""
    current = units.parse_show_output(
        _block(unit, active="failed", sub="failed", result="start-limit-hit", nrestarts=5)
    )
    outcomes = units.evaluate(current=current, previous={}, now=NOW)
    assert len(outcomes) == 1
    assert outcomes[0].severity == "P1"
    assert outcomes[0].fired is True
    assert "auto-recover" in outcomes[0].message.lower()

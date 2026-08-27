"""Every timer-backed health writer must be in BOTH catalogs.

R-236: `breadth-scan` is a SCHEDULED writer with zero staleness alarm on
either side. `radon-breadth.timer` fires every five minutes across ET trading
hours into `run_breadth_scan.sh` → `breadth_scan.py`'s
`mirror_scan_snapshot("breadth-scan", ...)`, which writes the heartbeat row
(pinned by `test_breadth_scan.py`). But `serviceHealthWindows.ts` records it
as `category: "on-demand"` with the comment "writes when a user POSTs
/breadth/scan", so the route coerces a past-window row into the informational
`dormant` state rather than `stale`; and `SCHEDULED_SERVICES` has no
`breadth-scan` key at all, so it lands in no `BUCKETS` list and `check.py` —
which iterates `BUCKETS[bucket]` and nothing else — never evaluates it. Disable
the timer and breadth data freezes silently. `units.py` is not a backstop: it
fires only on `ActiveState=failed`, which a soft or no-op exit does not
produce.

NF-8: the standing sweep compared only the DELTA's own jobs against the two
catalogs, which is why a five-minute RTH timer whose job has written this
heartbeat since before the audit anchor was never checked by it. The parity
test below enumerates every service name reachable from a `cloud/services/*.timer`
instead, so the sweep is a test rather than a procedure.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from watchdog import services as services_mod  # noqa: E402
from watchdog import units as units_mod  # noqa: E402

REPO = _SCRIPTS_DIR.parent
SERVICES_DIR = REPO / "cloud" / "services"
WEB_CATALOG = REPO / "web" / "lib" / "serviceHealthWindows.ts"

# Health-row names written by a timer-backed job but deliberately NOT
# staleness-checked. Each needs a stated reason, so the exemption is a
# decision rather than an oversight.
EXEMPT: dict[str, str] = {}


def _timer_backed_services() -> set[str]:
    """Service names whose unit is driven by a `cloud/services/*.timer`."""
    names: set[str] = set()
    for timer in SERVICES_DIR.glob("*.timer"):
        unit = timer.with_suffix(".service")
        if unit.exists():
            names.add(unit.stem)
    return names


# `record_service_health(name, state)` — a literal in the FIRST position is a
# service name; these are states, and appear first only in helper wrappers.
_STATE_LITERALS = frozenset({"ok", "error", "warn", "degraded", "paused", "syncing"})


def _names_in(text: str) -> set[str]:
    found = set(re.findall(r'mirror_scan_snapshot\(\s*"([a-z0-9-]+)"', text))
    found |= set(re.findall(r'record_service_health\(\s*"([a-z0-9-]+)"', text))
    return found - _STATE_LITERALS


def _resolve(rel: str) -> Path | None:
    for candidate in (
        REPO / rel.lstrip("/").replace("home/radon/radon/", ""),
        _SCRIPTS_DIR / Path(rel).name,
    ):
        if candidate.exists():
            return candidate
    return None


def _health_names_written_by(unit_stem: str) -> set[str]:
    """Health-row names the unit's ExecStart chain writes.

    A `.sh` ExecStart is a wrapper, so the python module it invokes is the
    real job and is followed one level. A `.py` ExecStart IS the job — do not
    follow further, or every module it merely mentions gets attributed to this
    unit.
    """
    unit = (SERVICES_DIR / f"{unit_stem}.service").read_text(encoding="utf-8")
    exec_start = "\n".join(
        line for line in unit.splitlines() if line.startswith("ExecStart")
    )
    names: set[str] = set()
    for rel in re.findall(r"([A-Za-z0-9_./-]+\.(?:py|sh))", exec_start):
        path = _resolve(rel)
        if path is None:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        names |= _names_in(text)
        if path.suffix == ".sh":
            for inner in re.findall(r"(scripts/[A-Za-z0-9_./-]+\.py)", text):
                inner_path = _resolve(inner)
                if inner_path is not None:
                    names |= _names_in(inner_path.read_text(encoding="utf-8", errors="replace"))
    return names


def _timer_backed_health_names() -> dict[str, str]:
    """health name -> the timer-backed unit that writes it."""
    out: dict[str, str] = {}
    for unit_stem in sorted(_timer_backed_services()):
        for name in _health_names_written_by(unit_stem):
            out.setdefault(name, unit_stem)
    return out


def _web_category(name: str) -> str | None:
    text = WEB_CATALOG.read_text(encoding="utf-8")
    match = re.search(rf'"{re.escape(name)}":\s*\{{[^}}]*category:\s*"([a-z-]+)"', text)
    return match.group(1) if match else None


class TestBreadthScanIsRegistered:
    def test_breadth_scan_is_in_the_watchdog_catalog(self):
        assert "breadth-scan" in services_mod.SCHEDULED_SERVICES, (
            "a five-minute RTH timer's writer is in no BUCKETS list, so "
            "check.py never evaluates it"
        )

    def test_breadth_scan_is_in_an_evaluated_bucket(self):
        buckets = {
            name
            for members in services_mod.BUCKETS.values()
            for name in members
        }
        assert "breadth-scan" in buckets

    def test_the_web_catalog_calls_it_scheduled(self):
        assert _web_category("breadth-scan") == "scheduled", (
            "on-demand coerces a past-window row into the informational "
            "`dormant` state instead of the degraded `stale` one"
        )


class TestCatalogParity:
    """NF-8: the standing sweep, as a test."""

    def test_the_enumeration_finds_something(self):
        found = _timer_backed_health_names()
        assert found, "the timer/writer enumeration matched nothing — it broke"
        assert "breadth-scan" in found, sorted(found)

    def test_every_timer_backed_writer_is_in_both_catalogs(self):
        missing: dict[str, list[str]] = {}
        for name, unit in sorted(_timer_backed_health_names().items()):
            if name in EXEMPT:
                continue
            gaps = []
            if name not in services_mod.SCHEDULED_SERVICES:
                gaps.append("watchdog SCHEDULED_SERVICES")
            if _web_category(name) is None:
                gaps.append("web serviceHealthWindows.ts")
            elif _web_category(name) == "on-demand":
                gaps.append("web category is on-demand despite a timer")
            if gaps:
                missing[f"{name} ({unit})"] = gaps
        assert not missing, (
            "timer-backed health writers with no staleness alarm:\n  "
            + "\n  ".join(f"{k}: {v}" for k, v in missing.items())
        )


class TestDur02BrakeAndFlapReach:
    """R-268: `flap` cannot fire for any DUR-02-braked unit.

    `_flap_alert` needs `SubState == "auto-restart"` in TWO consecutive
    five-minute cycles, but that substate is occupied only for `RestartSec` per
    restart — 5 s for radon-api and radon-nextjs, 10 s for radon-monitor, 2 s
    for radon-health — and with `StartLimitIntervalSec=300` /
    `StartLimitBurst=5` systemd parks a crash-looping unit as `failed` about
    25 seconds in. Two five-minute samples cannot both land inside a
    five-second window. This is not an outage gap: `Result=start-limit-hit`
    does page P1.

    T-208: the two tests here used to assert the brake arithmetic as an
    invariant (`to_park < cycle_seconds`) and the module docstring as prose,
    which blessed the dead path — a genuine fix (shorter cycle, or keying on
    `NRestarts`) went red — while `continue`-ing past any unit missing the
    directives, so deleting `StartLimitBurst` passed. What actually has to
    hold is that the brake IS declared, and that the braked crash loop pages
    P1 through the `start-limit-hit` branch. Both are asserted below.
    """

    UNITS_PATH = _SCRIPTS_DIR / "watchdog" / "units.py"
    BRAKED_UNITS = ("radon-api", "radon-nextjs", "radon-monitor", "radon-health")

    def test_the_braked_units_declare_the_dur_02_brake(self):
        """No silent skip: a unit that drops a directive must fail, not pass.

        `flap` is documented as unreachable for these units precisely BECAUSE
        the brake parks them first, so the brake going missing is the change
        that would invalidate the reasoning — it must not pass unnoticed.
        """
        required = ("RestartSec", "StartLimitIntervalSec", "StartLimitBurst")
        gaps: dict[str, list[str]] = {}
        for unit_name in self.BRAKED_UNITS:
            unit = SERVICES_DIR / f"{unit_name}.service"
            if not unit.exists():
                gaps[unit_name] = ["unit file is missing"]
                continue
            text = unit.read_text(encoding="utf-8")
            absent = [
                directive for directive in required
                if not re.search(rf"^{directive}=(\d+)", text, re.M)
            ]
            if absent:
                gaps[unit_name] = absent
        assert not gaps, (
            "DUR-02 brake directives missing — a crash-looping unit restarts "
            f"forever instead of parking as start-limit-hit: {gaps}"
        )

    def test_the_braked_crash_loop_pages_p1_through_start_limit_hit(self):
        """The covering alarm for the braked shape, asserted as behaviour.

        Sampled at the five-minute cadence, a braked crash loop is already
        parked `failed` — so the P1 must come from the `failed` branch, and
        `_flap_alert` must NOT be what carries it.
        """
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        parked = {
            "Id": "radon-api.service",
            "ActiveState": "failed",
            "SubState": "failed",
            "Result": "start-limit-hit",
            "NRestarts": 5,
            "Type": "simple",
        }
        previous = {"radon-api.service": {"nrestarts": 5, "auto_restart": False}}

        outcomes = units_mod.evaluate(current=[parked], previous=previous, now=now)
        assert [o.severity for o in outcomes] == ["P1"], outcomes
        assert "start-limit-hit" in outcomes[0].message
        assert "reset-failed" in outcomes[0].message, (
            "a parked unit never auto-recovers, so the page must name the "
            "manual action"
        )
        assert units_mod._flap_alert(parked, previous, now) is None, (
            "flap is not the signal that covers a braked unit — the parked "
            "unit is no longer in auto-restart"
        )

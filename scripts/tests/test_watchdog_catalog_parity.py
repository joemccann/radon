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
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from watchdog import services as services_mod  # noqa: E402

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


# Most jobs pass a module-level constant rather than a literal
# (`record_service_health(SERVICE, ...)`), which a literal-only scan cannot
# see. Resolve the constant from its own assignment in the same file.
_SERVICE_CONST = re.compile(
    r'^(SERVICE|SERVICE_NAME|HEALTH_SERVICE)\s*=\s*"([a-z0-9-]+)"', re.MULTILINE
)


def _names_in(text: str) -> set[str]:
    consts = {name: value for name, value in _SERVICE_CONST.findall(text)}
    found = set(re.findall(r'mirror_scan_snapshot\(\s*"([a-z0-9-]+)"', text))
    found |= set(re.findall(r'record_service_health\(\s*"([a-z0-9-]+)"', text))
    for call in ("mirror_scan_snapshot", "record_service_health"):
        for ident in re.findall(rf"{call}\(\s*([A-Z_]+)\b", text):
            if ident in consts:
                found.add(consts[ident])
    return found - _STATE_LITERALS


def _resolve(rel: str) -> Path | None:
    stripped = rel.lstrip("/").replace("home/radon/radon/", "")
    for candidate in (
        REPO / stripped,
        _SCRIPTS_DIR / Path(rel).name,
        # `-I /usr/local/lib/radon/<x>.py` is an INSTALLED copy; its source of
        # truth is the cloud tree.
        REPO / "cloud" / "scripts" / Path(rel).name,
    ):
        if candidate.exists():
            return candidate
    # `-m scripts.watchdog` is a PACKAGE, not a module: `_exec_targets` maps it
    # to `scripts/watchdog.py`, which does not exist. Its entry point is
    # `scripts/watchdog/__main__.py`. Four of the fleet's units are packages.
    if stripped.endswith(".py"):
        pkg_main = REPO / stripped[: -len(".py")] / "__main__.py"
        if pkg_main.exists():
            return pkg_main
    return None


def _exec_targets(exec_start: str) -> set[str]:
    """Every source file an ExecStart line actually runs.

    A `\\.(py|sh)` scan alone sees neither of the two forms this fleet uses
    most: `python -m scripts.data_refresh` names no file at all, and a `node`
    ExecStart names a `.js`. Both returned an EMPTY set, so the parity check
    below asserted precisely nothing about those units — `radon-refresh`,
    `radon-incident-watchdog` and `radon-demo-mirror` were invisible to it
    rather than exempt from it. R-277.
    """
    targets = set(re.findall(r"([A-Za-z0-9_./-]+\.(?:py|sh|js))", exec_start))
    # `-m package.module` -> `package/module.py`
    for dotted in re.findall(r"-m\s+([A-Za-z0-9_.]+)", exec_start):
        targets.add(dotted.replace(".", "/") + ".py")
    return targets


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
    for rel in _exec_targets(exec_start):
        path = _resolve(rel)
        if path is None:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        names |= _names_in(text)
        if path.suffix == ".sh":
            for inner in re.findall(r"(scripts/[A-Za-z0-9_./-]+\.(?:py|js))", text):
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


class TestFlapBucketIsNotAdvertisedBeyondItsReach:
    """R-268: `flap` cannot fire for any DUR-02-braked unit.

    `_flap_alert` needs `SubState == "auto-restart"` in TWO consecutive
    five-minute cycles, but that substate is occupied only for `RestartSec` per
    restart — 5 s for radon-api and radon-nextjs, 10 s for radon-monitor, 2 s
    for radon-health — and with `StartLimitIntervalSec=300` /
    `StartLimitBurst=5` systemd parks a crash-looping unit as `failed` about
    25 seconds in. Two five-minute samples cannot both land inside a
    five-second window. This is not an outage gap: `Result=start-limit-hit`
    does page P1. It is a dead bucket that makes the module read as having two
    independent P1 signals when it has one.
    """

    UNITS_PATH = _SCRIPTS_DIR / "watchdog" / "units.py"

    def test_the_docstring_states_the_limitation(self):
        text = self.UNITS_PATH.read_text(encoding="utf-8")
        header = text[: text.index('"""', text.index('"""') + 3)]
        flap = header[header.index("``flap``"):]
        flap = flap[: flap.index("``delta``")]
        assert "Unreachable" in flap and "start-limit-hit" in flap, (
            "the module advertises `flap` as the sustained-crash-loop signal "
            "without noting that a braked unit reaches `failed` first, so it "
            "reads as having two independent P1 signals when it has one"
        )

    def test_braked_units_reach_the_start_limit_before_two_cycles(self):
        cycle_seconds = 5 * 60
        for unit_name in ("radon-api", "radon-nextjs", "radon-monitor", "radon-health"):
            unit = SERVICES_DIR / f"{unit_name}.service"
            if not unit.exists():
                continue
            text = unit.read_text(encoding="utf-8")
            restart_sec = re.search(r"^RestartSec=(\d+)", text, re.M)
            burst = re.search(r"^StartLimitBurst=(\d+)", text, re.M)
            if not (restart_sec and burst):
                continue
            # Time systemd spends looping before parking the unit `failed`.
            to_park = int(restart_sec.group(1)) * int(burst.group(1))
            assert to_park < cycle_seconds, (
                f"{unit_name} parks in {to_park}s, well inside one "
                f"{cycle_seconds}s watchdog cycle — `flap` needs two "
                "consecutive samples inside a RestartSec window and can never "
                "get them"
            )


# ── R-277: every timer-backed unit is accounted for ──────────────────────────
#
# The parity check below only constrains health names it can FIND. A unit whose
# ExecStart could not be parsed, or whose job writes no row at all, was not
# exempt from the invariant — it was invisible to it, which reads identically
# and is what let `radon-refresh`, `radon-incident-watchdog` and
# `radon-demo-mirror` sit outside both catalogs unnoticed.
#
# These two maps make the gap ENUMERATED instead of invisible. Every entry is a
# real gap or a real exemption; a NEW unit in neither fails the test, so the
# class cannot grow silently while it is worked down.

# Units whose job DOES call record_service_health, under a name this static
# parser cannot resolve (built at runtime, or passed in by a caller). Not a
# reliability gap — a parser limitation, tracked so it is not mistaken for one.
HEALTH_NAME_UNRESOLVED = {
    "radon-ib-watchdog",
    "radon-nextjs-db-watchdog",
    "radon-perf-twr",
    "radon-watchdog-continuous",
    "radon-watchdog-daily",
    "radon-watchdog-error",
    "radon-watchdog-intraday",
}

# Units that write NO service_health row at all. Each is a real observability
# gap: the unit can fail on every fire and nothing in either catalog notices.
# Listed rather than fixed here because each needs its own writer and its own
# freshness window; carried as the standing remainder of R-277.
NO_HEALTH_WRITER = {
    "radon-bpi",
    "radon-cta-sync",
    "radon-db-backup",
    "radon-db-retention",
    "radon-demo-mirror",
    "radon-drift-audit",
    "radon-forecast-nightly",
    "radon-grok-page-responder",
    "radon-host-metrics",
    "radon-incident-watchdog",
    "radon-knowledge",
    "radon-llm-index",
    "radon-media-backup",
    "radon-portfolio-archive",
    "radon-portfolio-sync",
    "radon-refresh",
    "radon-signals-refresh",
}


class TestEveryTimerBackedUnitIsAccountedFor:
    def test_no_unit_is_silently_outside_the_invariant(self):
        unaccounted = sorted(
            unit
            for unit in _timer_backed_services()
            if not _health_names_written_by(unit)
            and unit not in HEALTH_NAME_UNRESOLVED
            and unit not in NO_HEALTH_WRITER
        )
        assert not unaccounted, (
            "these timer-backed units contribute no catalogued health name and "
            "are in neither exempt list, so nothing notices when they fail on "
            f"every fire: {unaccounted}"
        )

    def test_the_exempt_lists_name_only_real_units(self):
        """A stale entry would silently re-open the hole it was covering."""
        units = _timer_backed_services()
        stale = sorted((HEALTH_NAME_UNRESOLVED | NO_HEALTH_WRITER) - set(units))
        assert not stale, f"exempt entries for units that no longer exist: {stale}"

    def test_every_exempt_unit_still_lacks_a_resolvable_name(self):
        """When a unit starts contributing a name, drop it from the list."""
        fixed = sorted(
            unit
            for unit in (HEALTH_NAME_UNRESOLVED | NO_HEALTH_WRITER)
            if _health_names_written_by(unit)
        )
        assert not fixed, (
            f"these units now contribute a health name; remove them: {fixed}"
        )

    def test_every_exec_start_resolves_to_a_real_file(self):
        """An unresolvable ExecStart makes the parity check assert nothing.

        `-m scripts.watchdog` names a PACKAGE, a `node` ExecStart names a
        `.js`, and the drift auditor runs an installed copy under
        /usr/local/lib. All three resolved to nothing before R-277.
        """
        unresolved = {}
        for unit_stem in sorted(_timer_backed_services()):
            unit = (SERVICES_DIR / f"{unit_stem}.service").read_text(encoding="utf-8")
            exec_start = "\n".join(
                line for line in unit.splitlines() if line.startswith("ExecStart")
            )
            targets = _exec_targets(exec_start)
            if targets and not any(_resolve(t) for t in targets):
                unresolved[unit_stem] = sorted(targets)
        assert not unresolved, (
            f"ExecStart targets that resolve to no file in-tree: {unresolved}"
        )

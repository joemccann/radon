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


# Most jobs pass a module-level constant rather than a literal
# (`record_service_health(SERVICE, ...)`), which a literal-only scan cannot
# see.
#
# R-325: matching only the three names `SERVICE`/`SERVICE_NAME`/
# `HEALTH_SERVICE` left `perf_twr_builder.py`'s `_PERF_TWR_SERVICE` — and every
# other privately-named constant — unresolvable, and the unit therefore looked
# like it wrote nothing. Resolve ANY module-level `NAME = "literal"` binding
# instead; the call-site match still decides which of them is a service name,
# so widening the assignment pattern cannot invent one.
_SERVICE_CONST = re.compile(
    r'^(?:const |let |var )?(_?[A-Z][A-Z0-9_]*)\s*=\s*"([a-z0-9-]+)"', re.MULTILINE
)

# R-325: `service_cycle(SERVICE, market_hours_class=...)` is the fleet's third
# health writer — it opens and closes a `service_health` row around the job —
# and the scan did not know the call existed, so every job that heartbeats
# through it (bpi_scan, and the wrappers that import it) resolved to an empty
# name set and read as "writes no row".
_HEALTH_CALLS = (
    "mirror_scan_snapshot",
    "record_service_health",
    "service_cycle",
    # The node writers spell it camelCase (`scripts/db/writer.js`).
    "recordServiceHealth",
    # host_metrics_sampler.py / grok_page_responder.py call the bounded Hrana
    # writer directly, passing the name as a module-level constant. R-412.
    "write_service_health_http",
)

# The bounded-stdlib jobs (db_backup, db_retention_sweep, drift_audit,
# media_backup, archive_portfolio_snapshots, disk_cleanup) each define their OWN
# `write_service_health(state, detail, started_at)` and take the service name
# from a module-level constant, so the name never appears as a call argument.
# The scan could not see the shape at all, and eight units were consequently
# labelled `gap: writes no service_health row` when they heartbeat on every
# fire. R-412.
_LOCAL_HEALTH_WRITER = re.compile(r"^def write_service_health\(", re.MULTILINE)


def _names_in(text: str) -> set[str]:
    consts = {name: value for name, value in _SERVICE_CONST.findall(text)}
    found: set[str] = set()
    for call in _HEALTH_CALLS:
        found |= set(re.findall(rf'{call}\(\s*"([a-z0-9-]+)"', text))
        for ident in re.findall(rf"{call}\(\s*(_?[A-Z][A-Z0-9_]*)\b", text):
            if ident in consts:
                found.add(consts[ident])
    if _LOCAL_HEALTH_WRITER.search(text) and "SERVICE_NAME" in consts:
        found.add(consts["SERVICE_NAME"])
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
            # Comments FIRST. `run_flow_refresh.sh` mentions
            # `scripts/api/server.py` in a comment about a shed marker, which
            # attributed every name that file writes to the flow-refresh timer.
            # R-412.
            code = "\n".join(
                line for line in text.splitlines() if not line.lstrip().startswith("#")
            )
            for inner in re.findall(r"(scripts/[A-Za-z0-9_./-]+\.(?:py|js))", code):
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

# R-325: these were two bare `set`s, so an entry recorded WHICH bucket a unit
# was in but never WHY, and the two buckets were the only distinction. A unit
# added to either was indistinguishable from a decision, which is exactly the
# shape NF-8 keeps re-opening in. Every entry now carries its own reason, and
# `test_every_exempt_unit_states_a_reason` fails on an empty one.
#
# Two kinds of entry, both named in the reason text:
#   "parser:" — the job DOES heartbeat, under a name this static scan cannot
#               resolve (built at runtime, or written through the cloud tree's
#               own bounded stdlib libSQL pipeline rather than
#               record_service_health). Not a reliability gap.
#   "gap:"    — the job writes NO service_health row at all. A real
#               observability gap: the unit can fail on every fire and nothing
#               in either catalog notices. Each needs its own writer AND its
#               own freshness window, so each is its own task; carried as the
#               standing remainder of R-277.
EXEMPT_UNITS: dict[str, str] = {
    # Nine former entries were deleted once `_names_in` learned the
    # bounded-stdlib writer shape (R-412): db-backup, db-retention,
    # disk-cleanup, drift-audit, grok-page-responder, host-metrics,
    # ib-watchdog, media-backup and portfolio-archive all RESOLVE now. Ten of
    # the sixteen entries here were labelled `gap: writes no service_health
    # row` and that was false for eight of them, which is worse than no
    # exemption: it told the next reader a monitored job was unmonitored.
    # `test_rel141_catalog_exemptions_are_true.py` now asserts that every
    # remaining `gap:` label is TRUE.
    "radon-watchdog-continuous": (
        "parser: `python -m scripts.watchdog` writes its rows through the "
        "watchdog package, keyed on the bucket passed on the command line."
    ),
    "radon-watchdog-daily": "parser: same as radon-watchdog-continuous.",
    "radon-watchdog-error": "parser: same as radon-watchdog-continuous.",
    "radon-watchdog-intraday": "parser: same as radon-watchdog-continuous.",
    "radon-portfolio-sync": (
        "wrapper: run_portfolio_refresh.sh POSTs FastAPI /portfolio/sync, and "
        "the row is opened by ib_sync.py's `service_cycle(\"portfolio-sync\")` "
        "inside the API process. The name IS in both catalogs; the unit's own "
        "ExecStart chain simply does not contain the writer."
    ),
    "radon-signals-refresh": (
        "wrapper: run_signals_refresh.sh POSTs /theta-harvester/scan and "
        "/strength-confirmation/scan, each of which writes its own row under "
        "`theta-harvester` / `strength-confirmation` — both already in both "
        "catalogs. The wrapper needs no name of its own; registering one would "
        "add a key nothing writes."
    ),
}


class TestEveryTimerBackedUnitIsAccountedFor:
    def test_every_timer_backed_unit_writes_a_health_row_or_is_exempt(self):
        """R-325: the assertion the suite was missing.

        `test_every_timer_backed_writer_is_in_both_catalogs` iterates only the
        names it RESOLVED, so a unit the parser could not read was skipped
        rather than flagged — which reads identically to being fine. This is
        the complementary check: no timer-backed unit may be silently outside
        the invariant.
        """
        unaccounted = sorted(
            unit
            for unit in _timer_backed_services()
            if not _health_names_written_by(unit) and unit not in EXEMPT_UNITS
        )
        assert not unaccounted, (
            "these timer-backed units contribute no catalogued health name and "
            "carry no EXEMPT_UNITS reason, so nothing notices when they fail "
            f"on every fire: {unaccounted}"
        )

    def test_the_exempt_lists_name_only_real_units(self):
        """A stale entry would silently re-open the hole it was covering."""
        stale = sorted(set(EXEMPT_UNITS) - _timer_backed_services())
        assert not stale, f"exempt entries for units that no longer exist: {stale}"

    def test_every_exempt_unit_still_lacks_a_resolvable_name(self):
        """When a unit starts contributing a name, drop it from the list."""
        fixed = sorted(
            unit for unit in EXEMPT_UNITS if _health_names_written_by(unit)
        )
        assert not fixed, (
            f"these units now contribute a health name; remove them: {fixed}"
        )

    def test_every_exempt_unit_states_a_reason(self):
        """An exemption without a reason is an oversight wearing a decision."""
        unreasoned = sorted(
            unit
            for unit, reason in EXEMPT_UNITS.items()
            if not reason.strip()
            or not reason.strip().startswith(("parser:", "gap:", "wrapper:"))
        )
        assert not unreasoned, (
            "every EXEMPT_UNITS entry must state whether the unit heartbeats "
            "under a name this parser cannot resolve (`parser:`), delegates to "
            "a process that writes an already-catalogued name (`wrapper:`), or "
            "writes no "
            f"row at all (`gap:`): {unreasoned}"
        )

    def test_the_service_cycle_and_private_constant_writers_are_resolved(self):
        """Pins the two forms R-325 named, so the widening cannot regress.

        `bpi_scan.py` heartbeats through `service_cycle(SERVICE_NAME, ...)` —
        a call the scan did not know existed — and `perf_twr_builder.py` binds
        its name to `_PERF_TWR_SERVICE`, which the old three-name constant
        pattern did not match. Both units read as "writes no row".
        """
        assert "bpi-scan" in _health_names_written_by("radon-bpi")
        assert "perf-twr" in _health_names_written_by("radon-perf-twr")

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

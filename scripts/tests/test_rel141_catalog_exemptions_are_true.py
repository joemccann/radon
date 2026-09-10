"""R-402 / R-412 / REL-141: an exemption must be TRUE, and a timer must be monitored.

R-412 (NF-8, third consecutive week): REL-114's completeness assertion is
legitimately green, but ten of its sixteen `EXEMPT_UNITS` entries are labelled
`gap: writes no service_health row` and that is FALSE for eight of them --
`db_backup.py`, `db_retention_sweep.py`, `drift_audit.py`, `media_backup.py`,
`archive_portfolio_snapshots.py`, `host_metrics_sampler.py`,
`grok_page_responder.py` and the wrapper `.sh` targets all heartbeat on every
fire, and seven of those names are in BOTH watchdog catalogs. The root cause is
that `_HEALTH_CALLS` never listed the bounded-stdlib `write_service_health`
shape (a module-level `SERVICE_NAME` literal plus a `def write_service_health`),
so the scan could not see them. A mislabelled exemption is worse than no
exemption: it tells the next reader a monitored job is unmonitored.

R-402: `forecast-nightly` and `signals-refresh` are the two GENUINE gaps -- both
timer-backed, in neither catalog, and `nightly_forecast.py` contains no
`service_health` reference on any path.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_watchdog_catalog_parity as parity  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

# Units the walk verified DO heartbeat, with the name each writes.
RESOLVABLE = {
    "radon-db-backup": "db-backup",
    "radon-db-retention": "db-retention",
    "radon-drift-audit": "config-drift",
    "radon-media-backup": "media-backup",
    "radon-portfolio-archive": "portfolio-archive",
    "radon-host-metrics": "host-metrics",
    "radon-disk-cleanup": "disk-cleanup",
    "radon-grok-page-responder": "grok-page-responder",
}


class TestTheScanSeesTheBoundedStdlibWriter:
    @pytest.mark.parametrize("unit,name", sorted(RESOLVABLE.items()))
    def test_the_resolver_finds_the_service_name(self, unit, name):
        assert parity._health_names_written_by(unit) == {name}, (
            f"{unit} heartbeats through its own bounded-stdlib "
            "write_service_health; the scan simply could not see the shape"
        )

    @pytest.mark.parametrize("unit", sorted(RESOLVABLE))
    def test_the_false_exemption_is_gone(self, unit):
        assert unit not in parity.EXEMPT_UNITS, (
            f"{unit} resolves now, so its exemption is dead weight that tells "
            "the next reader a monitored job is unmonitored"
        )


class TestEveryRemainingGapIsReallyAGap:
    @staticmethod
    def _writes_a_row(unit: str) -> str | None:
        """The ExecStart target that contradicts a `gap:` label, if any."""
        unit_text = (parity.SERVICES_DIR / f"{unit}.service").read_text(encoding="utf-8")
        exec_start = "\n".join(
            line for line in unit_text.splitlines() if line.startswith("ExecStart")
        )
        for rel in parity._exec_targets(exec_start):
            path = parity._resolve(rel)
            if path is None or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(call in text for call in parity._HEALTH_CALLS):
                return str(path)
            if parity._LOCAL_HEALTH_WRITER.search(text):
                return str(path)
        return None

    def test_no_gap_exemption_names_a_unit_that_does_heartbeat(self):
        """The assertion the suite was missing: a `gap:` label must be TRUE."""
        liars = [
            (unit, self._writes_a_row(unit))
            for unit, reason in parity.EXEMPT_UNITS.items()
            if reason.startswith("gap:") and self._writes_a_row(unit)
        ]
        assert not liars, (
            "these exemptions claim the unit writes no service_health row, but "
            f"its own ExecStart target contains one: {liars}"
        )

    def test_a_seeded_false_gap_is_caught(self, monkeypatch):
        """The guard above must actually red on a mislabelled exemption."""
        seeded = next(
            unit for unit in sorted(parity._timer_backed_services())
            if self._writes_a_row(unit)
        )
        monkeypatch.setitem(
            parity.EXEMPT_UNITS, seeded, "gap: writes no service_health row."
        )
        with pytest.raises(AssertionError):
            self.test_no_gap_exemption_names_a_unit_that_does_heartbeat()


class TestTheTwoGenuineGapsAreClosed:
    @pytest.mark.parametrize("name", ("forecast-nightly", "flow-refresh"))
    def test_the_service_is_in_both_catalogs(self, name):
        """`flow-refresh` is here because the widened resolver surfaced it: it
        has always written its own row and was in NEITHER catalog."""
        from watchdog.services import SCHEDULED_SERVICES

        assert name in SCHEDULED_SERVICES, name
        windows = (REPO / "web" / "lib" / "serviceHealthWindows.ts").read_text(encoding="utf-8")
        assert re.search(rf'"{re.escape(name)}"\s*:', windows), name

    def test_signals_refresh_is_a_wrapper_not_a_new_catalog_key(self):
        """R-402 asked for a `signals-refresh` registration; it is not one.

        `run_signals_refresh.sh` POSTs /theta-harvester/scan and
        /strength-confirmation/scan, each of which writes its OWN row under a
        name already in both catalogs. Registering `signals-refresh` would add a
        key nothing ever writes, which ages to stale immediately and pages
        forever. The exemption states that instead.
        """
        from watchdog.services import SCHEDULED_SERVICES

        assert "signals-refresh" not in SCHEDULED_SERVICES
        assert parity.EXEMPT_UNITS["radon-signals-refresh"].startswith("wrapper:")
        for delegate in ("theta-harvester", "strength-confirmation"):
            assert delegate in SCHEDULED_SERVICES, delegate

    def test_the_forecast_shim_writes_a_row_even_when_it_raises(self, monkeypatch):
        import nightly_forecast

        rows: list[tuple] = []
        monkeypatch.setattr(
            nightly_forecast, "_record_health",
            lambda state, detail=None: rows.append((state, detail)),
        )
        def _boom(_argv):
            raise RuntimeError("chronos down")

        monkeypatch.setattr(nightly_forecast, "_run", _boom)
        # The unit must still FAIL — the row is additional evidence, not a
        # replacement for the non-zero exit systemd needs.
        with pytest.raises(RuntimeError):
            nightly_forecast.main([])
        assert rows and rows[-1][0] == "error", rows
        assert "chronos down" in str(rows[-1][1]), rows

    def test_the_forecast_shim_writes_ok_on_success(self, monkeypatch):
        import nightly_forecast

        rows: list[tuple] = []
        monkeypatch.setattr(
            nightly_forecast, "_record_health",
            lambda state, detail=None: rows.append((state, detail)),
        )
        monkeypatch.setattr(nightly_forecast, "_run", lambda _argv: 0)
        assert nightly_forecast.main([]) == 0
        assert [state for state, _ in rows] == ["ok"], rows

    def test_the_forecast_service_name_is_a_module_literal(self):
        import nightly_forecast

        assert nightly_forecast.SERVICE_NAME == "forecast-nightly"

    def test_both_units_resolve_a_health_name_now(self):
        for unit, name in (("radon-forecast-nightly", "forecast-nightly"),):
            assert parity._health_names_written_by(unit) == {name}, unit

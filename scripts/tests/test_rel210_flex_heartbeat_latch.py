"""REL-210 (R-584, R-585, R-586): the flex batch heartbeat is honest.

R-584: the sftp batch loop iterates the remote listing unsorted and delivered
files are never removed remotely, so a failing NEW Activity statement (error
heartbeat) followed by a stale duplicate (unconditional ok heartbeat) ended
the run green on the exact `cash-flow-sync` row the `/cash-flows` lozenge and
watchdog read. The error must latch for the life of the process.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import flex_delivery_ingest as ingest  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
ACTIVITY_XML = FIXTURES / "cash_transactions_flex_ytd_detail_sample.xml"


@pytest.fixture(autouse=True)
def _reset_latch(monkeypatch):
    monkeypatch.setattr(ingest, "_CASH_FLOW_ERROR_LATCHED", False, raising=False)


@pytest.fixture()
def heartbeats(monkeypatch):
    """Capture every cash-flow-sync heartbeat state in write order."""
    from db import writer

    states: list[str] = []

    def _record(service, state, **kwargs):
        if service == ingest.CASH_FLOW_HEALTH_SERVICE:
            states.append(state)
        return True

    monkeypatch.setattr(writer, "record_service_health", _record)
    return states


class TestDuplicateOkNeverOverwritesSameRunError:
    def test_failing_new_then_duplicate_ends_in_error(
        self, monkeypatch, tmp_path, heartbeats
    ):
        import cash_flow_sync

        xml = ACTIVITY_XML.read_text()
        # First call: a NEW statement whose cash_flow_sync fails.
        monkeypatch.setattr(ingest, "claim_flex_delivery", lambda *a, **k: True)
        monkeypatch.setattr(ingest, "release_flex_delivery", lambda _d: True)
        monkeypatch.setattr(cash_flow_sync, "main", lambda _a: 1)
        failing = tmp_path / "new.xml"
        failing.write_text(xml + "<!-- new -->", encoding="utf-8")
        first = ingest.ingest_path(failing)
        assert first["ok"] is False
        assert heartbeats and heartbeats[-1] == "error"

        # Second call, SAME process: an already-applied duplicate.
        monkeypatch.setattr(ingest, "claim_flex_delivery", lambda *a, **k: False)
        monkeypatch.setattr(ingest, "flex_delivery_status", lambda _d: "applied")
        dup = tmp_path / "dup.xml"
        dup.write_text(xml, encoding="utf-8")
        second = ingest.ingest_path(dup)
        assert second.get("outcome") == "duplicate"

        assert heartbeats[-1] == "error", (
            "a duplicate-ok overwrote a same-run error heartbeat; the "
            f"cash-flow-sync row ended {heartbeats[-1]!r}: {heartbeats}"
        )

    def test_duplicate_alone_still_heartbeats_ok(
        self, monkeypatch, tmp_path, heartbeats
    ):
        """The R-389 compensation stays: a clean duplicate-only run is ok."""
        monkeypatch.setattr(ingest, "claim_flex_delivery", lambda *a, **k: False)
        monkeypatch.setattr(ingest, "flex_delivery_status", lambda _d: "applied")
        dup = tmp_path / "dup.xml"
        dup.write_text(ACTIVITY_XML.read_text(), encoding="utf-8")
        result = ingest.ingest_path(dup)
        assert result.get("outcome") == "duplicate"
        assert heartbeats == ["ok"]


class TestFreshnessPairingIsDocumented:
    def test_watchdog_window_names_the_flex_pull_pairing(self):
        """R-585/R-586: the 3-day cash-flow-sync window is shadowed by
        flex-pull's 26h duplicate-only error; data-freshness paging rides on
        flex-pull and the source must say so where the window is set."""
        src = (SCRIPTS / "watchdog" / "services.py").read_text()
        block = src[src.index("cash-flow-sync"):][:1200]
        assert "flex-pull" in block and "duplicate-only" in block, (
            "watchdog services.py sets the cash-flow-sync window without "
            "documenting that freshness paging depends on flex-pull"
        )

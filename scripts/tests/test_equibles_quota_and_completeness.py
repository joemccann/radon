"""Equibles: quota exhaustion must not heartbeat ok, and a partial holder
write must not claim completeness.

R-226: the sibling 13F job defines `_cycle_fatal_errors()` returning
`(EquiblesAuthError, EquiblesRateLimitError)` and re-raises them out of
`_safe_call` precisely so "an exhausted daily allowance ... [does not] write an
empty payload and heartbeat 'ok'" — its docstring says so. Filing-forensics has
no such carve-out: `_fetch_source` is a bare `except Exception` that turns a
429 or a revoked key into `SourceResult(STATUS_ERROR, [])` for every source of
every remaining ticker, the loop marks each into `skipped`, and `_record_health("ok")`
fires as long as ONE ticker succeeded before the quota tripped. Exit 0, a green
freshness banner, and 29 of 30 dossiers silently stale mid-cycle.

R-227: `_upsert_holder_rows` writes N chunks in a loop with a single
`db.commit()`, so a failure on chunk k leaves chunks 0..k-1 executed and
k..N absent. The `holder rows non-fatal` catch swallows that, and
`_upsert_snapshot` then writes the snapshot containing the FULL holders array
and `holder_count`, asserting completeness — while the upsert is not preceded
by a delete, so the truncated set is silently mixed with the previous
quarter's surviving rows.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import fetch_equibles_filing_forensics as forensics  # noqa: E402
import fetch_equibles_smart_money_13f as smart_money  # noqa: E402
from clients.equibles_client import EquiblesAuthError, EquiblesRateLimitError  # noqa: E402


class TestForensicsQuotaIsFatal:
    @pytest.mark.parametrize("exc", [EquiblesRateLimitError, EquiblesAuthError])
    def test_a_quota_error_is_not_swallowed_into_an_empty_source(self, exc):
        def call():
            raise exc("daily allowance exhausted")

        with pytest.raises(exc):
            forensics._fetch_source(call)

    def test_an_ordinary_endpoint_fault_still_degrades_one_source(self):
        def call():
            raise RuntimeError("that one endpoint is down")

        result = forensics._fetch_source(call)
        assert result.status == forensics.STATUS_ERROR
        assert result.rows == []

    def test_the_carve_out_matches_the_sibling_job(self):
        assert set(forensics._cycle_fatal_errors()) == set(
            smart_money._cycle_fatal_errors()
        ), "the two Equibles jobs disagree about what kills a cycle"

    def test_a_mid_cycle_quota_trip_does_not_heartbeat_ok(self, monkeypatch):
        states: list[str] = []
        monkeypatch.setattr(
            forensics, "_record_health",
            lambda state, error=None: states.append(state),
        )
        monkeypatch.setattr(forensics, "fetch_going_concern_universe", lambda c: {})
        monkeypatch.setattr(forensics, "_write_db_row", lambda d: None)
        monkeypatch.setattr(forensics, "_write_json_cache", lambda d: None)

        calls = {"n": 0}

        def fetch_dossier(client, ticker, going_concern, today=None, as_of=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"ticker": ticker, "flag_count": 0, "flags": [],
                        "data_complete": True, "sources": {"x": "ok"}}
            raise EquiblesRateLimitError("daily allowance exhausted")

        monkeypatch.setattr(forensics, "fetch_dossier", fetch_dossier)
        monkeypatch.setattr(forensics, "has_usable_data", lambda d: True)

        with pytest.raises(EquiblesRateLimitError):
            forensics.run(["AAA", "BBB", "CCC"], client=MagicMock())

        assert "ok" not in states, (
            f"a quota-exhausted cycle heartbeat {states}; one ticker that "
            "landed before the trip was enough to report healthy"
        )
        assert states and states[-1] == "error"


class TestHolderWriteIsAtomic:
    def _holders(self, n: int) -> list[dict]:
        return [
            {
                "cik": f"{i:010d}", "institution": f"F{i}", "shares": 100,
                "value_usd": 1000, "pct_of_institutional_total": 0.1,
                "position_type": "long", "change_in_shares": 0, "change_type": "hold",
            }
            for i in range(n)
        ]

    def test_a_failed_chunk_does_not_leave_earlier_chunks_committed(self, monkeypatch):
        executed: list[int] = []

        class _Db:
            def execute(self, sql, params=()):
                executed.append(len(params))
                if len(executed) == 3:
                    raise RuntimeError("hrana stream closed")

            def commit(self):
                pass

            def rollback(self):
                executed.clear()

        import db.client as db_client

        monkeypatch.setattr(db_client, "get_db", lambda: _Db())
        holders = self._holders(smart_money._HOLDER_INSERT_CHUNK_ROWS * 5)

        with pytest.raises(RuntimeError):
            smart_money._upsert_holder_rows("AAPL", "2026-06-30", holders, "now")

        assert executed == [], (
            "chunks 0..k-1 stayed applied after chunk k failed, so the table "
            "holds a truncated set for that (ticker, report_date)"
        )

    def test_a_partial_holder_write_is_recorded_on_the_snapshot(self, monkeypatch):
        recorded: dict = {}

        monkeypatch.setattr(
            smart_money, "_upsert_holder_rows",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("chunk 3 failed")),
        )
        monkeypatch.setattr(
            smart_money, "_upsert_snapshot",
            lambda ticker, report_date, scan_time, payload: recorded.update(payload),
        )
        payload = {
            "ticker": "AAPL", "report_date": "2026-06-30", "scan_time": "now",
            "holders": self._holders(10), "holder_count": 10,
        }
        smart_money._write_db_cache(payload)

        assert recorded.get("holders_persisted") is False, (
            "the snapshot asserted a complete holder set while the rows write "
            "had failed; nothing downstream could tell"
        )

    def test_a_successful_holder_write_marks_the_snapshot_complete(self, monkeypatch):
        recorded: dict = {}
        monkeypatch.setattr(smart_money, "_upsert_holder_rows", lambda *a, **k: None)
        monkeypatch.setattr(
            smart_money, "_upsert_snapshot",
            lambda ticker, report_date, scan_time, payload: recorded.update(payload),
        )
        payload = {
            "ticker": "AAPL", "report_date": "2026-06-30", "scan_time": "now",
            "holders": self._holders(10), "holder_count": 10,
        }
        smart_money._write_db_cache(payload)
        assert recorded.get("holders_persisted") is True


class TestCanonicalArtifactsAreAtomic:
    """R-260: `data/garch_convergence.json` has no Turso table behind it.

    A SIGTERM or a full disk during a plain `write_text` truncates the only
    copy, `/api/garch-convergence` then serves a JSONDecodeError, and there is
    no DB row to fall back to. `bpi_scan.py` already uses `atomic_save` for the
    same job. `mirror_scan_snapshot` also runs AFTER the write, so a truncated
    file is not even paired with a health row recording the fault.
    """

    @pytest.mark.parametrize(
        "module", ["garch_convergence.py", "leap_scanner_uw.py"]
    )
    def test_the_canonical_cache_is_written_atomically(self, module):
        source = (_SCRIPTS_DIR / module).read_text(encoding="utf-8")
        body = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        assert "atomic_save" in body, f"{module} writes its canonical cache non-atomically"
        assert "cache_path.write_text(" not in body, (
            f"{module} still has a plain write_text on the cache path"
        )

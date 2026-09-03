"""REL-220 (R-588): a TWR-build failure releases the claim and the retry
converges — re-ingesting the same bytes re-applies to identical rows."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import flex_delivery_ingest as ingest  # noqa: E402

ACTIVITY_XML = (Path(__file__).parent / "fixtures" / "cash_transactions_flex_ytd_detail_sample.xml")


class TestTwrFailureRetryConverges:
    def test_claim_released_and_second_ingest_converges(self, monkeypatch, tmp_path):
        import cash_flow_sync
        import perf_twr_builder
        from db import writer

        monkeypatch.setattr(writer, "record_service_health", lambda *a, **k: True)

        claims: list[str] = []
        released: list[str] = []
        applied: list[str] = []
        monkeypatch.setattr(
            ingest, "claim_flex_delivery", lambda d, **k: claims.append(d) or True
        )
        monkeypatch.setattr(
            ingest, "release_flex_delivery", lambda d: released.append(d) or True
        )
        monkeypatch.setattr(
            ingest, "mark_flex_delivery_applied", lambda d: applied.append(d) or True
        )

        cash_runs: list[str] = []
        monkeypatch.setattr(
            cash_flow_sync, "main", lambda args: cash_runs.append(args[1]) or 0
        )
        twr_calls = {"n": 0}

        def twr(**kwargs):
            twr_calls["n"] += 1
            if twr_calls["n"] == 1:
                raise RuntimeError("turso died mid-TWR")
            return {"status": "ok"}

        monkeypatch.setattr(perf_twr_builder, "build_and_persist", twr)

        path = tmp_path / "activity.xml"
        path.write_text(ACTIVITY_XML.read_text(), encoding="utf-8")

        with pytest.raises(RuntimeError):
            ingest.ingest_path(path)
        assert released == claims[:1], (
            "a TWR-build exception did not hand the claim back — the same "
            "bytes would be permanently unretryable behind a green heartbeat"
        )
        assert applied == []

        result = ingest.ingest_path(path)
        assert result["ok"] is True
        # Convergence: the retry re-ran the SAME cash apply with the same
        # source; the id-keyed upsert makes the rows identical.
        assert cash_runs[0] == cash_runs[1]
        assert applied, "the successful retry never marked the claim applied"

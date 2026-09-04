"""REL-220 (R-588): a TWR-build failure releases the claim and the retry
converges — re-ingesting the same bytes re-applies to identical rows."""
from __future__ import annotations

import sqlite3
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

        # A REAL cash_flows table, not a call recorder. Recording the argument
        # and comparing cash_runs[0] == cash_runs[1] compared one tmp_path
        # string to itself and stayed green even if the "upsert" duplicated
        # every row on the retry — which is the whole claim under test.
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            (SCRIPTS / "db" / "migrations" / "0002_cash_flows.sql").read_text()
        )
        monkeypatch.setattr(writer, "get_db", lambda: conn)
        # synced_at is stamped per call; freeze it so "identical" means all
        # eight columns, not seven plus a wall-clock timestamp.
        monkeypatch.setattr(writer, "_now_iso", lambda: "2026-09-04T00:00:00Z")

        cash_runs: list[str] = []

        def fake_cash_main(args):
            path_arg = args[args.index("--from-file") + 1]
            cash_runs.append(path_arg)
            parsed = cash_flow_sync.parse_cash_transactions(
                Path(path_arg).read_text(encoding="utf-8")
            )
            writer.upsert_cash_flow_rows(parsed)
            return 0

        monkeypatch.setattr(cash_flow_sync, "main", fake_cash_main)

        def cash_snapshot():
            return conn.execute(
                "SELECT id, date, type, amount, currency, description, raw_type,"
                " synced_at FROM cash_flows ORDER BY id"
            ).fetchall()
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
        first_pass = cash_snapshot()
        assert first_pass, "the first ingest applied no cash rows at all"
        assert released == claims[:1], (
            "a TWR-build exception did not hand the claim back — the same "
            "bytes would be permanently unretryable behind a green heartbeat"
        )
        assert applied == []

        result = ingest.ingest_path(path)
        assert result["ok"] is True
        # Convergence: the retry re-ran the SAME cash apply against the same
        # table, and the id-keyed upsert left the row set byte-identical
        # rather than duplicating every row.
        assert cash_runs[0] == cash_runs[1]
        assert cash_snapshot() == first_pass, (
            "re-ingesting the same bytes did not converge — the second apply "
            "changed or duplicated rows"
        )
        assert applied, "the successful retry never marked the claim applied"

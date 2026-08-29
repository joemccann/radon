"""R-323 / R-361 / REL-112: the Flex Activity ingest is transactional in effect.

The Activity branch ran two independent persisting writers back to back with
no transaction and no short-circuit: `cash_flow_sync.main(...)`, then
`perf_twr_builder.build_and_persist(persist=True)` UNCONDITIONALLY, with the
cash exit code only inspected afterwards to shape the return dict.
`upsert_cash_flow_rows` chunks its writes, so a mid-upsert failure leaves
earlier chunks committed and the TWR series is then computed over half-written
cash flows and persisted as authoritative.

Separately, the inbox loop caught only `FlexClassifyError`, so one unreadable
file aborted the whole batch AFTER earlier files had already mutated
`cash_flows`, `journal` and TWR — with nothing printed at all.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import flex_delivery_ingest as ingest  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ACTIVITY = FIXTURES / "cash_transactions_flex_ytd_detail_sample.xml"


@pytest.fixture(autouse=True)
def _claim_always_wins(monkeypatch):
    """R-326 added a delivery claim ahead of the writers; grant it here.

    These cases are about what happens AFTER the file is accepted, so the
    fingerprint gate is stubbed rather than exercised. Its own behaviour is
    covered by `test_flex_delivery_fingerprint.py`.

    T-257 added the paired release; it is recorded rather than granted so the
    short-circuit case can assert the failed run handed its claim back.
    """
    released: list[str] = []
    monkeypatch.setattr(ingest, "claim_flex_delivery", lambda _d, **_k: True)
    monkeypatch.setattr(ingest, "release_flex_delivery", lambda d: released.append(d))
    return released


class TestActivityShortCircuit:
    def test_twr_is_not_persisted_over_half_written_cash_flows(
        self, monkeypatch, tmp_path, _claim_always_wins
    ):
        """A failed cash upsert must stop the run BEFORE the TWR build."""
        import cash_flow_sync
        import perf_twr_builder

        calls: list[str] = []

        def _cash(_argv):
            calls.append("cash")
            return getattr(cash_flow_sync, "EXIT_WRITE_ERROR", 3)

        def _twr(**_kwargs):
            calls.append("twr")
            return {"status": "ok"}

        monkeypatch.setattr(cash_flow_sync, "main", _cash)
        monkeypatch.setattr(perf_twr_builder, "build_and_persist", _twr)

        path = tmp_path / "activity.xml"
        path.write_text(ACTIVITY.read_text(), encoding="utf-8")
        result = ingest.ingest_path(path)

        assert "twr" not in calls, (
            "the TWR series was rebuilt and persisted as authoritative over a "
            f"cash-flow table the writer failed half way through; calls={calls}"
        )
        assert result["ok"] is False
        assert result["cash_exit"] != 0
        assert _claim_always_wins == [result["content_sha256"]], (
            "the failed run kept its delivery claim, so re-dropping the fixed "
            "file returns 'duplicate' and the half-written chunks stay. T-257"
        )

    def test_a_healthy_activity_file_still_runs_both_writers(self, monkeypatch, tmp_path):
        import cash_flow_sync
        import perf_twr_builder

        calls: list[str] = []
        monkeypatch.setattr(cash_flow_sync, "main", lambda _a: calls.append("cash") or 0)
        monkeypatch.setattr(
            perf_twr_builder,
            "build_and_persist",
            lambda **_k: (calls.append("twr"), {"status": "ok"})[1],
        )

        path = tmp_path / "activity.xml"
        path.write_text(ACTIVITY.read_text(), encoding="utf-8")
        result = ingest.ingest_path(path)

        assert calls == ["cash", "twr"]
        assert result["ok"] is True


class TestInboxLoopIsolatesFailures:
    def _inbox(self, tmp_path, names):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        for name in names:
            (inbox / name).write_text(ACTIVITY.read_text(), encoding="utf-8")
        return inbox

    def test_one_unreadable_file_does_not_abort_the_batch(self, monkeypatch, tmp_path, capsys):
        inbox = self._inbox(tmp_path, ["a.xml", "b.xml", "c.xml"])
        seen: list[str] = []

        def _ingest_path(path: Path):
            if path.name == "b.xml":
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
            seen.append(path.name)
            return {"ok": True, "source_path": str(path)}

        monkeypatch.setattr(ingest, "ingest_path", _ingest_path)

        code = ingest.main(["--inbox", str(inbox)])

        out = capsys.readouterr().out
        assert out.strip(), "a batch that hit an unreadable file printed nothing at all"
        payload = json.loads(out)
        assert seen == ["a.xml", "c.xml"], (
            f"the third file was never processed; seen={seen}"
        )
        assert len(payload["results"]) == 3
        failed = [r for r in payload["results"] if not r.get("ok")]
        assert len(failed) == 1
        assert "b.xml" in failed[0]["source_path"]
        assert failed[0].get("error")
        assert payload["ok"] is False
        assert code == 1

    def test_a_clean_batch_still_reports_every_file(self, monkeypatch, tmp_path, capsys):
        inbox = self._inbox(tmp_path, ["a.xml", "b.xml"])
        monkeypatch.setattr(
            ingest, "ingest_path", lambda p: {"ok": True, "source_path": str(p)}
        )
        code = ingest.main(["--inbox", str(inbox)])
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["ok"] is True
        assert len(payload["results"]) == 2

    def test_classify_errors_are_still_recorded_per_file(self, monkeypatch, tmp_path, capsys):
        """The pre-existing FlexClassifyError handling keeps its behaviour."""
        from lib.flex_classify import FlexClassifyError

        inbox = self._inbox(tmp_path, ["a.xml", "b.xml"])

        def _ingest_path(path: Path):
            if path.name == "a.xml":
                raise FlexClassifyError("unknown_statement")
            return {"ok": True, "source_path": str(path)}

        monkeypatch.setattr(ingest, "ingest_path", _ingest_path)
        code = ingest.main(["--inbox", str(inbox)])
        payload = json.loads(capsys.readouterr().out)
        assert code == 1
        assert "unknown_statement" in payload["results"][0]["error"]
        assert payload["results"][1]["ok"] is True

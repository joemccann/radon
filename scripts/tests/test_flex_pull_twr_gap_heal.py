"""flex-pull heals an EARLIER twr_subperiods gap by replaying the TWR build only.

2026-09-24: a nightly build that failed for any reason wrote no subperiods, and
`_extend_statement_flows` then refused every later statement with
`historical_flow_coverage_unverified` forever. The weekday perf-twr timer chains
only through MAX(twr_subperiods), so nothing healed it until the operator
replayed the missed Equity_Summary statements oldest-first by hand.

The replay must go through the TWR builder ONLY: the activity delivery is
already fingerprint-claimed, and re-running cash_flow_sync / the journal is not
idempotent. A session with no statement stays uncovered (never zero flows).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import flex_sftp_pull as pull  # noqa: E402
import perf_twr_builder as twr  # noqa: E402
from test_flex_sftp_pull import AFTER_FIRST_DELIVERY, FakeSftp  # noqa: E402
from test_rel146_flex_sftp_honesty import _config_lines, _write  # noqa: E402

ACCOUNT = "U4698258"


def _statement_xml(day: str) -> str:
    return (
        '<FlexQueryResponse><FlexStatements count="1">'
        f'<FlexStatement accountId="{ACCOUNT}" fromDate="{day}" toDate="{day}" period="LastBusinessDay">'
        "<EquitySummaryInBase/><CashTransactions/><Transfers/>"
        "</FlexStatement></FlexStatements></FlexQueryResponse>"
    )


def _name(day: str) -> str:
    return f"{ACCOUNT}.Equity_Summary_in_Base.{day}.{day}.xml.pgp"


def _iso(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


class Harness:
    """Drive `pull.run` over dated activity statements with Turso stubbed."""

    def __init__(self, monkeypatch, tmp_path, days, *, uncovered, after_replay=None):
        self.beats: list[tuple] = []
        self.ingested: list[str] = []
        self.replayed: list[str] = []
        self.uncovered_calls: list[tuple] = []
        self._answers = [uncovered, after_replay if after_replay is not None else []]
        self.tmp_path = tmp_path
        self.files = {_name(day): _statement_xml(day).encode() for day in days}
        monkeypatch.setattr(pull, "_heartbeat", lambda state, error=None: self.beats.append((state, error)))
        monkeypatch.setattr(pull, "classify_flex_xml", lambda _x: "activity")
        monkeypatch.setattr(pull, "_uncovered_nav_sessions", self._uncovered)
        monkeypatch.setattr(pull, "_replay_twr_statement", self._replay)

    def _uncovered(self, since, through):
        self.uncovered_calls.append((since, through))
        answer = self._answers[min(len(self.uncovered_calls) - 1, len(self._answers) - 1)]
        return None if answer is None else list(answer)

    def _replay(self, xml_text):
        self.replayed.append(xml_text.split('toDate="')[1][:8])

    def _ingest(self, xml_text, source_path="", **_k):
        self.ingested.append(Path(source_path).name)
        return {"ok": True, "outcome": "duplicate", "persistence_confirmed": True}

    def run(self) -> int:
        inbox = self.tmp_path / "inbox"
        inbox.mkdir(exist_ok=True)
        return pull.run(
            config=_write(self.tmp_path, _config_lines()),
            inbox=inbox,
            runner=FakeSftp(self.files),
            decrypt=lambda data, **k: data.decode(),
            ingest=self._ingest,
            now=AFTER_FIRST_DELIVERY,
        )


class TestReplayPlan:
    def test_maps_uncovered_sessions_to_statements_oldest_first(self):
        statements = [
            pull.ActivityStatement(_name(d), Path(d), date.fromisoformat(_iso(d)), date.fromisoformat(_iso(d)))
            for d in ("20260924", "20260922", "20260923")
        ]
        plan = pull.plan_gap_replay(["2026-09-23", "2026-09-22"], statements)
        assert [s.period_to.isoformat() for s in plan.statements] == ["2026-09-22", "2026-09-23"]
        assert plan.unhealable == []

    def test_session_without_a_statement_blocks_every_later_replay(self):
        """The builder gate needs every EARLIER session covered, so a statement
        after an unhealable session cannot pass it; replaying it is wasted time."""
        statements = [
            pull.ActivityStatement(_name(d), Path(d), date.fromisoformat(_iso(d)), date.fromisoformat(_iso(d)))
            for d in ("20260918", "20260923")
        ]
        plan = pull.plan_gap_replay(["2026-09-18", "2026-09-22", "2026-09-23"], statements)
        assert [s.period_to.isoformat() for s in plan.statements] == ["2026-09-18"]
        assert plan.unhealable == ["2026-09-22"]
        assert plan.blocked == ["2026-09-23"]


class TestFlexPullHealsEarlierGap:
    def test_replays_missed_statements_oldest_first_through_twr_only(self, tmp_path, monkeypatch):
        days = ("20260921", "20260922", "20260923", "20260924")
        h = Harness(monkeypatch, tmp_path, days, uncovered=["2026-09-22", "2026-09-23", "2026-09-24"])
        assert h.run() == 0
        assert h.replayed == ["20260922", "20260923", "20260924"]
        # The activity writers saw each delivery exactly once (the normal
        # ingest); the replay never re-entered cash_flow_sync / journal.
        assert sorted(h.ingested) == sorted(_name(d)[: -len(".pgp")] for d in days)
        state, note = h.beats[-1]
        assert state == "ok"
        assert note["class"] == "twr_gap_healed"
        assert note["replayed"] == ["2026-09-22", "2026-09-23", "2026-09-24"]

    def test_lookback_is_bounded_and_anchored_on_the_newest_statement(self, tmp_path, monkeypatch):
        h = Harness(monkeypatch, tmp_path, ("20260923", "20260924"), uncovered=[])
        h.run()
        since, through = h.uncovered_calls[0]
        assert through == "2026-09-24"
        assert since == (date(2026, 9, 24) - pull.TWR_GAP_LOOKBACK).isoformat()

    def test_no_gap_leaves_the_heartbeat_untouched(self, tmp_path, monkeypatch):
        h = Harness(monkeypatch, tmp_path, ("20260923", "20260924"), uncovered=[])
        assert h.run() == 0
        assert h.replayed == []
        assert h.beats[-1] == ("ok", None)

    def test_unknown_coverage_replays_nothing(self, tmp_path, monkeypatch):
        h = Harness(monkeypatch, tmp_path, ("20260923", "20260924"), uncovered=None)
        assert h.run() == 0
        assert h.replayed == []
        assert h.beats[-1] == ("ok", None)

    def test_missing_remote_statement_is_a_warning_and_never_zero_filled(self, tmp_path, monkeypatch):
        # 09-22 was never delivered: nothing may be replayed for it or after it.
        h = Harness(
            monkeypatch, tmp_path, ("20260921", "20260923", "20260924"),
            uncovered=["2026-09-22", "2026-09-23"],
            after_replay=["2026-09-22", "2026-09-23"],
        )
        assert h.run() == 0
        assert h.replayed == []
        state, note = h.beats[-1]
        assert state == "ok"
        assert note["class"] == "twr_gap_unhealable"
        assert note["unhealable"] == ["2026-09-22"]
        assert note["still_uncovered"] == ["2026-09-22", "2026-09-23"]
        assert "2026-09-22" in note["message"]

    def test_replay_that_does_not_cover_its_session_is_reported(self, tmp_path, monkeypatch):
        h = Harness(
            monkeypatch, tmp_path, ("20260923", "20260924"),
            uncovered=["2026-09-23"], after_replay=["2026-09-23"],
        )
        assert h.run() == 0
        state, note = h.beats[-1]
        assert state == "ok"
        assert note["class"] == "twr_gap_unhealed"
        assert note["still_uncovered"] == ["2026-09-23"]

    def test_replay_exception_does_not_fail_the_delivery_run(self, tmp_path, monkeypatch):
        h = Harness(
            monkeypatch, tmp_path, ("20260923", "20260924"),
            uncovered=["2026-09-23"], after_replay=["2026-09-23"],
        )

        def boom(_xml):
            raise RuntimeError("turso down")

        monkeypatch.setattr(pull, "_replay_twr_statement", boom)
        assert h.run() == 0
        state, note = h.beats[-1]
        assert state == "ok"
        assert note["failed"] == ["2026-09-23"]

    def test_spent_budget_defers_the_replay(self, tmp_path, monkeypatch):
        h = Harness(monkeypatch, tmp_path, ("20260923", "20260924"), uncovered=["2026-09-23"],
                    after_replay=["2026-09-23"])
        clock = iter([0.0] * 3 + [10_000.0] * 50)
        monkeypatch.setattr(pull.time, "monotonic", lambda: next(clock))
        assert h.run() == 0
        assert h.replayed == []
        state, note = h.beats[-1]
        assert note["deferred"] == ["2026-09-23"]


class TestDefaultReplayIsTwrOnly:
    def test_builds_from_a_private_file_and_removes_it(self, monkeypatch):
        calls: list[dict] = []

        def fake_build(**kwargs):
            path = Path(kwargs["from_file"])
            calls.append({**kwargs, "mode": path.stat().st_mode & 0o777, "text": path.read_text()})
            return {"status": "ok"}

        monkeypatch.setattr(twr, "build_and_persist", fake_build)
        import cash_flow_sync

        monkeypatch.setattr(cash_flow_sync, "main", lambda *_a, **_k: pytest.fail("cash writer replayed"))
        pull._replay_twr_statement(_statement_xml("20260922"))
        assert len(calls) == 1
        assert calls[0]["persist"] is True
        assert calls[0]["mode"] == 0o600
        assert 'toDate="20260922"' in calls[0]["text"]
        assert not Path(calls[0]["from_file"]).exists()


class TestUncoveredNavSessions:
    def _stub(self, monkeypatch, *, nav, covered, opening):
        def query(sql, params=()):
            if "MIN(report_date)" in sql:
                return [{"report_date": opening}]
            assert "nav_snapshots" in sql
            since, through = params
            return [{"report_date": d} for d in nav if since <= d <= through]

        monkeypatch.setattr(twr, "_query_turso_strict", query)
        monkeypatch.setattr(twr, "load_flow_coverage_dates", lambda: covered)

    def test_returns_stored_sessions_without_a_subperiod(self, monkeypatch):
        self._stub(
            monkeypatch,
            nav=["2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23"],
            covered={"2026-09-18", "2026-09-21"},
            opening="2025-01-02",
        )
        assert twr.load_uncovered_nav_sessions("2026-09-01", "2026-09-22") == ["2026-09-22"]

    def test_opening_valuation_is_never_a_gap(self, monkeypatch):
        self._stub(monkeypatch, nav=["2026-09-18", "2026-09-21"], covered={"2026-09-21"},
                   opening="2026-09-18")
        assert twr.load_uncovered_nav_sessions("2026-09-01", "2026-09-30") == []

    def test_unavailable_coverage_is_unknown_not_empty(self, monkeypatch):
        self._stub(monkeypatch, nav=["2026-09-22"], covered=None, opening="2025-01-02")
        assert twr.load_uncovered_nav_sessions("2026-09-01", "2026-09-30") is None

    def test_query_failure_is_unknown(self, monkeypatch):
        def boom(*_a, **_k):
            raise RuntimeError("hrana 502")

        monkeypatch.setattr(twr, "_query_turso_strict", boom)
        monkeypatch.setattr(twr, "load_flow_coverage_dates", lambda: set())
        assert twr.load_uncovered_nav_sessions("2026-09-01", "2026-09-30") is None

"""REL-257 / R-678: repeated Flex budget exhaustion is durable and visible.

Two budget stops that each apply a statement write a degraded heartbeat.
One stop stays distinguishable and healthy. A later complete run clears the
streak. Gap healing still receives the statements ingested before the stop.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_REAL_MONOTONIC = time.monotonic

SCRIPTS = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for entry in (str(SCRIPTS), str(TESTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import flex_sftp_pull as pull  # noqa: E402
from test_flex_sftp_pull import AFTER_FIRST_DELIVERY, FakeSftp  # noqa: E402
from test_rel146_flex_sftp_honesty import _config_lines, _write  # noqa: E402

DAYS = ("20260901", "20260902", "20260903")
NEWEST = "20260903"


def delivery_name(day: str) -> str:
    return f"U1234567.Equity_Summary_in_Base.{day}.{day}.xml.pgp"


def _statement_xml(day: str) -> bytes:
    text = (
        '<FlexQueryResponse><FlexStatements count="1">'
        f'<FlexStatement accountId="U1234567" fromDate="{day}" toDate="{day}" '
        'period="LastBusinessDay">'
        "<EquitySummaryInBase/><CashTransactions/><Transfers/>"
        "</FlexStatement></FlexStatements></FlexQueryResponse>"
    )
    return text.encode()


class BudgetSweep:
    """One inbox, one clock, repeated `pull.run` calls."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        self.monkeypatch = monkeypatch
        self.inbox = tmp_path / "inbox"
        self.inbox.mkdir()
        self.tmp_path = tmp_path
        self.beats: list[tuple] = []
        self.healed: list[list[str]] = []
        self.clock_calls = 0
        monkeypatch.setattr(
            pull, "_heartbeat", lambda state, error=None: self.beats.append((state, error))
        )
        monkeypatch.setattr(pull, "classify_flex_xml", lambda _xml: pull.ACTIVITY)
        monkeypatch.setattr(pull, "nightly_period_ok", lambda _xml: True)
        monkeypatch.setattr(pull, "_uncovered_nav_sessions", lambda *_args, **_kwargs: [])

        def record_heal(statements, **_kwargs):
            self.healed.append([statement.name for statement in statements])
            return None

        monkeypatch.setattr(pull, "heal_twr_coverage_gaps", record_heal)

    def stop_after_one_file(self) -> None:
        def monotonic() -> float:
            self.clock_calls += 1
            # Call 1 samples the deadline. Call 2 admits the newest file.
            # Every later sample is past the budget, so the tail is deferred.
            return 0.0 if self.clock_calls <= 2 else 10_000.0

        self.monkeypatch.setattr(pull.time, "monotonic", monotonic)
        self.monkeypatch.setattr(pull, "SWEEP_BUDGET_S", 5.0, raising=False)

    def allow_the_whole_batch(self) -> None:
        self.monkeypatch.setattr(pull.time, "monotonic", _REAL_MONOTONIC)
        self.monkeypatch.setattr(pull, "SWEEP_BUDGET_S", 120.0, raising=False)

    def run_once(self) -> int:
        self.beats.clear()
        self.healed.clear()
        self.clock_calls = 0
        files = {delivery_name(day): _statement_xml(day) for day in DAYS}
        return pull.run(
            config=_write(self.tmp_path, _config_lines()),
            inbox=self.inbox,
            runner=FakeSftp(files),
            decrypt=lambda data, **_kwargs: data.decode(),
            ingest=lambda _xml, source_path="", **_kwargs: {"ok": True, "outcome": "applied"},
            now=AFTER_FIRST_DELIVERY,
        )

    def last_beat(self) -> tuple:
        assert self.beats, "budget stop produced no heartbeat"
        return self.beats[-1]


def one_progress_stop(sweep: BudgetSweep) -> None:
    sweep.stop_after_one_file()
    assert sweep.run_once() == 0
    state, note = sweep.last_beat()
    assert state == "ok"
    assert note["class"] == "budget"
    assert note["consecutive_budget_runs"] == 1
    assert pull.read_budget_state(sweep.inbox)["consecutive_budget_runs"] == 1


def second_progress_stop(sweep: BudgetSweep) -> None:
    sweep.stop_after_one_file()
    assert sweep.run_once() == 0
    assert sweep.run_once() == 0
    state, note = sweep.last_beat()
    assert state == "degraded"
    assert note["class"] == "budget_degraded"
    assert note["consecutive_budget_runs"] == 2
    assert pull.read_budget_state(sweep.inbox)["consecutive_budget_runs"] == 2


def full_run_clears_the_streak(sweep: BudgetSweep) -> None:
    second_progress_stop(sweep)
    sweep.allow_the_whole_batch()
    assert sweep.run_once() == 0
    state, note = sweep.last_beat()
    assert state == "ok"
    assert note is None
    assert pull.read_budget_state(sweep.inbox)["consecutive_budget_runs"] == 0


def heal_receives_the_statement_applied_before_the_stop(sweep: BudgetSweep) -> None:
    sweep.stop_after_one_file()
    assert sweep.run_once() == 0
    assert sweep.healed == [[delivery_name(NEWEST)]]


class TestFlexBudgetState:
    def test_budget_state_path_exists(self):
        path = pull._budget_state_path(Path("/var/lib/radon/flex-inbox"))
        assert path.name == ".flex_budget_state.json"
        assert path.parent == Path("/var/lib/radon/flex-inbox")

    def test_read_budget_state_missing_file_returns_zero(self, tmp_path):
        assert pull.read_budget_state(tmp_path) == {"consecutive_budget_runs": 0}

    def test_write_and_read_budget_state(self, tmp_path):
        pull.write_budget_state(tmp_path, {"consecutive_budget_runs": 3})
        assert pull.read_budget_state(tmp_path) == {"consecutive_budget_runs": 3}

    def test_write_budget_state_atomic(self, tmp_path):
        pull.write_budget_state(tmp_path, {"consecutive_budget_runs": 2})
        assert list(tmp_path.glob("*.tmp*")) == []


class TestFlexBudgetHeartbeat:
    def test_first_budget_run_with_progress_writes_ok_budget(self, tmp_path, monkeypatch):
        one_progress_stop(BudgetSweep(monkeypatch, tmp_path))

    def test_consecutive_budget_runs_write_degraded(self, tmp_path, monkeypatch):
        second_progress_stop(BudgetSweep(monkeypatch, tmp_path))

    def test_complete_run_clears_budget_counter(self, tmp_path, monkeypatch):
        full_run_clears_the_streak(BudgetSweep(monkeypatch, tmp_path))


class TestFlexBudgetIntegration:
    def test_two_consecutive_budget_runs_produce_degraded(self, tmp_path, monkeypatch):
        second_progress_stop(BudgetSweep(monkeypatch, tmp_path))

    def test_single_partial_run_distinguishable_no_page(self, tmp_path, monkeypatch):
        one_progress_stop(BudgetSweep(monkeypatch, tmp_path))

    def test_complete_catchup_clears_degraded_state(self, tmp_path, monkeypatch):
        full_run_clears_the_streak(BudgetSweep(monkeypatch, tmp_path))

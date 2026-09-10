"""R-321 / REL-110: the mirrored-flow coverage bound must fail CLOSED.

`_query_turso` returns None on ANY exception and `load_flows_coverage_through`
returns None on an empty `twr_subperiods`, so `bound_observations_to_coverage`
saw a falsy `covered_through` and passed the FULL NAV series through. Because
`bounded` was then non-empty, the `mirrored_flows_cover_nothing_*` escape never
fired, every uncovered session was chained asserting an implicit flow of 0.0,
and a real deposit published as investment return — the exact class the
function's own docstring says it exists to prevent. The `FLOWS_SOURCE_MIRROR`
severity stays at `info` on the stated grounds that this bound does the
policing, so the one mechanism it defers to was the one failing open.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

import perf_twr_builder as ptb
# Re-export through the builder: importing `lib.twr_math` directly loads a
# second copy of the module and every `is` check against the enum fails.
from perf_twr_builder import FlowSet, FlowsStatus, NavObservation


def _obs(*pairs):
    return [NavObservation(date=d, nav=n) for d, n in pairs]


def _mirror_flows(by_date=None):
    return FlowSet(
        status=FlowsStatus.OK, by_date=by_date or {}, source="turso"
    )


class TestCoverageBoundFailsClosed:
    def test_query_error_yields_no_coverage_not_full_coverage(self, monkeypatch):
        """A raising `_query_turso` must not read as 'everything is covered'."""
        def _boom(_sql):
            raise RuntimeError("turso unreachable")

        monkeypatch.setattr(ptb, "_query_turso_strict", _boom)
        observations = _obs(("2026-08-01", 100000.0), ("2026-08-20", 180000.0))
        bounded, flows, warnings = ptb._apply_mirrored_flow_coverage(
            observations, _mirror_flows()
        )
        assert flows.status is FlowsStatus.FAILED, (
            "an errored coverage query means coverage is UNKNOWN, which is zero "
            f"evidence; got {flows.status} with {len(bounded)} sessions chained"
        )

    def test_empty_subperiods_yields_no_coverage_not_full_coverage(self, monkeypatch):
        monkeypatch.setattr(ptb, "_query_turso_strict", lambda _sql: [])
        observations = _obs(("2026-08-01", 100000.0), ("2026-08-20", 180000.0))
        _bounded, flows, _w = ptb._apply_mirrored_flow_coverage(
            observations, _mirror_flows()
        )
        assert flows.status is FlowsStatus.FAILED

    def test_query_failure_is_distinguishable_from_empty_coverage(self, monkeypatch):
        """`_query_turso` collapsed 'errored' and 'empty' to the same None."""
        def _boom(_sql):
            raise RuntimeError("turso unreachable")

        monkeypatch.setattr(ptb, "_query_turso_strict", _boom)
        errored, ok_errored = ptb.load_flows_coverage_state()
        monkeypatch.setattr(ptb, "_query_turso_strict", lambda _sql: [])
        empty, ok_empty = ptb.load_flows_coverage_state()
        assert errored is None and empty is None
        assert ok_errored is False, "a raising query must report query failure"
        assert ok_empty is True, "an empty table is a successful query"

    def test_query_failure_raises_its_own_warning(self, monkeypatch):
        def _boom(_sql):
            raise RuntimeError("turso unreachable")

        monkeypatch.setattr(ptb, "_query_turso_strict", _boom)
        _b, _f, warnings = ptb._apply_mirrored_flow_coverage(
            _obs(("2026-08-20", 180000.0)), _mirror_flows()
        )
        codes = {w.get("code") for w in warnings}
        assert "FLOWS_COVERAGE_QUERY_FAILED" in codes, (
            f"the coverage query failure must surface on its own; got {codes}"
        )

    def test_uncovered_sessions_are_dropped_when_coverage_is_partial(self, monkeypatch):
        """The covered path must keep working exactly as before."""
        monkeypatch.setattr(
            ptb, "_query_turso_strict", lambda _sql: [{"report_date": "2026-08-10"}]
        )
        observations = _obs(
            ("2026-08-01", 100000.0), ("2026-08-10", 110000.0), ("2026-08-20", 180000.0)
        )
        bounded, flows, warnings = ptb._apply_mirrored_flow_coverage(
            observations, _mirror_flows()
        )
        assert [o.date for o in bounded] == ["2026-08-01", "2026-08-10"]
        assert flows.status is FlowsStatus.OK
        assert warnings[0]["code"] == "FLOWS_SOURCE_MIRROR"
        assert warnings[0]["context"]["sessions_dropped"] == 1

    def test_live_flex_flows_are_still_unbounded(self, monkeypatch):
        """Only a MIRROR needs the bound; a live statement covers what it covers."""
        monkeypatch.setattr(ptb, "_query_turso_strict", lambda _sql: [])
        live = FlowSet(status=FlowsStatus.OK, by_date={}, source="flex")
        observations = _obs(("2026-08-01", 100000.0), ("2026-08-20", 180000.0))
        bounded, flows, warnings = ptb._apply_mirrored_flow_coverage(observations, live)
        assert list(bounded) == observations
        assert flows is live
        assert warnings == []


class TestUncoveredDepositIsNotInvestmentReturn:
    """The money case: an $80k deposit on an uncovered session."""

    def test_deposit_past_coverage_cannot_publish_as_return(self, monkeypatch):
        monkeypatch.setattr(ptb, "_query_turso_strict", lambda _sql: [])
        # NAV steps 100k -> 180k purely because $80k was deposited.
        with_deposit = _obs(("2026-08-01", 100000.0), ("2026-08-20", 180000.0))
        bounded, flows, _w = ptb._apply_mirrored_flow_coverage(
            with_deposit, _mirror_flows()
        )
        assert flows.status is FlowsStatus.FAILED, (
            "with zero verified coverage the mirror asserts an implicit flow of "
            "0.0 for 2026-08-20, publishing an $80k deposit as an 80% return"
        )
        assert not flows.by_date


def test_bound_observations_to_coverage_returns_nothing_for_unknown_coverage():
    """The primitive itself must not treat 'unknown' as 'all covered'."""
    observations = _obs(("2026-08-01", 100000.0), ("2026-08-20", 180000.0))
    assert list(ptb.bound_observations_to_coverage(observations, None)) == []
    assert list(ptb.bound_observations_to_coverage(observations, "")) == []

"""T-258: exactly ONE Flex SendRequest per run, asserted rather than commented.

CLAUDE.md makes this throttle-critical: `IB_FLEX_FLOWS_QUERY_ID` is
deliberately unset so `_flows_query_id()` falls back to the NAV id and
`resolve_flows` reuses the one document already fetched. A second request in
the same run against a token that has already spent its attempt is what
escalates a transient 1001 into the documented 24h-to-168h 1025 embargo.

Nothing tested the DEFAULT configuration's request count. The only mention in
`scripts/tests` was a hardcoded `query_ids = 1` inside a systemd-timeout
computation (`test_nested_deadlines.py`), which ASSUMES the property instead
of proving it, so a refactor making `_flows_query_id()` diverge from the NAV
id doubled the request rate with every test still green.

Scope note, corrected on landing. T-258 as filed says "no test references
`resolve_flows`, `_flows_query_id` or `IB_FLEX_FLOWS_QUERY_ID`". That is
wrong: the ROOT `tests/` tree (a second collection root the finding did not
search) has `tests/test_perf_twr_flex_single_request.py`, which references all
three and whose `test_a_distinct_flows_query_id_is_still_fetched` deliberately
pins the OPPOSITE contract — that a divergent id gets its own SendRequest,
because "the suppression is scoped to the id that failed, not to Flex itself".

So the divergent-id case is a DISPUTED product decision, not a coverage gap,
and this loop does not settle it. What is undisputed, and what this file
therefore asserts, is the property CLAUDE.md documents and production runs:
with `IB_FLEX_FLOWS_QUERY_ID` unset, a run makes exactly ONE Flex request.
The divergent-id behaviour is pinned below as-is, cross-referenced to the
older contract, so that whichever way an operator resolves it, the change is
deliberate and both files move together. Filed for the next audit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ACTIVITY_XML = FIXTURES / "cash_transactions_flex_ytd_detail_sample.xml"

NAV_QUERY_ID = "1442520"


@pytest.fixture
def flex_requests(monkeypatch, tmp_path) -> list[str]:
    """Every Flex SendRequest this run would make, in order."""
    import perf_twr_builder as builder

    monkeypatch.setenv("IB_FLEX_TOKEN", "test-token")
    monkeypatch.setenv("IB_FLEX_NAV_QUERY_ID", NAV_QUERY_ID)
    monkeypatch.delenv("IB_FLEX_FLOWS_QUERY_ID", raising=False)

    requested: list[str] = []
    xml = ACTIVITY_XML.read_text(encoding="utf-8")

    def _fetch(_token, query_id, *a, **k):
        requested.append(str(query_id))
        return xml

    monkeypatch.setattr(builder, "fetch_flex_xml", _fetch)
    monkeypatch.setattr(builder, "_NAV_CACHE_PATH", tmp_path / "nav_cache.json")
    monkeypatch.setattr(builder, "load_benchmark_closes", lambda *a, **k: {})
    monkeypatch.setattr(builder, "get_risk_free_rate", lambda **k: (0.0, "test"))
    return requested


class TestOneFlexRequestPerRun:
    def test_the_nav_document_is_reused_for_flows(self, flex_requests):
        import perf_twr_builder as builder

        builder.build_and_persist(persist=False, sendrequest=True)

        assert flex_requests == [NAV_QUERY_ID], (
            "more than one Flex SendRequest in a single run; the second attempt "
            f"is what turns a transient 1001 into a 1025 embargo. {flex_requests}"
        )

    def test_a_divergent_flows_query_id_costs_a_second_request(
        self, monkeypatch, flex_requests
    ):
        """DISPUTED — this pins today's behaviour, it does not endorse it.

        Setting `IB_FLEX_FLOWS_QUERY_ID` to an id the NAV leg did not fetch
        DOES issue a second SendRequest, doubling the request rate against a
        token CLAUDE.md describes as already carrying a 24h-to-168h embargo.
        `tests/test_perf_twr_flex_single_request.py::
        test_a_distinct_flows_query_id_is_still_fetched` asserts the same
        thing on purpose, reasoning that the 1001 suppression is scoped to the
        id that FAILED rather than to Flex as a whole.

        Whether the code should instead ignore a divergent id is an operator
        decision about a config knob, so it is pinned rather than changed.
        Making the change must red this test AND the older one together, which
        is the point: the two now name each other.
        """
        import perf_twr_builder as builder

        monkeypatch.setenv("IB_FLEX_FLOWS_QUERY_ID", "9999999")
        builder.build_and_persist(persist=False, sendrequest=True)

        assert flex_requests == [NAV_QUERY_ID, "9999999"], (
            "the divergent-id request count changed. That is a throttle-facing "
            "product decision, not a refactor: update this test, "
            "tests/test_perf_twr_flex_single_request.py and the "
            f"IB_FLEX_FLOWS_QUERY_ID note in CLAUDE.md together. {flex_requests}"
        )

    def test_a_spent_attempt_is_not_retried_under_the_same_query_id(
        self, monkeypatch, flex_requests
    ):
        """The undisputed half: an id that already failed is never re-requested.

        This is the actual 1001-to-1025 escalation path, and it holds
        independently of how the divergent-id question is settled.
        """
        import perf_twr_builder as builder

        monkeypatch.setattr(builder, "load_flows_from_turso", lambda *a, **k: {})
        document = builder.FlexDocument(
            query_id=NAV_QUERY_ID, error="1001 statement generation in progress"
        )

        flows, _coverage = builder.resolve_flows(document, allow_fetch=True)

        assert flex_requests == [], (
            "a second SendRequest went out after this run's attempt had already "
            f"failed — the exact repeat that escalates 1001 to 1025; {flex_requests}"
        )
        assert flows.status is builder.FlowsStatus.FAILED

"""T-258: exactly ONE Flex SendRequest per run, asserted rather than commented.

CLAUDE.md makes this throttle-critical: `IB_FLEX_FLOWS_QUERY_ID` is
deliberately unset so `_flows_query_id()` falls back to the NAV id and
`resolve_flows` reuses the one document already fetched. A second request in
the same run against a token that has already spent its attempt is what
escalates a transient 1001 into the documented 24h-to-168h 1025 embargo.

Nothing tested it. The only mention in the suite was a hardcoded
`query_ids = 1` inside a systemd-timeout computation
(`test_nested_deadlines.py`), which ASSUMES the property instead of proving
it — so setting `IB_FLEX_FLOWS_QUERY_ID`, or any refactor that made
`_flows_query_id()` diverge from the NAV id, doubled the request rate with
every test still green.
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

    def test_a_divergent_flows_query_id_does_not_add_a_second_request(
        self, monkeypatch, flex_requests
    ):
        """Setting IB_FLEX_FLOWS_QUERY_ID must not double the request rate."""
        import perf_twr_builder as builder

        monkeypatch.setenv("IB_FLEX_FLOWS_QUERY_ID", "9999999")
        builder.build_and_persist(persist=False, sendrequest=True)

        assert len(flex_requests) == 1, (
            "a second query id issued a second SendRequest in the same run; "
            "CLAUDE.md pins this at ONE request because the token is already "
            f"under a 24h-168h throttle embargo. {flex_requests}"
        )

    def test_a_spent_attempt_is_not_retried_under_a_divergent_flows_id(
        self, monkeypatch, flex_requests
    ):
        """The run's one attempt already failed; flows must not spend another."""
        import perf_twr_builder as builder

        monkeypatch.setenv("IB_FLEX_FLOWS_QUERY_ID", "9999999")
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

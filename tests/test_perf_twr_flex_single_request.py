"""A build must send at most ONE Flex request per query id, failure included.

Incident 2026-08-17/18. `/performance` showed DEGRADED MEASUREMENT with:

    NAV   fetch failed: code=1001 Statement could not be generated at this time.
    flows fetch failed: code=1025 Too many failed attempts. Please review your
                                  configuration.

Both lines carry the same journal second. Two SendRequests, same token, same
query id, back to back — and the second one is what turned a transient 1001
into a 1025 token lockout.

`get_nav_snapshots` documents the invariant it relies on: "The fetched
statement travels with the result so the flows query can be parsed out of the
document already in memory ... with `IB_FLEX_FLOWS_QUERY_ID` unset both queries
resolve to the same id." CLAUDE.md states it as "a run makes ONE Flex request".

That holds only on the success path. `_fetch_nav_document` swallows the failure
and returns None, which `resolve_flows` cannot distinguish from "NAV was never
attempted", so it re-requests the id that just failed. Doubling the failed
attempt rate against a token IBKR is already counting failures on is precisely
what code 1025 is measuring.

No network: `fetch_flex_xml` is monkeypatched on every path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.perf_twr_builder as builder  # noqa: E402
from scripts.lib.twr_math import FlowsStatus  # noqa: E402

NAV_QUERY_ID = "1442520"


@pytest.fixture
def flex_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IB_FLEX_TOKEN", "test-token")
    monkeypatch.setenv("IB_FLEX_NAV_QUERY_ID", NAV_QUERY_ID)
    monkeypatch.delenv("IB_FLEX_FLOWS_QUERY_ID", raising=False)


@pytest.fixture
def flex_down(monkeypatch: pytest.MonkeyPatch) -> list:
    """Record every SendRequest. The first fails 1001, as IBKR did."""
    attempts: list = []

    def _boom(_token, query_id, *_args, **_kwargs):
        attempts.append(query_id)
        raise RuntimeError(
            "Flex SendRequest failed code=1001: "
            "Statement could not be generated at this time. Please try again shortly."
        )

    monkeypatch.setattr(builder, "fetch_flex_xml", _boom)
    return attempts


class TestOneFlexRequestPerQueryIdPerRun:
    def test_failed_nav_fetch_is_not_retried_by_the_flows_fetch(
        self, flex_configured, flex_down, monkeypatch
    ):
        """The incident. One id, one request — even when that request failed."""
        monkeypatch.setattr(builder, "load_nav_from_disk", lambda: {"2026-08-14": 1.0})
        monkeypatch.setattr(builder, "load_flows_from_turso", lambda: None)

        resolution = builder.get_nav_snapshots(sendrequest=True)
        builder.resolve_flows(resolution.document)

        assert flex_down == [NAV_QUERY_ID], (
            "the flows leg re-requested the query id the NAV leg just failed; "
            f"sent {len(flex_down)} SendRequests for {set(flex_down)}"
        )

    def test_the_flows_failure_still_names_the_upstream_error(
        self, flex_configured, flex_down, monkeypatch
    ):
        """Skipping the second request must not hide why flows are missing."""
        monkeypatch.setattr(builder, "load_nav_from_disk", lambda: {"2026-08-14": 1.0})
        monkeypatch.setattr(builder, "load_flows_from_turso", lambda: None)

        resolution = builder.get_nav_snapshots(sendrequest=True)
        flows, _coverage = builder.resolve_flows(resolution.document)

        assert flows.status is FlowsStatus.FAILED
        assert "1001" in flows.reason

    def test_a_distinct_flows_query_id_is_still_fetched(
        self, flex_configured, flex_down, monkeypatch
    ):
        """The suppression is scoped to the id that failed, not to Flex itself.

        With `IB_FLEX_FLOWS_QUERY_ID` set to a different query, that query has
        its own statement and has not failed yet — it must still be attempted.
        """
        monkeypatch.setenv("IB_FLEX_FLOWS_QUERY_ID", "9999999")
        monkeypatch.setattr(builder, "load_nav_from_disk", lambda: {"2026-08-14": 1.0})
        monkeypatch.setattr(builder, "load_flows_from_turso", lambda: None)

        resolution = builder.get_nav_snapshots(sendrequest=True)
        builder.resolve_flows(resolution.document)

        assert flex_down == [NAV_QUERY_ID, "9999999"]

    def test_no_flex_document_at_all_still_fetches_flows(
        self, flex_configured, flex_down, monkeypatch
    ):
        """`resolve_flows()` called standalone has no prior failure to honour."""
        monkeypatch.setattr(builder, "load_flows_from_turso", lambda: None)

        builder.resolve_flows()

        assert flex_down == [NAV_QUERY_ID]


class TestMirroredFlowsAreBoundedByVerifiedCoverage:
    """Mirrored flows are facts only for the sessions a good run actually saw.

    `external_flows` stores only dates that HAD a flow, so its MAX(report_date)
    says nothing about how far coverage extends. Chaining a NAV date past the
    mirror's coverage silently asserts "no deposit that day" — an invented zero
    of exactly the kind that produced the +951% TWR.
    """

    def test_nav_beyond_mirror_coverage_is_dropped(self):
        observations = [
            builder.NavObservation("2026-08-12", 100.0),
            builder.NavObservation("2026-08-13", 110.0),
            builder.NavObservation("2026-08-14", 120.0),
        ]

        bounded = builder.bound_observations_to_coverage(observations, "2026-08-13")

        assert [o.date for o in bounded] == ["2026-08-12", "2026-08-13"]

    def test_full_coverage_keeps_every_observation(self):
        observations = [
            builder.NavObservation("2026-08-13", 110.0),
            builder.NavObservation("2026-08-14", 120.0),
        ]

        bounded = builder.bound_observations_to_coverage(observations, "2026-08-14")

        assert [o.date for o in bounded] == ["2026-08-13", "2026-08-14"]

    def test_no_coverage_marker_means_no_verified_coverage(self):
        """R-321: this case asserted the fail-open, and was rewritten.

        Its stated intent — "a live Flex fetch has no mirror bound to apply" —
        is right, but this was the wrong mechanism for it. A live fetch never
        reaches the bound at all: `_apply_mirrored_flow_coverage` returns early
        on `flows.source != "turso"`, which is what
        `scripts/tests/test_twr_coverage_bound.py::test_live_flex_flows_are_
        still_unbounded` now pins. Reaching the bound with NO coverage marker
        means the coverage query errored or `twr_subperiods` is empty — zero
        verified sessions, not all of them — and returning the full series
        there is the invented zero this class exists to prevent.
        """
        observations = [builder.NavObservation("2026-08-14", 120.0)]

        assert list(builder.bound_observations_to_coverage(observations, None)) == []

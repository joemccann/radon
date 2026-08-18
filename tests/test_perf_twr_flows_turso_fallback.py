"""A Flex flows outage must not blank the performance page.

Incident 2026-08-17. `/performance` oscillated between publishing +90.81% and
publishing nothing at all. Whenever a run reached Flex it wrote `status=ok`;
whenever Flex errored it wrote `status=degraded` with `flows_status=failed`,
and the render layer correctly suppressed every gated metric — TWR, Max DD,
Sharpe, the equity curve. Same account, same NAV, five minutes apart.

The asymmetry: NAV degrades through a ladder (`flex -> disk_cache -> turso`)
but flows had NO fallback. One `fetch_flex_xml` exception went straight to
`FlowSet.failed`, which by design forbids publishing a TWR.

That is the right rule for flows we have never seen. It is the wrong rule when
the builder already mirrored a good flow set into Turso `external_flows` on a
previous run — those flows are facts about closed sessions and do not change
when IBKR's statement generator is unavailable. Serving last-known-good flows
with an honest `source` beats blanking a page that was correct minutes ago.

Observed codes driving this: 1001 ("Statement could not be generated at this
time") and 1025 ("Too many failed attempts. Please review your configuration").

No network, no Turso: the loader is monkeypatched on every path.
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

# Two real external flows already mirrored to Turso by an earlier good run.
MIRRORED_FLOWS = {"2026-01-13": 80_007.13, "2026-02-06": 655_497.16}


@pytest.fixture
def flex_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IB_FLEX_TOKEN", "test-token")
    monkeypatch.setenv("IB_FLEX_NAV_QUERY_ID", "1442520")


def _flex_raises(code: str):
    def _boom(*_args, **_kwargs):
        raise RuntimeError(
            f"Flex SendRequest failed code={code}: synthetic outage for test"
        )

    return _boom


class TestFlowsFallBackToTursoOnFlexOutage:
    @pytest.mark.parametrize("code", ["1001", "1025"])
    def test_mirrored_flows_are_served_when_flex_errors(
        self, flex_configured, monkeypatch, code
    ):
        """The exact incident: Flex is down, Turso has the flows, publish them."""
        monkeypatch.setattr(builder, "fetch_flex_xml", _flex_raises(code))
        monkeypatch.setattr(builder, "load_flows_from_turso", lambda: dict(MIRRORED_FLOWS))

        flows, _coverage = builder.resolve_flows()

        assert flows.status is not FlowsStatus.FAILED, (
            "a Flex outage with good mirrored flows must not suppress TWR"
        )
        assert dict(flows.by_date) == MIRRORED_FLOWS
        assert flows.source == "turso"

    def test_source_is_honest_so_the_page_can_say_where_flows_came_from(
        self, flex_configured, monkeypatch
    ):
        monkeypatch.setattr(builder, "fetch_flex_xml", _flex_raises("1025"))
        monkeypatch.setattr(builder, "load_flows_from_turso", lambda: dict(MIRRORED_FLOWS))

        flows, _coverage = builder.resolve_flows()

        assert flows.source == "turso", "never claim a live Flex fetch"

    def test_still_fails_when_turso_has_nothing(self, flex_configured, monkeypatch):
        """No fallback data = the original rule. Never invent a zero flow set:
        treating 'unknown' as 'no deposits' is what produced +951% TWR."""
        monkeypatch.setattr(builder, "fetch_flex_xml", _flex_raises("1001"))
        monkeypatch.setattr(builder, "load_flows_from_turso", lambda: None)

        flows, _coverage = builder.resolve_flows()

        assert flows.status is FlowsStatus.FAILED
        assert "1001" in flows.reason

    def test_empty_turso_table_is_not_a_verified_zero(self, flex_configured, monkeypatch):
        """An empty dict is absence of evidence, not evidence of no flows."""
        monkeypatch.setattr(builder, "fetch_flex_xml", _flex_raises("1025"))
        monkeypatch.setattr(builder, "load_flows_from_turso", lambda: {})

        flows, _coverage = builder.resolve_flows()

        assert flows.status is FlowsStatus.FAILED

    def test_a_live_flex_success_never_consults_turso(self, flex_configured, monkeypatch):
        """The fallback must not shadow a healthy fetch."""
        called = {"turso": False}

        def _turso():
            called["turso"] = True
            return dict(MIRRORED_FLOWS)

        monkeypatch.setattr(builder, "fetch_flex_xml", lambda *a, **k: "<FlexQueryResponse/>")
        monkeypatch.setattr(builder, "load_flows_from_turso", _turso)
        monkeypatch.setattr(
            builder, "parse_flows", lambda _xml: builder.FlowSet.empty_verified("flex")
        )

        flows, _coverage = builder.resolve_flows()

        assert called["turso"] is False
        assert flows.source == "flex"

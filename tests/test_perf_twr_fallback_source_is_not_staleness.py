"""A fallback SOURCE is provenance, not staleness.

Incident 2026-08-17, final act. After giving flows a Turso fallback, the
builder produced `flows=ok` but `status=stale`, and the render layer gates the
TWR on `stale` exactly as it gates on `degraded` — so the page was still blank.

`_freshness_warnings` emitted `NAV_SOURCE_DISK` at severity `warn`, and
`_SEVERITY_FLOOR` maps `warn -> stale`. So the payload was floored to "stale"
because the NAV *came from a cache*, even though the cached NAV was 2026-08-14
— which is precisely what a live Flex fetch would have returned at that moment,
IBKR being T+1.

Two independent things were being conflated:

  * WHERE the NAV came from (flex / disk_cache / turso) -- provenance
  * HOW OLD the NAV is (`behind` vs NAV_STALENESS_BUDGET_SESSIONS) -- freshness

Only the second should gate a number. Age is already policed twice: the builder
refuses a disk cache older than `_NAV_DISK_MAX_AGE_DAYS`, and the read layer
re-derives `sessionsBehind` from `nav_as_of` against the current clock. A
source-based floor adds nothing and suppresses correct data.

So: within the staleness budget, a fallback source is `info` (surface the
provenance, publish the number). Past the budget it stays `error`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.lib import twr_gates  # noqa: E402
from scripts.perf_twr_builder import _freshness_warnings  # noqa: E402

BUDGET = twr_gates.NAV_STALENESS_BUDGET_SESSIONS
NAV_AS_OF = "2026-08-14"


def _by_code(warnings, code):
    for w in warnings:
        if w.get("code") == code:
            return w
    return None


class TestFallbackSourceWithinBudget:
    @pytest.mark.parametrize("nav_source", ["disk_cache", "turso"])
    def test_source_warning_is_info_not_warn(self, nav_source):
        """The exact production case: NAV one session behind, served from a
        fallback. `warn` floors the payload to `stale`, which blanks the page."""
        warnings = _freshness_warnings(nav_source, behind=1, nav_as_of=NAV_AS_OF)

        w = _by_code(warnings, f"NAV_SOURCE_{'DISK' if nav_source == 'disk_cache' else 'TURSO'}")
        assert w is not None, "provenance must still be surfaced"
        assert w["severity"] == "info", (
            "a fallback source inside the freshness budget is provenance, "
            "not a reason to suppress every metric"
        )

    def test_provenance_is_still_reported(self):
        """Downgrading severity must not hide WHERE the NAV came from."""
        warnings = _freshness_warnings("disk_cache", behind=1, nav_as_of=NAV_AS_OF)
        w = _by_code(warnings, "NAV_SOURCE_DISK")
        assert w["context"]["nav_source"] == "disk_cache"
        assert "disk_cache" in w["message"]

    def test_at_the_budget_edge_it_is_still_info(self):
        warnings = _freshness_warnings("disk_cache", behind=BUDGET, nav_as_of=NAV_AS_OF)
        assert _by_code(warnings, "NAV_SOURCE_DISK")["severity"] == "info"


class TestGenuinelyStaleStillEscalates:
    def test_past_the_budget_the_source_warning_is_an_error(self):
        """Old data is a real defect and must keep gating."""
        warnings = _freshness_warnings("disk_cache", behind=BUDGET + 1, nav_as_of=NAV_AS_OF)
        assert _by_code(warnings, "NAV_SOURCE_DISK")["severity"] == "error"

    def test_a_live_flex_fetch_emits_no_source_warning(self):
        warnings = _freshness_warnings("flex_live", behind=1, nav_as_of=NAV_AS_OF)
        assert _by_code(warnings, "NAV_SOURCE_DISK") is None
        assert _by_code(warnings, "NAV_SOURCE_TURSO") is None

"""REL-052 group A / R-132, R-133, R-134 (all P2) — Flex callers outside the
embargo, and a 1025 taxonomy quadruplicated into disagreement.

R-132: `portfolio_performance.py` has three raw Flex paths with no
`flex_embargo` import and no 1025 detection, and its poll loops `continue`
whenever `<FlexStatements` is absent — exactly what a 1025 error envelope
looks like. One invocation is 1 SendRequest plus up to 30 GetStatements
against a locked token, each extending it.
`_throttle_backoff.py` names this file as the known uncontrolled consumer.

R-133: the SECOND `FlexQueryFetcher`, in `blotter_service.py`, has no
embargo check and no 1025 handling, and treats the presence of
`FlexStatementResponse` — the ERROR envelope — as READY, so a 1025 body
parses to zero `.//Trade` nodes and is returned as a successful empty
execution list.

R-134: the taxonomy is duplicated four ways and the shared `is_lockout_code`
has zero production callers. `LOCKOUT_DAYS` is duplicated as
`LOCKOUT_EMBARGO_DAYS`, and `server.py`'s `is_throttled` still sets on
1001|1018|1019 (only 1018 is a rate limit, per 436dcdc1) and omits 1025 — so
the 5-minute soft 1001 renders the amber "don't manually retry" lozenge while
the 7-day lockout falls through to generic red.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils import flex_embargo


class TestOneLockoutTaxonomy:
    def test_lockout_days_is_single_sourced(self):
        from monitor_daemon.handlers import cash_flow_sync as handler

        assert handler.LOCKOUT_EMBARGO_DAYS == flex_embargo.LOCKOUT_DAYS
        source = (REPO / "scripts" / "monitor_daemon" / "handlers" / "cash_flow_sync.py").read_text()
        assert "LOCKOUT_EMBARGO_DAYS = 7" not in source, (
            "the 7-day window is still typed out a second time"
        )

    def test_is_lockout_code_has_production_callers(self):
        import subprocess

        hits = subprocess.run(
            ["grep", "-rln", "is_lockout_code", "scripts"],
            cwd=REPO, capture_output=True, text=True,
        ).stdout.split()
        production = [
            h for h in hits
            if "/tests/" not in h and not h.endswith("utils/flex_embargo.py")
        ]
        assert production, "the shared classifier still has zero production callers"

    @pytest.mark.parametrize("code", ["1025", " 1025 "])
    def test_the_lockout_code_is_recognised(self, code):
        assert flex_embargo.is_lockout_code(code) is True

    @pytest.mark.parametrize("code", ["1001", "1018", "1019", None, ""])
    def test_soft_and_throttle_codes_are_not_lockouts(self, code):
        assert flex_embargo.is_lockout_code(code) is False


class TestThrottleClassification:
    def _classify(self, message: str) -> dict:
        from api.server import _classify_cash_flow_error

        return _classify_cash_flow_error(message)

    def test_only_1018_is_a_rate_limit(self):
        assert self._classify("Flex throttle (code 1018): rate limited")["is_throttled"] is True
        # 436dcdc1: 1001 is "could not be generated" and 1019 is "in
        # progress". Neither is a rate limit, and calling them one renders
        # the amber "don't manually retry" lozenge for a 5-minute soft wait.
        assert self._classify("Flex error (code 1001): could not be generated")["is_throttled"] is False
        assert self._classify("Flex error (code 1019): in progress")["is_throttled"] is False

    def test_a_lockout_is_surfaced_distinctly(self):
        verdict = self._classify("Flex lockout (code 1025): Too many failed attempts")
        assert verdict["is_lockout"] is True
        assert verdict["is_throttled"] is False
        assert "do not retry" in verdict["error_summary"].lower()


class TestBlotterServiceRespectsTheEmbargo:
    def test_the_second_fetcher_checks_the_embargo(self):
        source = (REPO / "scripts" / "trade_blotter" / "blotter_service.py").read_text()
        assert "raise_if_blocked" in source, (
            "the second FlexQueryFetcher SendRequests straight into a live lockout"
        )
        assert "is_lockout_code" in source or "record_lockout" in source

    def test_the_error_envelope_is_not_treated_as_ready(self):
        source = (REPO / "scripts" / "trade_blotter" / "blotter_service.py").read_text()
        assert "<FlexStatements" in source, (
            "readiness is still keyed on FlexStatementResponse, which is the "
            "ERROR envelope — a 1025 body parses to zero Trade nodes and is "
            "returned as a successful empty execution list"
        )


class TestPortfolioPerformanceRespectsTheEmbargo:
    def test_it_imports_the_shared_embargo(self):
        source = (REPO / "scripts" / "portfolio_performance.py").read_text()
        assert "flex_embargo" in source, (
            "three raw Flex paths, no embargo check: one CLI invocation is "
            "1 SendRequest + up to 30 GetStatements against a locked token"
        )
        assert "raise_if_blocked" in source

    def test_it_detects_a_lockout_envelope(self):
        source = (REPO / "scripts" / "portfolio_performance.py").read_text()
        assert "is_lockout_code" in source

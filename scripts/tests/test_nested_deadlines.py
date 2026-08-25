"""REL-046 / R-093 + R-103 (both P1) — deadlines that do not nest.

R-093: `run_signals_refresh.sh` bounds each curl at `SCAN_TIMEOUT` but the
bounded 502/503 retry ladder is charged nowhere. Worst case per scan is
`3 x 490 + 2 x 8 = 1486s`, two scans `2972s`, against `TimeoutStartSec=1050`.
Even the ordinary shed shape (503 fast, 503 fast, third attempt hangs to the
full 490s, twice) lands at ~1012s plus env load plus the market_calendar
subprocess — inside the noise of 1050. systemd SIGTERMs the unit group
mid-write: `Result=signal`, a partial snapshot, a P1 page outside a deploy
window.

R-103: `poll_delays()` sizes the schedule to 420s of SLEEP; the
`urlopen(..., timeout=30)` in the same loop body is not charged. Worst case
cash-flow-sync is `30 x 30 + 420 = 1320s` against a 480s SIGKILL, and even a
benign 3s GetStatement latency puts the run at ~510s. Each kill lands
mid-poll: one SendRequest spent, zero rows written, no state recorded.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

WRAPPER = REPO / "scripts" / "run_signals_refresh.sh"
SIGNALS_UNIT = REPO / "cloud" / "services" / "radon-signals-refresh.service"
TWR_UNIT = REPO / "cloud" / "services" / "radon-perf-twr.service"


def _shell_default(text: str, name: str) -> int:
    match = re.search(rf'{name}="\$\{{[A-Z_]+:-(\d+)\}}"', text)
    assert match, f"{name} default not found"
    return int(match.group(1))


def _unit_value(path: Path, key: str) -> int:
    match = re.search(rf"^{key}=(\d+)$", path.read_text(), re.MULTILINE)
    assert match, f"{key} not found in {path.name}"
    return int(match.group(1))


class TestSignalsRefreshDeadlineNests:
    def test_the_retry_ladder_is_charged_against_one_per_scan_deadline(self):
        """A single deadline per scan, so retries cannot multiply the cap."""
        text = WRAPPER.read_text()
        assert "SCAN_DEADLINE" in text, (
            "the retry ladder is still unbounded in wall time: each attempt "
            "gets a fresh -m and the sleeps are charged nowhere"
        )

    def test_unit_timeout_covers_two_full_scan_deadlines_plus_the_probe(self):
        text = WRAPPER.read_text()
        scan_timeout = _shell_default(text, "SCAN_TIMEOUT")
        probe = _shell_default(text, "PROBE_BUDGET_SECS")
        env = re.search(
            r"Environment=RADON_SIGNALS_SCAN_TIMEOUT=(\d+)", SIGNALS_UNIT.read_text()
        )
        assert env, "the unit no longer overrides the scan timeout"
        installed_scan_timeout = int(env.group(1))
        required = 2 * installed_scan_timeout + probe
        assert _unit_value(SIGNALS_UNIT, "TimeoutStartSec") >= required, (
            f"TimeoutStartSec must cover two {installed_scan_timeout}s scan "
            f"deadlines plus a {probe}s probe budget = {required}s"
        )
        assert scan_timeout <= installed_scan_timeout


class TestFlexPollBudgetIsWallClock:
    def test_http_latency_is_charged_against_the_budget(self, monkeypatch):
        """R-103's injection: urlopen sleeps 4s and returns not-ready. The
        run must finish inside `budget_seconds`, not `budget + 30 * n`."""
        import cash_flow_sync as cfs

        clock = {"now": 0.0}
        monkeypatch.setattr(cfs.time, "monotonic", lambda: clock["now"])
        monkeypatch.setattr(cfs.time, "sleep", lambda s: clock.__setitem__("now", clock["now"] + s))

        polls = {"n": 0}

        class _Resp:
            def __init__(self, body):
                self._body = body

            def read(self):
                return self._body.encode()

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        def _fake_urlopen(url, timeout=30):
            clock["now"] += 4.0  # HTTP latency the old budget never charged
            if "SendRequest" in url or "Send" in url:
                return _Resp("<FlexStatementResponse><Status>Success</Status>"
                             "<ReferenceCode>1234</ReferenceCode></FlexStatementResponse>")
            polls["n"] += 1
            return _Resp("<FlexStatementResponse><Status>Warn</Status>"
                         "<ErrorCode>1019</ErrorCode>"
                         "<ErrorMessage>in progress</ErrorMessage></FlexStatementResponse>")

        monkeypatch.setattr(cfs, "urlopen", _fake_urlopen)
        monkeypatch.setattr(cfs, "_raise_if_token_locked", lambda: None)

        budget = 60.0
        with pytest.raises(Exception):
            cfs.fetch_statement_xml("tok", "1", budget_seconds=budget)

        assert clock["now"] <= budget + 10.0, (
            f"poll loop ran {clock['now']:.0f}s against a {budget:.0f}s budget — "
            "HTTP time is still uncharged"
        )
        assert polls["n"] > 0


class TestTwrUnitBudgetMatchesTheBuilder:
    def test_unit_timeout_is_derived_from_the_poll_budget(self):
        builder = (REPO / "scripts" / "perf_twr_builder.py").read_text()
        match = re.search(r"FLEX_POLL_BUDGET_SECONDS\s*=\s*([\d.]+)", builder)
        assert match, "perf_twr_builder no longer names its poll budget"
        budget = float(match.group(1))
        query_ids = 1  # resolve_flows reuses the single NAV document
        required = budget * query_ids
        assert _unit_value(TWR_UNIT, "TimeoutStartSec") >= required, (
            f"TimeoutStartSec must cover the {budget:.0f}s poll budget"
        )

    def test_the_unit_comment_does_not_describe_a_dead_budget(self):
        text = TWR_UNIT.read_text()
        assert "30 x 3s" not in text, (
            "the unit still documents a 90s poll budget the builder abandoned"
        )

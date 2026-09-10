"""R-354's per-ticker dedupe was declared and never wired.

`_FLOW_REPORT_INFLIGHT` and `_FLOW_REPORT_INFLIGHT_LOCK` were added with the
comment "One in-flight scan per ticker. Nothing deduped concurrent requests,
so N browser tabs on the same symbol each claimed a slot" — and then no code
read or wrote either. Every tab still claimed its own general-lane slot, so
the operator's own duplicates were part of the saturation the shed retry then
had to wait out. /flow-analysis/AMZN served a Jun 16 report on 2026-08-28.

The shield matters as much as the dedupe: Caddy bounds the app upstream at a
30s `response_header_timeout`, well under an 81s AMZN scan, so the browser
request is cut long before the scan lands. Detaching the scan from any one
caller is what lets the cache write finish anyway, so the next page load is
fresh instead of replaying the same doomed scan.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(API_DIR.parent))

import server  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_inflight():
    server._FLOW_REPORT_INFLIGHT.clear()
    server._FLOW_REPORT_INFLIGHT_LOCK = None
    yield
    server._FLOW_REPORT_INFLIGHT.clear()
    server._FLOW_REPORT_INFLIGHT_LOCK = None


def _stub_scan(runs: list[str], *, delay: float = 0.05):
    async def _scan(ticker: str):
        runs.append(ticker)
        await asyncio.sleep(delay)
        return {"ticker": ticker, "fetched_at": "now"}

    return _scan


class TestOneScanPerTicker:
    def test_concurrent_requests_for_one_ticker_share_a_single_scan(self):
        runs: list[str] = []
        scan = _stub_scan(runs)

        async def _exercise():
            return await asyncio.gather(*[
                server._scan_once_per_ticker("AMZN", lambda: scan("AMZN"))
                for _ in range(4)
            ])

        results = asyncio.run(_exercise())
        assert runs == ["AMZN"], f"each tab claimed its own lane slot: {runs}"
        assert all(r["ticker"] == "AMZN" for r in results)

    def test_different_tickers_are_not_collapsed(self):
        runs: list[str] = []
        scan = _stub_scan(runs)

        async def _exercise():
            return await asyncio.gather(
                server._scan_once_per_ticker("AMZN", lambda: scan("AMZN")),
                server._scan_once_per_ticker("NVDA", lambda: scan("NVDA")),
            )

        asyncio.run(_exercise())
        assert sorted(runs) == ["AMZN", "NVDA"]

    def test_a_later_request_rescans_once_the_first_has_finished(self):
        runs: list[str] = []
        scan = _stub_scan(runs, delay=0.0)

        async def _exercise():
            await server._scan_once_per_ticker("AMZN", lambda: scan("AMZN"))
            await server._scan_once_per_ticker("AMZN", lambda: scan("AMZN"))

        asyncio.run(_exercise())
        assert runs == ["AMZN", "AMZN"], "a finished scan must not pin the cache forever"


class TestTheScanOutlivesTheCaller:
    def test_a_cancelled_caller_does_not_kill_the_scan(self):
        """Caddy cuts the app upstream at 30s; an 81s scan has to survive it."""
        finished: list[str] = []

        async def _scan():
            await asyncio.sleep(0.05)
            finished.append("AMZN")
            return {"ticker": "AMZN"}

        async def _exercise():
            waiter = asyncio.create_task(
                server._scan_once_per_ticker("AMZN", _scan)
            )
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            await asyncio.sleep(0.2)

        asyncio.run(_exercise())
        assert finished == ["AMZN"], (
            "the scan died with the request that started it, so the cache write "
            "never landed and the next visit replayed the same doomed scan"
        )

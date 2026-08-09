"""REL-013 — the index-chain IB fetch must be bounded
(RELIABILITY_AUDIT.md R-014).

This was the one unwrapped IB await in the FastAPI process: a wedged
gateway held the pool's data-role lock forever and stranded a worker
thread per exchange attempt.
"""

from __future__ import annotations

import asyncio
import re
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SERVER_PATH = SCRIPTS_DIR / "api" / "server.py"


class TestBoundedChainFetch:
    def test_hanging_fetch_times_out_promptly(self, monkeypatch):
        from scripts.api import server

        release = threading.Event()

        def hangs(*args):
            release.wait(5.0)
            return {}

        monkeypatch.setattr(server, "_fetch_ib_index_option_chain", hangs)
        monkeypatch.setattr(server, "_CHAIN_FETCH_TIMEOUT_SECS", 0.2)

        async def run():
            started = time.monotonic()
            with pytest.raises(asyncio.TimeoutError):
                await server._bounded_chain_fetch(object(), "NDX", "NASDAQ", "IND")
            return time.monotonic() - started

        elapsed = asyncio.run(run())
        release.set()
        assert elapsed < 2.0  # bounded, not the 5s hang

    def test_call_site_uses_bounded_helper(self):
        """No bare to_thread(_fetch_ib_index_option_chain …) may remain —
        the wait_for bound must not be silently dropped in a refactor."""
        source = SERVER_PATH.read_text()
        bare = re.findall(
            r"asyncio\.to_thread\(\s*_fetch_ib_index_option_chain", source
        )
        # Exactly one occurrence, inside _bounded_chain_fetch itself.
        assert len(bare) == 1
        helper_block = source.split("def _bounded_chain_fetch", 1)[-1][:800]
        assert "wait_for" in helper_block
        assert "_fetch_ib_index_option_chain" in helper_block

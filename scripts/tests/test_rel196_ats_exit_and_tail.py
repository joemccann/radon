"""REL-196 (R-528, R-558): the ATS sweep actually exits after a tarpit, and a
budget/timeout-dropped tail is visible instead of silently vanishing."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fetch_equibles_ats_venue_share as ats  # noqa: E402


class TestBoundedFetchCannotBlockExit:
    def test_a_hung_worker_does_not_block_interpreter_exit(self, tmp_path):
        """R-528, lead-executed repro: ThreadPoolExecutor registers an atexit
        join, so an abandoned tarpitted worker hung `threading._shutdown`
        until systemd's TimeoutStartSec SIGTERM. A daemon thread cannot."""
        script = tmp_path / "repro.py"
        script.write_text(
            f"""
import sys, time
sys.path.insert(0, {str(SCRIPTS)!r})
import fetch_equibles_ats_venue_share as ats

def hang(*a, **k):
    time.sleep(600)

ats._fetch_ticker = hang
try:
    ats._fetch_ticker_bounded(None, "XX", "2026-01-01", "2026-01-08", timeout_s=0.2)
except TimeoutError:
    pass
print("exiting now")
"""
        )
        start = time.monotonic()
        proc = subprocess.run(
            [sys.executable, str(script)], capture_output=True, text=True, timeout=30
        )
        elapsed = time.monotonic() - start
        assert "exiting now" in proc.stdout
        assert elapsed < 10, (
            f"interpreter exit blocked {elapsed:.1f}s on the abandoned worker"
        )

    def test_timeout_raises_and_a_fast_worker_returns(self, monkeypatch):
        monkeypatch.setattr(ats, "_fetch_ticker", lambda *a: time.sleep(5))
        with pytest.raises(TimeoutError, match="wall-clock"):
            ats._fetch_ticker_bounded(None, "XX", "a", "b", timeout_s=0.05)
        monkeypatch.setattr(ats, "_fetch_ticker", lambda *a: [{"x": 1}])
        assert ats._fetch_ticker_bounded(None, "XX", "a", "b", timeout_s=5) == [{"x": 1}]

    def test_no_threadpool_on_the_timeout_path(self):
        src = "\n".join(
            line
            for line in (SCRIPTS / "fetch_equibles_ats_venue_share.py")
            .read_text()
            .splitlines()
            if not line.lstrip().startswith("#")
        )
        body = src[src.index("def _fetch_ticker_bounded"):]
        body = body[: body.index("\ndef ")]
        assert "ThreadPoolExecutor" not in body
        assert "daemon=True" in body


class TestDroppedTailIsVisible:
    @pytest.fixture()
    def health_rows(self, monkeypatch):
        rows: list[dict] = []
        monkeypatch.setattr(
            ats, "_record_health",
            lambda state, scan_time, error=None: rows.append(
                {"state": state, "error": error}
            ),
        )
        return rows

    def _run_partial(self, monkeypatch, tmp_path, health_rows, prior=None):
        universe = [f"T{i}" for i in range(10)]
        monkeypatch.setattr(ats, "watchlist_tickers", lambda: universe)
        monkeypatch.setattr(ats, "_write_db_cache", lambda payload, st: None)
        written: dict = {}
        monkeypatch.setattr(
            ats, "_write_json_cache", lambda payload: written.update(payload)
        )
        if prior is not None:
            monkeypatch.setattr(ats, "_read_prior_payload", lambda: prior, raising=False)

        series = {"week_start_date": "2026-08-31", "off_exchange_share": 0.4}
        calls = {"n": 0}

        def fake_bounded(client, ticker, s, e, timeout_s):
            calls["n"] += 1
            if calls["n"] > 7:
                raise TimeoutError(f"{ticker}: fetch exceeded {timeout_s:.0f}s wall-clock")
            return [dict(series)]

        monkeypatch.setattr(ats, "_fetch_ticker_bounded", fake_bounded)
        payload = ats.run(client=object(), tickers=universe)
        return payload, written

    def test_a_timeout_deferred_tail_records_an_error_state(
        self, monkeypatch, tmp_path, health_rows
    ):
        """R-558: 7/10 covered is over the 60% floor, but the dropped tail
        must land in a state the watchdog error bucket can see."""
        payload, _written = self._run_partial(monkeypatch, tmp_path, health_rows)
        assert health_rows, "no health row recorded"
        last = health_rows[-1]
        assert last["state"] == "error", (
            f"a dropped tail was recorded {last['state']!r}; the watchdog "
            "error bucket only fires on state == 'error'"
        )
        message = json.dumps(last["error"] or {})
        assert "T8" in message or "dropped" in message.lower()

    def test_the_prior_snapshot_tail_is_carried_forward(
        self, monkeypatch, tmp_path, health_rows
    ):
        prior = {
            "series": {
                "T8": [{"week_start_date": "2026-08-24", "off_exchange_share": 0.5}],
                "T9": [{"week_start_date": "2026-08-24", "off_exchange_share": 0.6}],
            },
        }
        payload, _written = self._run_partial(
            monkeypatch, tmp_path, health_rows, prior=prior
        )
        assert "T8" in payload["series"] and "T9" in payload["series"], (
            "the covered-only payload replaced the fuller prior snapshot"
        )
        assert set(payload.get("carried_forward", [])) == {"T8", "T9"}

    def test_a_full_batch_still_records_ok(self, monkeypatch, tmp_path, health_rows):
        universe = [f"T{i}" for i in range(4)]
        monkeypatch.setattr(ats, "watchlist_tickers", lambda: universe)
        monkeypatch.setattr(ats, "_write_db_cache", lambda payload, st: None)
        monkeypatch.setattr(ats, "_write_json_cache", lambda payload: None)
        monkeypatch.setattr(
            ats, "_fetch_ticker_bounded",
            lambda client, t, s, e, timeout_s: [
                {"week_start_date": "2026-08-31", "off_exchange_share": 0.4}
            ],
        )
        ats.run(client=object(), tickers=universe)
        assert health_rows and health_rows[-1]["state"] == "ok"

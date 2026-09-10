"""REL-200 (R-561, R-566, R-567): every broker-remote failure is structured
and the stop/start cooldown survives a daemon restart."""
from __future__ import annotations

import http.client
import importlib
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class TestNonHttpGarbageIsStructured:
    def test_badstatusline_is_the_structured_minus_one(self, monkeypatch):
        """R-566: a TLS-valid non-HTTP reply raised BadStatusLine through
        both layers — admin actions 500'd raw."""
        from api import services

        def boom(*a, **k):
            raise http.client.BadStatusLine("\x00\x01garbage")

        monkeypatch.setenv("RADON_IB_REMOTE_URL", "https://10.0.0.4:8340")
        monkeypatch.setattr(services.urllib.request, "urlopen", boom)
        monkeypatch.setattr(services, "_remote_ssl_context", lambda: None)
        status, payload = services._remote_http("status", 2.0)
        assert status == -1
        assert payload["ok"] is False
        assert "garbage" in payload["detail"] or "BadStatusLine" in payload["detail"]


class TestWedgedProbeIsAbandoned:
    def test_a_stale_inflight_probe_is_dropped_and_replaced(self, monkeypatch):
        """R-561: `_remote_status_future` reused a never-finishing future
        forever; one slow-drip connection wedged the status probe for the
        process lifetime."""
        from concurrent.futures import Future

        from api import services

        services._reset_remote_status_cache()
        wedged: Future = Future()  # never resolves
        services._remote_status_inflight = wedged
        # Age the probe past the abandonment dwell.
        services._remote_status_started = (
            time.monotonic() - 10 * services.REMOTE_STATUS_TIMEOUT_S
        )
        monkeypatch.setattr(
            services, "_remote_http", lambda verb, timeout: (200, {"ok": True})
        )
        fresh = services._remote_status_future()
        assert fresh is not wedged, "the wedged probe was reused"
        services._reset_remote_status_cache()

    def test_a_recent_inflight_probe_is_still_coalesced(self, monkeypatch):
        from concurrent.futures import Future

        from api import services

        services._reset_remote_status_cache()
        inflight: Future = Future()
        services._remote_status_inflight = inflight
        services._remote_status_started = time.monotonic()
        same = services._remote_status_future()
        assert same is inflight
        services._reset_remote_status_cache()


class TestCooldownSurvivesRestart:
    @pytest.fixture(autouse=True)
    def _clean_history(self):
        yield
        from ib_gateway_remote import serve

        with serve._verb_history_guard:
            serve._verb_history.clear()
            serve._verb_wall_history.clear()

    def test_reloading_the_module_keeps_the_refusal(self, monkeypatch, tmp_path):
        """R-567: `_verb_history` was process memory; a daemon restart
        between stop and start re-enabled the stacked-2FA-push sequence."""
        monkeypatch.setenv("RADON_IB_REMOTE_STATE_DIR", str(tmp_path))
        from ib_gateway_remote import serve

        importlib.reload(serve)
        serve.record_verb("stop")
        assert serve.cooldown_refusal("start") is not None
        reloaded = importlib.reload(serve)
        refusal = reloaded.cooldown_refusal("start")
        assert refusal is not None, (
            "a daemon restart between stop and start erased the cooldown"
        )
        assert "cooldown" in refusal

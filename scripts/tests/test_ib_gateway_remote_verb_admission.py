"""Broker verb cooldown: armed by every attempt, admitted atomically.

Audit 2026-11-03 §3.2 (broker verb cooldown):

* ``record_verb`` ran only on helper exit 0. A restart that failed AFTER the
  helper started the Gateway (rc 1, or a 124 timeout) may already have sent an
  IBKR push, yet armed no cooldown, so an immediate retry stacked a second one.
  Only the helper's explicit "did nothing" exits (hold 73, busy 74, lease 75,
  usage 2, unavailable 127) skip the record now.
* The cooldown check and the record were separate critical sections, so two
  ThreadingHTTPServer workers could both pass the check before either ran the
  helper. Admission now checks and claims under one lock, and refuses a second
  Gateway mutation while one is in flight.
"""
from __future__ import annotations

import sys
import threading
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ib_gateway_remote import serve  # noqa: E402
from test_ib_gateway_remote import _call, _config, _start, mint_mtls  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_history(monkeypatch, tmp_path):
    monkeypatch.delenv("STATE_DIRECTORY", raising=False)
    monkeypatch.delenv("RADON_IB_REMOTE_STATE_DIR", raising=False)
    monkeypatch.setenv("RADON_IB_OPERATOR_HOLD_PATH", str(tmp_path / "no-hold.json"))
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "lease" / "lock.json"))
    monkeypatch.setattr(serve, "_verb_history", {})
    monkeypatch.setattr(serve, "_verb_wall_history", {})
    monkeypatch.setattr(serve, "_verbs_in_flight", set(), raising=False)


def _helper(tmp_path: Path, *, rc: int, sleep: float = 0.0) -> Path:
    path = tmp_path / "radon-ib-gateway-control"
    path.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$1\" >> '{tmp_path / 'helper.log'}'\n"
        f"sleep {sleep}\n"
        "echo done\n"
        f"exit {rc}\n"
    )
    path.chmod(0o755)
    return path


def _calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "helper.log"
    return log.read_text().splitlines() if log.exists() else []


def _post(httpd, certs, verb):
    try:
        return _call(httpd, certs, f"/{verb}", "POST")
    except urllib.error.HTTPError as err:
        import json

        return err.code, json.loads(err.read().decode() or "{}")


class TestAttemptArmsTheCooldown:
    @pytest.mark.parametrize("rc", [1, 124])
    def test_failed_restart_arms_the_cooldown(self, tmp_path, rc):
        certs = mint_mtls(tmp_path)
        httpd = _start(_config(tmp_path, certs, helper=_helper(tmp_path, rc=rc)))
        try:
            status, _ = _post(httpd, certs, "restart")
            assert status == 502
            assert serve.cooldown_refusal("restart") is None  # restart does not gate itself
            assert "restart" in serve._verb_history
        finally:
            httpd.shutdown()

    @pytest.mark.parametrize("rc", [2, 73, 74, 75, 127])
    def test_helper_refusals_do_not_arm_it(self, tmp_path, rc):
        certs = mint_mtls(tmp_path)
        httpd = _start(_config(tmp_path, certs, helper=_helper(tmp_path, rc=rc)))
        try:
            _post(httpd, certs, "stop")
            assert "stop" not in serve._verb_history
            assert serve.cooldown_refusal("start") is None
        finally:
            httpd.shutdown()

    def test_failed_stop_still_blocks_a_following_start(self, tmp_path):
        certs = mint_mtls(tmp_path)
        httpd = _start(_config(tmp_path, certs, helper=_helper(tmp_path, rc=1)))
        try:
            assert _post(httpd, certs, "stop")[0] == 502
            status, body = _post(httpd, certs, "start")
            assert status == 409
            assert "cooldown" in body["detail"]
            assert _calls(tmp_path) == ["stop"]
        finally:
            httpd.shutdown()


class TestAtomicAdmission:
    def test_admission_claims_under_one_lock(self):
        assert serve.admit_verb("start") is None
        # A second mutation while the first is in flight is refused.
        refusal = serve.admit_verb("restart")
        assert refusal is not None and "in progress" in refusal
        serve.finish_verb("start", 0)
        assert serve.admit_verb("restart") is None
        serve.finish_verb("restart", 0)

    def test_finish_records_before_releasing_the_claim(self):
        assert serve.admit_verb("stop") is None
        serve.finish_verb("stop", 0)
        refusal = serve.admit_verb("start")
        assert refusal is not None and "cooldown" in refusal

    def test_concurrent_restarts_reach_the_helper_once(self, tmp_path):
        certs = mint_mtls(tmp_path)
        httpd = _start(_config(tmp_path, certs, helper=_helper(tmp_path, rc=0, sleep=1.0)))
        results: list[int] = []
        try:
            threads = [
                threading.Thread(target=lambda: results.append(_post(httpd, certs, "restart")[0]))
                for _ in range(2)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=20)
            assert sorted(results) == [200, 409]
            assert _calls(tmp_path) == ["restart"]
        finally:
            httpd.shutdown()

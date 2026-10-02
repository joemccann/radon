"""The broker daemon refuses an operator login while IBKR throttles logins.

2026-09-26: the watchdog (#744) held its own restarts once IBKR answered
"Too many failed login attempts", but the operator path did not. The admin
page's Restart went POST /ib/restart -> broker /restart -> a fresh login, and
every one was another failed attempt keeping IBKR's counter armed. The daemon
now reads the watchdog's recorded throttle and refuses restart/start with a
409 until the quiet period ends. stop and reset-lease stay allowed.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ib_gateway_remote import serve  # noqa: E402
from test_ib_gateway_remote import _call, _config, _start, _stop, mint_mtls  # noqa: E402
from utils.ib_login_throttle import LOGIN_THROTTLE_COOLDOWN_BASE_SECS  # noqa: E402

THROTTLE_LINE = (
    "2026-09-26 22:40:32:372 IBC: Too many failed login attempts. "
    "Please wait 22 seconds before attempting to re-login again."
)


@pytest.fixture(autouse=True)
def _clean_verb_history():
    yield
    with serve._verb_history_guard:
        serve._verb_history.clear()
        serve._verb_wall_history.clear()


@pytest.fixture
def certs(tmp_path):
    pki = tmp_path / "pki"
    pki.mkdir()
    return mint_mtls(pki)


def _write_watchdog_state(path: Path, *, since: float, retries: int = 0) -> None:
    path.write_text(json.dumps({
        "login_throttle_since": since,
        "login_throttle_line": THROTTLE_LINE if since else "",
        "login_throttle_retries": retries,
    }))


def _post(httpd, certs, verb: str):
    try:
        return _call(httpd, certs, f"/{verb}", "POST")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def _helper_calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "helper.log"
    return log.read_text().split() if log.exists() else []


@pytest.fixture
def daemon(tmp_path, certs):
    state = tmp_path / "ib-watchdog-state.json"
    httpd = _start(_config(tmp_path, certs, RADON_IB_WATCHDOG_STATE_PATH=state))
    yield httpd, state
    _stop(httpd)


@pytest.mark.parametrize("verb", ["restart", "start"])
def test_login_verbs_refused_inside_the_throttle_quiet_period(daemon, certs, tmp_path, verb):
    httpd, state = daemon
    _write_watchdog_state(state, since=time.time() - 60)

    status, body = _post(httpd, certs, verb)

    assert status == 409
    assert body["ok"] is False
    assert body["returncode"] == serve.CONTROL_BUSY_RC
    assert "too many failed login attempts" in body["detail"].lower()
    assert "UTC" in body["detail"]
    assert _helper_calls(tmp_path) == [], "the helper ran: that is one more failed IBKR login"


def test_restart_allowed_once_the_quiet_period_has_passed(daemon, certs, tmp_path):
    httpd, state = daemon
    _write_watchdog_state(state, since=time.time() - LOGIN_THROTTLE_COOLDOWN_BASE_SECS - 5)

    status, body = _post(httpd, certs, "restart")

    assert (status, body["ok"]) == (200, True)
    assert _helper_calls(tmp_path) == ["restart"]


def test_quiet_period_grows_with_each_watchdog_retry(daemon, certs, tmp_path):
    httpd, state = daemon
    _write_watchdog_state(state, since=time.time() - LOGIN_THROTTLE_COOLDOWN_BASE_SECS - 5, retries=1)

    status, _ = _post(httpd, certs, "restart")

    assert status == 409
    assert _helper_calls(tmp_path) == []


@pytest.mark.parametrize("verb", ["stop", "reset-lease"])
def test_non_login_verbs_are_never_throttle_gated(daemon, certs, tmp_path, verb):
    httpd, state = daemon
    _write_watchdog_state(state, since=time.time() - 60)

    status, body = _post(httpd, certs, verb)

    assert (status, body["ok"]) == (200, True)
    assert _helper_calls(tmp_path) == [verb]


@pytest.mark.parametrize("contents", [None, "{not json", json.dumps({"login_throttle_since": 0.0})])
def test_no_recorded_throttle_leaves_restart_alone(daemon, certs, tmp_path, contents):
    httpd, state = daemon
    if contents is not None:
        state.write_text(contents)

    status, body = _post(httpd, certs, "restart")

    assert (status, body["ok"]) == (200, True)
    assert _helper_calls(tmp_path) == ["restart"]

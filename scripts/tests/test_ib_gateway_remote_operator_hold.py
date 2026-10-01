"""The broker daemon refuses Gateway logins during an IBKR operator hold.

2026-09-25: the Gateway and the operator share one IBKR username. The admin
page's Restart and the relay's stale-data escalation both reach the broker
through this daemon; while `radon ib release` holds, each would log in and
kick the operator. The daemon answers 423 OPERATOR_HOLD without running the
helper, and /status carries the hold so the app can show it.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ib_gateway_remote import serve  # noqa: E402
from test_ib_gateway_remote import _call, _config, _start, _stop, mint_mtls  # noqa: E402
from utils import ib_operator_hold  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("RADON_IB_OPERATOR_HOLD_PATH", str(tmp_path / "hold.json"))
    monkeypatch.setenv("RADON_IB_OPERATOR_HOLD_AUDIT", str(tmp_path / "hold.jsonl"))
    monkeypatch.setenv("RADON_IB_OPERATOR_HOLD_OWNER_UID", str(os.getuid()))
    # A cooldown stamp left by another module's daemon must not refuse ours.
    with serve._verb_history_guard:
        serve._verb_history.clear()
        serve._verb_wall_history.clear()
    yield
    with serve._verb_history_guard:
        serve._verb_history.clear()
        serve._verb_wall_history.clear()


@pytest.fixture
def certs(tmp_path):
    pki = tmp_path / "pki"
    pki.mkdir()
    return mint_mtls(pki)


@pytest.fixture
def httpd(tmp_path, certs):
    server = _start(_config(tmp_path, certs))
    yield server
    _stop(server)


def _request(httpd, certs, path: str, method: str):
    try:
        return _call(httpd, certs, path, method)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def _helper_calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "helper.log"
    return log.read_text().split() if log.exists() else []


@pytest.mark.parametrize("verb", ["start", "restart"])
def test_login_verbs_are_refused_while_held(httpd, certs, tmp_path, verb):
    ib_operator_hold.set_hold("operator web login", "test")

    status, body = _request(httpd, certs, f"/{verb}", "POST")

    assert status == 423
    assert body["code"] == "OPERATOR_HOLD"
    assert body["ok"] is False
    assert _helper_calls(tmp_path) == [], "the helper ran: that is a login that kicks the operator"


def test_stop_is_allowed_while_held(httpd, certs, tmp_path):
    ib_operator_hold.set_hold("operator web login", "test")
    status, body = _request(httpd, certs, "/stop", "POST")
    assert (status, body["ok"]) == (200, True)
    assert _helper_calls(tmp_path) == ["stop"]


def test_restart_passes_once_the_hold_is_cleared(httpd, certs, tmp_path):
    ib_operator_hold.set_hold("operator web login", "test")
    ib_operator_hold.clear_hold("test")
    status, body = _request(httpd, certs, "/restart", "POST")
    assert (status, body["ok"]) == (200, True)
    assert _helper_calls(tmp_path) == ["restart"]


def test_status_reports_the_hold(httpd, certs):
    ib_operator_hold.set_hold("operator web login", "test")
    _status, body = _request(httpd, certs, "/status", "GET")
    assert body["operator_hold"]["held"] is True
    assert body["operator_hold"]["reason"] == "operator web login"


# --- Set and clear from the admin panel (2026-10-01) -------------------------
# The app host reaches the broker only through this daemon. `hold` must work
# even right after a restart (no cooldown: it never logs in); `unhold` clears
# and logs in once, through the same cooldown and throttle gates as `start`.


def _post_json(httpd, certs, path: str, payload) -> tuple[int, dict]:
    import urllib.request

    from test_ib_gateway_remote import _ctx

    port = httpd.server_address[1]
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"https://127.0.0.1:{port}{path}",
        method="POST",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, context=_ctx(certs), timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def test_hold_sets_the_flag_then_stops_the_gateway(httpd, certs, tmp_path):
    status, body = _post_json(httpd, certs, "/hold", {"reason": "flatten on mobile", "actor": "user_1"})
    assert status == 200, body
    assert body["ok"] is True
    state = ib_operator_hold.hold_state()
    assert state["held"] is True
    assert (state["reason"], state["actor"]) == ("flatten on mobile", "app:user_1")
    assert body["operator_hold"]["held"] is True
    assert _helper_calls(tmp_path) == ["stop"]


def test_hold_is_not_gated_by_the_cooldown(httpd, certs, tmp_path):
    _request(httpd, certs, "/restart", "POST")
    status, body = _post_json(httpd, certs, "/hold", {"reason": "x", "actor": "u"})
    assert status == 200, body
    assert _helper_calls(tmp_path) == ["restart", "stop"]


def test_hold_reports_a_failed_stop_but_keeps_the_hold(tmp_path, certs):
    from test_ib_gateway_remote import _helper_script

    server = _start(_config(tmp_path, certs, helper=_helper_script(tmp_path, rc=1, stdout="boom")))
    try:
        status, body = _post_json(server, certs, "/hold", {"reason": "x", "actor": "u"})
    finally:
        _stop(server)
    assert status == 502
    assert body["ok"] is False
    assert "radon ib release" in body["detail"]
    assert ib_operator_hold.is_held() is True


def test_unhold_clears_then_logs_in_once(httpd, certs, tmp_path):
    ib_operator_hold.set_hold("flatten", "ssh:joe")
    status, body = _post_json(httpd, certs, "/unhold", {"actor": "user_1"})
    assert status == 200, body
    assert body["ok"] is True
    assert body["gateway_start"]["ok"] is True
    assert ib_operator_hold.is_held() is False
    assert _helper_calls(tmp_path) == ["start"]


def test_unhold_right_after_hold_clears_but_defers_the_login(httpd, certs, tmp_path):
    _post_json(httpd, certs, "/hold", {"reason": "x", "actor": "u"})
    status, body = _post_json(httpd, certs, "/unhold", {"actor": "u"})
    assert status == 200, body
    assert ib_operator_hold.is_held() is False
    assert body["gateway_start"]["ok"] is False
    assert "cooldown" in body["gateway_start"]["detail"]
    assert _helper_calls(tmp_path) == ["stop"], "a login inside the cooldown would stack a push"


def test_unhold_by_a_non_owner_removes_a_root_hold(httpd, certs, monkeypatch):
    ib_operator_hold.set_hold("flatten", "ssh:operator")
    monkeypatch.setenv("RADON_IB_OPERATOR_HOLD_OWNER_UID", str(os.getuid() + 1))
    status, body = _post_json(httpd, certs, "/unhold", {"actor": "u"})
    assert status == 200, body
    assert ib_operator_hold.is_held() is False


@pytest.mark.parametrize("payload", [b"not json", b"[1, 2]"])
def test_hold_rejects_a_bad_body_without_touching_anything(httpd, certs, tmp_path, payload):
    status, _body = _post_json(httpd, certs, "/hold", payload)
    assert status == 400
    assert ib_operator_hold.is_held() is False
    assert _helper_calls(tmp_path) == []


def test_refused_login_reports_the_hold(httpd, certs):
    ib_operator_hold.set_hold("flatten", "ssh:joe")
    _status, body = _request(httpd, certs, "/start", "POST")
    assert body["operator_hold"]["actor"] == "ssh:joe"

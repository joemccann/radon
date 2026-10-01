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

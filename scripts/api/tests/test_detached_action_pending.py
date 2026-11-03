"""Detached panel-host actions report "pending", not success (audit §3.2).

The radon-control daemon answers a radon-api / radon-nextjs restart or a
full-stack restart BEFORE it runs, because the action cycles the caller.
That reply ({"ok": true, "accepted": true}) was mapped to a plain success,
so the panel showed "done" for an action whose operator CLI could still
exit 74 on the deploy lock a few seconds later. The outcome is now carried
as pending end to end: daemon reply, ActionResult, and HTTP 202.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from control_service import serve  # noqa: E402
from scripts.api import auth, server  # noqa: E402
from scripts.api import services as admin_services  # noqa: E402


class _Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        return 0, "ok"


def _controller(tmp_path):
    spawned = []
    controller = serve.Controller(
        runner=_Runner(),
        loaded_units=lambda: ["radon-api.service", "radon-relay.service"],
        unit_rows=lambda: [],
        deploy_lock_path=tmp_path / "deploy.lock",
        spawn=spawned.append,
        detach_delay=0.0,
    )
    return controller, spawned


def test_daemon_marks_a_detached_reply_pending(tmp_path):
    controller, spawned = _controller(tmp_path)
    reply = controller.handle({"op": "stack-restart"}, peer_uid=os.getuid())
    assert reply["accepted"] is True
    assert reply["pending"] is True
    assert len(spawned) == 1


def test_daemon_synchronous_reply_is_not_pending(tmp_path):
    controller, _ = _controller(tmp_path)
    reply = controller.handle(
        {"op": "unit", "verb": "restart", "unit": "radon-relay.service"}, peer_uid=os.getuid(),
    )
    assert reply["ok"] is True
    assert "pending" not in reply


@pytest.fixture
def via_socket(monkeypatch):
    # The server binds `api.services`, a distinct module object from
    # `scripts.api.services` under this repo's two import roots: patch both.
    modules = {admin_services, server.admin_services}
    for services in modules:
        monkeypatch.setattr(services, "is_systemd_available", lambda: False)
        monkeypatch.setattr(services, "is_operator_cli_available", lambda: False)
        monkeypatch.setattr(services.host_control, "socket_present", lambda: True)
    monkeypatch.delenv("RADON_HOST_ROLE", raising=False)

    def install(reply):
        async def acall(request, timeout):
            return reply

        for services in modules:
            monkeypatch.setattr(services.host_control, "acall", acall)

    return install


_ACCEPTED = {"ok": True, "accepted": True, "pending": True, "returncode": 0, "detail": "accepted"}


def test_services_carry_pending(via_socket):
    via_socket(_ACCEPTED)
    result = asyncio.run(admin_services.restart_full_stack(actor="user_abc"))
    assert result.ok is True
    assert result.pending is True
    unit = asyncio.run(admin_services.control_unit("radon-api.service", "restart"))
    assert unit.pending is True


def test_services_completed_action_is_not_pending(via_socket):
    via_socket({"ok": True, "returncode": 0, "detail": "restart completed"})
    result = asyncio.run(admin_services.control_unit("radon-relay.service", "restart"))
    assert result.ok is True
    assert result.pending is False


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    return TestClient(server.app)


@pytest.mark.parametrize("path", [
    "/admin/stack/restart",
    "/admin/services/radon-api.service/restart",
])
def test_routes_answer_202_for_a_pending_action(client, via_socket, path):
    via_socket(_ACCEPTED)
    resp = client.post(path)
    assert resp.status_code == 202, resp.text
    assert resp.json()["pending"] is True


def test_route_answers_200_for_a_completed_action(client, via_socket):
    via_socket({"ok": True, "returncode": 0, "detail": "restart completed"})
    resp = client.post("/admin/services/radon-relay.service/restart")
    assert resp.status_code == 200, resp.text
    assert resp.json()["pending"] is False

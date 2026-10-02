"""T-525: exercise the kernel credential decoder before control admission.

The socket integration fixtures replace peer_uid. These tests retain the real
helper and fake only getsockopt, including a foreign uid with root-like pid/gid.
No system command or host socket is used.
"""
from __future__ import annotations

import socket
import struct
from unittest.mock import Mock

import pytest

from control_service import serve


@pytest.mark.parametrize(
    "pid,uid,gid,allowed",
    [(7100, 501, 20, True), (7100, 0, 20, True),
     (0, 502, 0, False), (501, 502, 501, False), (7100, 502, 20, False)],
)
def test_kernel_uid_alone_controls_admission(monkeypatch, tmp_path, pid, uid, gid, allowed):
    monkeypatch.setattr(serve.os, "getuid", lambda: 501)
    monkeypatch.setattr(socket, "SO_PEERCRED", 17, raising=False)
    peer = Mock()
    peer.getsockopt.return_value = struct.pack("3i", pid, uid, gid)
    runner = Mock(return_value=(0, "started"))
    controller = serve.Controller(
        runner=runner,
        loaded_units=lambda: ["radon-relay.service"],
        deploy_lock_path=tmp_path / "deploy.lock",
    )

    decoded = serve.peer_uid(peer)
    assert decoded == uid
    peer.getsockopt.assert_called_once_with(socket.SOL_SOCKET, 17, struct.calcsize("3i"))
    result = controller.handle(
        {"op": "unit", "verb": "start", "unit": "radon-relay.service"}, decoded,
    )
    assert result["ok"] is allowed
    if allowed:
        runner.assert_called_once_with(
            ["/usr/bin/sudo", "-n", "/usr/local/bin/radon", "unit", "start", "radon-relay.service"],
            serve.UNIT_TIMEOUT_S,
        )
    else:
        assert result["error"] == "peer not allowed"
        runner.assert_not_called()


@pytest.mark.parametrize("available", [False, True], ids=["unsupported", "kernel-error"])
def test_unknown_kernel_identity_refuses_without_execution(monkeypatch, tmp_path, available):
    peer = Mock()
    if available:
        monkeypatch.setattr(socket, "SO_PEERCRED", 17, raising=False)
        peer.getsockopt.side_effect = OSError("credentials unavailable")
    else:
        monkeypatch.delattr(socket, "SO_PEERCRED", raising=False)
    runner = Mock()
    controller = serve.Controller(
        runner=runner, loaded_units=lambda: ["radon-relay.service"],
        deploy_lock_path=tmp_path / "deploy.lock",
    )
    uid = serve.peer_uid(peer)
    assert uid is None
    result = controller.handle(
        {"op": "unit", "verb": "start", "unit": "radon-relay.service"}, uid,
    )
    assert result["ok"] is False
    assert result["error"] == "peer not allowed"
    runner.assert_not_called()
    if not available:
        peer.getsockopt.assert_not_called()

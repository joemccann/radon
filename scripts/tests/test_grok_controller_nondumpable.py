"""The controller hides its own /proc from the agent child it launches.

``grok_child_env`` strips the controller's Turso and Pushover credentials
from the child environment, but the child runs under the SAME UID, so that
allowlist is advisory: a same-UID process can read ``/proc/<ppid>/environ``.
``prctl(PR_SET_DUMPABLE, 0)`` on the CONTROLLER reparents its /proc entry
to root and denies that read. It must be applied to the controller itself,
before the launch — a ``preexec_fn`` would harden the child and leave the
controller's environ exposed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import grok_page_responder as responder  # noqa: E402


def test_controller_is_hardened_before_the_agent_launches(monkeypatch):
    order: list[str] = []

    monkeypatch.setattr(
        responder,
        "_harden_controller_against_child",
        lambda: order.append("prctl"),
    )

    def fake_run(*_a, **_k):
        order.append("subprocess.run")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    responder._default_grok_runner(["grok", "--prompt-file", "p"])

    assert order == ["prctl", "subprocess.run"]


def test_non_linux_platform_is_a_clean_noop(monkeypatch):
    import ctypes

    def boom(*_a, **_k):  # pragma: no cover - must never run off Linux
        raise AssertionError("prctl attempted off Linux")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(ctypes, "CDLL", boom)

    assert responder._harden_controller_against_child() is None


def test_missing_libc_does_not_stop_the_agent_from_launching(monkeypatch, capsys):
    import ctypes

    def no_libc(*_a, **_k):
        raise OSError("libc.so.6 not found")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ctypes, "CDLL", no_libc)

    launched: list[list[str]] = []

    def fake_run(cmd, *_a, **_k):
        launched.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    responder._default_grok_runner(["grok", "--prompt-file", "p"])

    assert launched == [["grok", "--prompt-file", "p"]]
    assert "libc.so.6 not found" in capsys.readouterr().err


def test_prctl_targets_this_process_not_the_child(monkeypatch):
    """PR_SET_DUMPABLE must be 4, set to 0, on the calling process."""
    import ctypes

    calls: list[tuple] = []

    class FakeLibc:
        def prctl(self, *args):
            calls.append(args)
            return 0

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ctypes, "CDLL", lambda *_a, **_k: FakeLibc())

    responder._harden_controller_against_child()

    assert calls and calls[0][0] == 4 and calls[0][1] == 0


def test_launch_passes_no_preexec_fn(monkeypatch):
    """Hardening the child's own /proc would not protect the controller."""
    seen: dict = {}

    monkeypatch.setattr(responder, "_harden_controller_against_child", lambda: None)

    def fake_run(*_a, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    responder._default_grok_runner(["grok"])

    assert "preexec_fn" not in seen

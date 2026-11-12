"""The native CLI canary must not fail when its child exits as it is stopped."""
from __future__ import annotations

import errno
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

_PATH = Path(__file__).resolve().parents[1] / "ci" / "check_model_cli_transport.py"


@pytest.fixture
def gate():
    spec = importlib.util.spec_from_file_location("check_model_cli_transport_under_test", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _exited_unreaped():
    # A session leader that has exited but has not been waited on: on macOS
    # killpg() of that zombie-only group fails with EPERM, not ESRCH.
    process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    process.wait()
    return process


def test_stop_owned_tolerates_eperm_from_an_exited_group(gate, monkeypatch):
    process = _exited_unreaped()
    monkeypatch.setattr(process, "poll", lambda: None)  # it was alive when polled

    def killpg(pid, sig):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(gate.os, "killpg", killpg)
    gate._stop_owned(process)


def test_stop_owned_tolerates_eperm_while_the_child_is_still_exiting(gate, monkeypatch):
    # macOS can refuse killpg() with EPERM while the leader is mid-exit, before
    # waitid() can see it. A child that finishes moments later is not an error.
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"], start_new_session=True)

    def killpg(pid, sig):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(gate.os, "killpg", killpg)
    gate._stop_owned(process)
    assert process.returncode == 0


def test_stop_owned_still_raises_eperm_for_a_live_group(gate, monkeypatch):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        real_killpg = gate.os.killpg

        def killpg(pid, sig):
            raise PermissionError(errno.EPERM, "Operation not permitted")

        monkeypatch.setattr(gate.os, "killpg", killpg)
        with pytest.raises(PermissionError):
            gate._stop_owned(process)
    finally:
        monkeypatch.undo()
        real_killpg(process.pid, 9)
        process.wait()


def test_stop_owned_real_zombie_group(gate, monkeypatch):
    # Real kernel behaviour on whichever platform runs the suite.
    process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    for _ in range(200):
        try:
            # Observe the exit without reaping it, leaving a zombie group.
            if os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is not None:
                break
        except ChildProcessError:
            break
        time.sleep(0.01)
    monkeypatch.setattr(process, "poll", lambda: None)
    gate._stop_owned(process)


def test_unexpected_errors_name_their_origin(gate, monkeypatch, capsys):
    def main(argv=None):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(gate, "main", main)
    with pytest.raises(SystemExit):
        gate._run()
    err = capsys.readouterr().err
    assert "PermissionError" in err and "check_model_cli_transport_under_test" not in err
    assert "test_check_model_cli_transport.py" in err

"""Gateway shell helpers must not orphan children on timeout or cancellation."""

from __future__ import annotations

import asyncio

import pytest

from scripts.api import ib_gateway


class _BlockingProcess:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.killed = False
        self.waited = False
        self.returncode = None

    async def communicate(self):
        self.started.set()
        await self.release.wait()
        self.returncode = -9 if self.killed else 0
        return b"", b""

    def kill(self) -> None:
        self.killed = True
        self.release.set()

    async def wait(self) -> int:
        self.waited = True
        self.returncode = -9 if self.killed else 0
        return self.returncode


def _factory(proc):
    async def create(*args, **kwargs):
        return proc

    return create


@pytest.mark.asyncio
async def test_docker_compose_timeout_kills_and_reaps(monkeypatch, tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    monkeypatch.setattr(ib_gateway, "COMPOSE_DIR", tmp_path)
    proc = _BlockingProcess()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _factory(proc))

    _stdout, _stderr, rc = await ib_gateway._docker_compose("ps", timeout=0.01)

    assert rc == -1
    assert proc.killed is True
    assert proc.waited is True


@pytest.mark.asyncio
async def test_docker_compose_cancellation_kills_and_reaps(monkeypatch, tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    monkeypatch.setattr(ib_gateway, "COMPOSE_DIR", tmp_path)
    proc = _BlockingProcess()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _factory(proc))

    task = asyncio.create_task(ib_gateway._docker_compose("ps", timeout=60))
    await proc.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert proc.killed is True
    assert proc.waited is True


@pytest.mark.asyncio
async def test_run_shell_cancellation_kills_and_reaps(monkeypatch, tmp_path):
    script = tmp_path / "gateway.sh"
    script.write_text("#!/bin/sh\n")
    proc = _BlockingProcess()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _factory(proc))

    task = asyncio.create_task(ib_gateway._run_shell(script, timeout=60))
    await proc.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert proc.killed is True
    assert proc.waited is True


@pytest.mark.asyncio
async def test_run_shell_timeout_kills_descendant_process_group(monkeypatch, tmp_path):
    marker = tmp_path / "orphan-survived"
    script = tmp_path / "gateway.sh"
    script.write_text(
        "#!/bin/sh\n"
        f"(trap '' TERM; sleep 0.5; printf survived > '{marker}') &\n"
        "trap '' TERM\n"
        "wait\n"
    )
    monkeypatch.setattr(ib_gateway, "PROCESS_TERM_GRACE_S", 0.05)

    _stdout, stderr, rc = await ib_gateway._run_shell(script, timeout=0.05)
    await asyncio.sleep(0.6)

    assert rc == -1
    assert "timed out" in stderr
    assert not marker.exists(), "IBC descendant survived the timed-out shell"

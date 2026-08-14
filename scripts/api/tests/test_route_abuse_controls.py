from __future__ import annotations

import asyncio
import json

import pytest


@pytest.mark.asyncio
async def test_subprocess_budget_rejects_without_spawning(monkeypatch):
    from scripts.api import subprocess as subprocess_mod

    monkeypatch.setattr(
        subprocess_mod,
        "_active_subprocesses",
        subprocess_mod.MAX_CONCURRENT_SUBPROCESSES,
    )
    spawned = False

    async def fake_spawn(*args, **kwargs):
        nonlocal spawned
        spawned = True

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    result = await subprocess_mod.run_script("scanner.py", [])

    assert result.ok is False
    assert result.error == "Subprocess capacity exhausted"
    assert spawned is False


@pytest.mark.asyncio
async def test_subprocess_budget_logs_exhaustion(monkeypatch, caplog):
    from scripts.api import subprocess as subprocess_mod

    monkeypatch.setattr(
        subprocess_mod,
        "_active_subprocesses",
        subprocess_mod.MAX_CONCURRENT_SUBPROCESSES,
    )
    caplog.set_level("WARNING", logger="radon.subprocess")
    result = await subprocess_mod.run_script("scanner.py", [])
    assert result.ok is False
    assert any("capacity exhausted" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_run_script_cancellation_kills_and_reaps(monkeypatch):
    from scripts.api import subprocess as subprocess_mod

    class BlockingProcess:
        returncode = None

        def __init__(self):
            self.started = asyncio.Event()
            self.released = asyncio.Event()
            self.killed = False
            self.waited = False

        async def communicate(self):
            self.started.set()
            await self.released.wait()
            return b"", b""

        def kill(self):
            self.killed = True
            self.returncode = -9
            self.released.set()

        async def wait(self):
            self.waited = True
            return self.returncode

    proc = BlockingProcess()

    async def fake_spawn(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    task = asyncio.create_task(subprocess_mod.run_script("scanner.py", []))
    await proc.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert proc.killed is True
    assert proc.waited is True


@pytest.mark.asyncio
async def test_oversized_jwt_is_rejected_before_jwks(monkeypatch):
    from scripts.api import auth

    called = False

    class Client:
        def get_signing_key_from_jwt(self, token):
            nonlocal called
            called = True
            raise AssertionError("JWKS must not be reached")

    monkeypatch.setattr(auth, "_get_jwks_client", lambda: Client())
    request = type(
        "Request",
        (),
        {
            "client": type("Client", (), {"host": "203.0.113.10"})(),
            "headers": {
                "Authorization": "Bearer " + ("x" * (auth.MAX_JWT_BYTES + 1)),
                "x-forwarded-for": "203.0.113.10",
            },
        },
    )()

    with pytest.raises(Exception) as exc:
        await auth.verify_clerk_jwt(request)
    assert getattr(exc.value, "status_code", None) == 401
    assert called is False


@pytest.mark.asyncio
async def test_jwks_lookup_is_single_flight_per_key_id(monkeypatch):
    from scripts.api import auth

    auth._jwks_inflight.clear()
    auth._jwks_negative.clear()
    calls = 0

    class Client:
        def get_signing_key_from_jwt(self, token):
            nonlocal calls
            import time

            calls += 1
            time.sleep(0.03)
            return object()

    monkeypatch.setattr(auth, "_get_jwks_client", lambda: Client())
    first, second = await asyncio.gather(
        auth._bounded_signing_key_lookup("token", "shared-kid"),
        auth._bounded_signing_key_lookup("token", "shared-kid"),
    )
    assert first is second
    assert calls == 1


@pytest.mark.asyncio
async def test_failed_jwks_key_id_is_negative_cached(monkeypatch):
    from scripts.api import auth

    auth._jwks_inflight.clear()
    auth._jwks_negative.clear()
    calls = 0

    class Client:
        def get_signing_key_from_jwt(self, token):
            nonlocal calls
            calls += 1
            raise ValueError("unknown kid")

    monkeypatch.setattr(auth, "_get_jwks_client", lambda: Client())
    for _ in range(2):
        with pytest.raises(Exception):
            await auth._bounded_signing_key_lookup("token", "missing-kid")
    assert calls == 1


def test_workflow_executor_rejects_oversized_graph_before_node_work(monkeypatch):
    from workflow import nodes as nodes_mod
    from workflow.executor import WorkflowError, execute_graph

    called = False

    def data_source(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(nodes_mod, "run_data_source", data_source)
    graph = {
        "nodes": [
            {"id": f"n{index}", "type": "data-source", "params": {"source": "scanner"}}
            for index in range(33)
        ],
        "edges": [],
    }

    with pytest.raises(WorkflowError, match="32 nodes"):
        execute_graph(graph)
    assert called is False


def test_workflow_executor_rejects_excessive_depth():
    from workflow.executor import WorkflowError, execute_graph

    nodes = [
        {"id": f"n{index}", "type": "filter", "params": {"expression": "True"}}
        for index in range(17)
    ]
    graph = {
        "nodes": nodes,
        "edges": [
            {"from": f"n{index}", "to": f"n{index + 1}"}
            for index in range(16)
        ],
    }

    with pytest.raises(WorkflowError, match="depth 16"):
        execute_graph(graph)


def test_workflow_executor_rejects_oversized_serialized_graph():
    from workflow.executor import WorkflowError, execute_graph

    graph = {
        "nodes": [
            {
                "id": "n1",
                "type": "filter",
                "params": {"expression": "x" * 70_000},
            }
        ],
        "edges": [],
    }
    assert len(json.dumps(graph)) > 65_536
    with pytest.raises(WorkflowError, match="65536 bytes"):
        execute_graph(graph)


@pytest.mark.asyncio
async def test_workflow_request_cancellation_releases_slot_after_worker_finishes(
    monkeypatch,
):
    from scripts.api import server

    started = asyncio.Event()
    finish = asyncio.Event()

    async def fake_to_thread(*args, **kwargs):
        started.set()
        await finish.wait()
        return {"steps": [], "final_rows": []}

    class Request:
        async def json(self):
            return {"graph": {"nodes": [], "edges": []}}

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(server, "_active_workflows", 0)

    request_task = asyncio.create_task(server.workflow_run(Request()))
    await started.wait()
    request_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request_task

    assert server._active_workflows == 1
    finish.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert server._active_workflows == 0

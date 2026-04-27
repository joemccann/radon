import asyncio

from scripts.api import server
from scripts.api.subprocess import ScriptResult


def test_local_launchd_runtime_failures_do_not_auto_restart(monkeypatch):
    async def fake_run_script(script, args, timeout=30):
        return ScriptResult(ok=False, error="API connection failed: TimeoutError()")

    async def fake_check_ib_gateway():
        return {"port_listening": True, "upstream_dead": True}

    async def fail_restart():
        raise AssertionError("restart should not be attempted in local launchd runtime recovery")

    monkeypatch.setattr(server, "run_script", fake_run_script)
    monkeypatch.setattr(server, "check_ib_gateway", fake_check_ib_gateway)
    monkeypatch.setattr(server, "restart_ib_gateway", fail_restart)
    monkeypatch.setattr(server, "is_cloud_mode", lambda: False)
    monkeypatch.setattr(server, "is_docker_mode", lambda: False)
    monkeypatch.setattr(server, "_pool_has_any_connection", lambda: True)
    monkeypatch.setattr(server, "_ib_last_failure", 0.0)

    result = asyncio.run(server._run_ib_script_with_recovery("ib_sync.py", []))

    assert result.ok is False
    assert result.error is not None
    assert "manual restart required" in result.error.lower()
    assert "2fa" in result.error.lower()


def test_cloud_runtime_failures_still_do_not_restart(monkeypatch):
    async def fake_run_script(script, args, timeout=30):
        return ScriptResult(ok=False, error="ConnectionRefusedError(61, 'Connect call failed')")

    async def fake_check_ib_gateway():
        return {"port_listening": False, "upstream_dead": False}

    monkeypatch.setattr(server, "run_script", fake_run_script)
    monkeypatch.setattr(server, "check_ib_gateway", fake_check_ib_gateway)
    monkeypatch.setattr(server, "is_cloud_mode", lambda: True)
    monkeypatch.setattr(server, "is_docker_mode", lambda: False)
    monkeypatch.setattr(server, "_pool_has_any_connection", lambda: True)
    monkeypatch.setattr(server, "_ib_last_failure", 0.0)

    result = asyncio.run(server._run_ib_script_with_recovery("ib_sync.py", []))

    assert result.ok is False
    assert "cloud mode" in result.error.lower()

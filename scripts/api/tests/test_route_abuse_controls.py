from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_subprocess_budget_rejects_without_spawning(monkeypatch):
    from scripts.api import subprocess as subprocess_mod

    monkeypatch.setattr(
        subprocess_mod,
        "_active_subprocesses",
        subprocess_mod.MAX_CONCURRENT_SUBPROCESSES,
    )
    monkeypatch.setattr(subprocess_mod, "SUBPROCESS_ADMISSION_WAIT_S", 0.0)
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
    monkeypatch.setattr(subprocess_mod, "SUBPROCESS_ADMISSION_WAIT_S", 0.0)
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
            import jwt as pyjwt

            calls += 1
            # REL-235: must be a PyJWT verdict — a bare ValueError is now
            # classified as an upstream outage and is never negative-cached.
            raise pyjwt.exceptions.PyJWKClientError("unknown kid")

    monkeypatch.setattr(auth, "_get_jwks_client", lambda: Client())
    for _ in range(2):
        with pytest.raises(Exception):
            await auth._bounded_signing_key_lookup("token", "missing-kid")
    assert calls == 1

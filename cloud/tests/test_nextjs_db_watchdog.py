"""nextjs-db-watchdog: 401/403 is not a Turso wedge.

2026-08-13: GET /api/service-health is Clerk-protected (tightened
2026-08-11). The loopback watchdog sent no bearer, treated HTTP 401 as a
Node-local Turso wedge, and restarted radon-nextjs.
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import urllib.error
import urllib.request

import pytest

CLOUD_ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = CLOUD_ROOT.parent
WATCHDOG_PATH = CLOUD_ROOT / "scripts" / "nextjs_db_watchdog.py"
MIDDLEWARE_PATH = REPO_ROOT / "web" / "middleware.ts"


def _load_watchdog(state_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RADON_NEXTJS_DB_WATCHDOG_STATE_PATH", str(state_path))
    spec = importlib.util.spec_from_file_location("nextjs_db_watchdog", WATCHDOG_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "STATE_PATH", str(state_path))
    return module


class _FakeResp:
    def __init__(self, body: bytes, status: int = 200):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc) -> None:
        return None


def _http_error(url: str, code: int, msg: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, msg, hdrs=None, fp=io.BytesIO(b""))


def _seed_state(path: pathlib.Path, **overrides) -> None:
    payload = {
        "consecutive_wedges": 0,
        "last_restart_epoch": 0,
        "last_state": "ok",
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def watchdog(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    _seed_state(state_path)
    module = _load_watchdog(state_path, monkeypatch)
    restarts: list[str] = []
    health: list[tuple[str, str]] = []
    canary_calls: list[int] = []

    monkeypatch.setattr(module, "restart_nextjs", lambda: restarts.append("restart"))
    monkeypatch.setattr(
        module,
        "write_health_row",
        lambda state, detail: health.append((state, detail)),
    )
    monkeypatch.setattr(
        module,
        "python_can_read_turso",
        lambda: canary_calls.append(1) or True,
    )
    return module, state_path, restarts, health, canary_calls


def test_service_health_is_bearer_gated_not_public():
    text = MIDDLEWARE_PATH.read_text(encoding="utf-8")
    public_block = text.split("export const isPublicRoute", 1)[1].split("]);", 1)[0]
    bearer_block = text.split("export const PROBE_BEARER_API_ROUTES", 1)[1].split(
        ";", 1
    )[0]
    assert "/api/service-health" not in public_block
    assert "/api/service-health" in bearer_block


def test_http_401_does_not_restart_or_count_as_wedge(watchdog, monkeypatch):
    module, state_path, restarts, health, canary_calls = watchdog
    _seed_state(state_path, consecutive_wedges=2, last_state="ok")
    monkeypatch.setenv("RADON_PROBE_FRESHNESS_TOKEN", "tok")

    def _urlopen(req, timeout=None):
        raise _http_error(module.PROBE_URL, 401, "Unauthorized")

    monkeypatch.setattr(module.urllib.request, "urlopen", _urlopen)
    module.main()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert restarts == []
    assert state["consecutive_wedges"] == 2
    assert canary_calls == []
    assert not any(row[0] == "error" for row in health)
    assert not any("Turso wedge" in row[1] for row in health)


def test_http_403_does_not_restart_or_count_as_wedge(watchdog, monkeypatch):
    module, state_path, restarts, health, canary_calls = watchdog
    _seed_state(state_path, consecutive_wedges=2)
    monkeypatch.setenv("RADON_PROBE_FRESHNESS_TOKEN", "tok")

    def _urlopen(req, timeout=None):
        raise _http_error(module.PROBE_URL, 403, "Forbidden")

    monkeypatch.setattr(module.urllib.request, "urlopen", _urlopen)
    module.main()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert restarts == []
    assert state["consecutive_wedges"] == 2
    assert canary_calls == []
    assert health == []


def test_missing_token_stands_down_without_restart(watchdog, monkeypatch):
    module, state_path, restarts, health, canary_calls = watchdog
    monkeypatch.delenv("RADON_PROBE_FRESHNESS_TOKEN", raising=False)

    def _boom(*_a, **_k):
        raise AssertionError("must not treat an unauthenticated probe as a wedge")

    monkeypatch.setattr(module.urllib.request, "urlopen", _boom)
    module.main()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert restarts == []
    assert state["consecutive_wedges"] == 0
    assert canary_calls == []
    assert health == []


def test_bearer_probe_succeeds_on_healthy_200(watchdog, monkeypatch):
    module, state_path, restarts, health, _canary = watchdog
    monkeypatch.setenv("RADON_PROBE_FRESHNESS_TOKEN", "tok-fresh")
    captured: list[urllib.request.Request] = []

    def _urlopen(req, timeout=None):
        captured.append(req)
        body = json.dumps({"services": [{"service": "vcg-scan", "state": "ok"}]})
        return _FakeResp(body.encode("utf-8"), 200)

    monkeypatch.setattr(module.urllib.request, "urlopen", _urlopen)
    module.main()

    assert len(captured) == 1
    assert captured[0].get_header("Authorization") == "Bearer tok-fresh"
    assert restarts == []
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["consecutive_wedges"] == 0
    assert state["last_state"] == "ok"
    assert health == [("ok", "")]


def test_turso_error_row_on_200_still_restarts_after_threshold(watchdog, monkeypatch):
    module, state_path, restarts, health, canary_calls = watchdog
    _seed_state(state_path, consecutive_wedges=2, last_restart_epoch=0)
    monkeypatch.setenv("RADON_PROBE_FRESHNESS_TOKEN", "tok")

    def _urlopen(req, timeout=None):
        body = json.dumps({
            "services": [{
                "service": "turso-db",
                "state": "error",
                "error_summary": "read timed out",
            }],
        })
        return _FakeResp(body.encode("utf-8"), 200)

    monkeypatch.setattr(module.urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(module.time, "time", lambda: 1_000_000)
    module.main()

    assert canary_calls == [1]
    assert restarts == ["restart"]
    assert health and health[0][0] == "error"
    assert "in-process Turso wedge" in health[0][1]

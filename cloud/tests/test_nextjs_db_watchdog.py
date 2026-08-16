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
    # REL-033: the row IS written now (an unauthenticated probe means the
    # auto-restart is off and somebody has to see that), but it must never
    # claim the Node-local DB stall this unit restarts for.
    assert health and health[-1][0] == "error"
    assert not any("in-process Turso wedge" in row[1] for row in health)


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
    # REL-033: visible, but still not counted as a wedge and still no restart.
    assert health and health[-1][0] == "error"


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
    assert health and health[-1][0] == "error"


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


# ---------------------------------------------------------------------------
# REL-033 (R-059) — the `unknown` verdict logs, re-saves state and returns
# WITHOUT ever calling write_health_row, unlike the ok and restart paths. And
# HEALTH_ROW_SERVICE is in neither watchdog catalog, so the resulting stale row
# is never staleness-checked either. A token rotated on the Next.js side alone
# therefore silently and permanently disables the Turso-wedge auto-restart:
# the unit exits 0 green every 60s with nobody watching the row. A MISSING
# token is deploy-gated by required-env.txt; a WRONG one is not.
# ---------------------------------------------------------------------------

SERVICES_PY = REPO_ROOT / "scripts" / "watchdog" / "services.py"
WINDOWS_TS = REPO_ROOT / "web" / "lib" / "serviceHealthWindows.ts"


class TestUnknownVerdictIsVisible:
    def test_http_401_writes_a_non_ok_health_row(self, watchdog, monkeypatch):
        module, _state_path, restarts, health, _canary = watchdog
        monkeypatch.setenv("RADON_PROBE_FRESHNESS_TOKEN", "wrong-token")

        def _urlopen(req, timeout=None):
            raise _http_error(module.PROBE_URL, 401, "Unauthorized")

        monkeypatch.setattr(module.urllib.request, "urlopen", _urlopen)
        module.main()

        assert restarts == []
        assert health, "an unknown verdict wrote no row at all — nobody can see it"
        assert health[-1][0] != "ok"
        # It must not claim a Turso wedge: that is a different failure.
        assert "Turso wedge" not in health[-1][1]

    def test_missing_token_writes_a_non_ok_health_row(self, watchdog, monkeypatch):
        module, _state_path, restarts, health, _canary = watchdog
        monkeypatch.delenv("RADON_PROBE_FRESHNESS_TOKEN", raising=False)

        def _boom(*_a, **_k):
            raise AssertionError("must not probe without a token")

        monkeypatch.setattr(module.urllib.request, "urlopen", _boom)
        module.main()

        assert restarts == []
        assert health and health[-1][0] != "ok"

    def test_the_row_names_the_probe_failure(self, watchdog, monkeypatch):
        """The operator must be able to tell a rotated token from a wedge."""
        module, _state_path, _restarts, health, _canary = watchdog
        monkeypatch.setenv("RADON_PROBE_FRESHNESS_TOKEN", "wrong-token")
        monkeypatch.setattr(
            module.urllib.request,
            "urlopen",
            lambda req, timeout=None: (_ for _ in ()).throw(
                _http_error(module.PROBE_URL, 403, "Forbidden")
            ),
        )
        module.main()

        assert "probe" in health[-1][1].lower()


class TestHealthRowServiceIsInBothCatalogs:
    def test_registered_in_the_python_catalog(self):
        assert '"nextjs-db-read"' in SERVICES_PY.read_text(encoding="utf-8")

    def test_registered_in_the_typescript_catalog(self):
        assert '"nextjs-db-read"' in WINDOWS_TS.read_text(encoding="utf-8")

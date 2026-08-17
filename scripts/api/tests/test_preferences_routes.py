"""Route tests for the operator preferences surface (GET/PUT/DELETE /preferences).

Every `app_preferences` call is monkeypatched on the module object the route
module itself holds, so no Turso connection is ever opened here. The registry is
NOT mocked: it is a pure constant with no I/O, and pinning the response against
it is what proves the route exposes the whole operator surface.

Offload discipline: uvicorn runs one event loop, so every blocking
app_preferences call must go through asyncio.to_thread. Each stub records
whether a running event loop was visible from its own thread; a stub that sees
one was called inline on the loop.
"""
from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


ENTRY_KEYS = (
    "key",
    "label",
    "group",
    "value_type",
    "value",
    "default",
    "hard_min",
    "hard_max",
    "unit",
    "description",
    "applies_immediately",
    "source",
    "db_rejected",
    "updated_at",
    "updated_by",
)

STORE_OK = {"available": True, "error": None, "checked_at": "2026-08-11T17:02:11Z"}
STORE_DOWN = {
    "available": False,
    "error": "HranaHttpError: connection refused",
    "checked_at": None,
}


def _ran_on_event_loop() -> bool:
    """True when the caller is executing on a thread that owns a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class _Recorder:
    """Callable stub that records call args and its execution thread context."""

    def __init__(self, result=None, raises: BaseException | None = None):
        self.result = result
        self.raises = raises
        self.calls: list[tuple] = []
        self.idents: list[int] = []
        self.on_loop: list[bool] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        self.idents.append(threading.get_ident())
        self.on_loop.append(_ran_on_event_loop())
        if self.raises is not None:
            raise self.raises
        return self.result


class _FakeResolved:
    """Stand-in for ResolvedPreference: the route only ever calls to_dict()."""

    def __init__(self, entry: dict):
        self._entry = entry

    def to_dict(self) -> dict:
        return dict(self._entry)


def _entry_for(pref, *, value=None, source="default", updated_at=None, updated_by=None) -> dict:
    return {
        "key": pref.key,
        "label": pref.label,
        "group": pref.group,
        "value_type": pref.value_type,
        "value": pref.default if value is None else value,
        "default": pref.default,
        "hard_min": pref.hard_min,
        "hard_max": pref.hard_max,
        "unit": pref.unit,
        "description": pref.description,
        "applies_immediately": pref.applies_immediately,
        "source": source,
        "db_rejected": False,
        "updated_at": updated_at,
        "updated_by": updated_by,
    }


def _route_module_instances():
    """Both import identities of the route module (`api.routes.preferences`
    is what the app registered; `scripts.api.routes.preferences` is what this
    file imports). They are distinct module objects, so patches must hit both."""
    from scripts.api.routes import preferences as preferences_module

    modules = [preferences_module]
    doppelganger = sys.modules.get("api.routes.preferences")
    if doppelganger is not None and doppelganger is not preferences_module:
        modules.append(doppelganger)
    return modules


@pytest.fixture(autouse=True)
def localhost_bypass(monkeypatch):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    for module in _route_module_instances():
        monkeypatch.setattr(
            module, "is_trusted_local_request", lambda request: True, raising=False
        )


@pytest.fixture
def client():
    from scripts.api.server import app

    return TestClient(app)


@pytest.fixture
def prefs():
    """The app_preferences module object the route module resolved at import."""
    from scripts.api.routes import preferences as preferences_module

    return preferences_module.app_preferences


@pytest.fixture
def registry(prefs):
    return list(prefs.registry())


@pytest.fixture
def store(monkeypatch, prefs, registry):
    """Patch every blocking app_preferences call with a recorder."""

    stubs = {
        "refresh_snapshot": _Recorder(result=True),
        "apply_env_overlay": _Recorder(result={}),
        "resolve_all": _Recorder(
            result=[_FakeResolved(_entry_for(pref)) for pref in registry]
        ),
        "store_status": _Recorder(result=dict(STORE_OK)),
        "set_value": _Recorder(),
        "clear_value": _Recorder(),
    }
    for name, stub in stubs.items():
        monkeypatch.setattr(prefs, name, stub)
    return stubs


class TestGetPreferences:
    def test_get_returns_every_registry_entry(self, client, store, prefs, registry):
        response = client.get("/preferences")

        assert response.status_code == 200
        body = response.json()
        assert len(body["preferences"]) == len(registry)
        assert body["groups"] == list(prefs.GROUP_ORDER)
        assert body["store"] == STORE_OK
        assert isinstance(body["generated_at"], str) and body["generated_at"]

    def test_get_entry_shape(self, client, store):
        """The handler passes ResolvedPreference.to_dict() through verbatim."""
        body = client.get("/preferences").json()

        assert tuple(body["preferences"][0].keys()) == ENTRY_KEYS

    def test_get_returns_200_when_store_unavailable(self, client, store, registry):
        store["refresh_snapshot"].result = False
        store["store_status"].result = dict(STORE_DOWN)

        response = client.get("/preferences")

        assert response.status_code == 200
        body = response.json()
        assert body["store"]["available"] is False
        assert body["store"]["error"]
        assert len(body["preferences"]) == len(registry)

    def test_get_does_not_mutate_the_process_environment(self, client, store):
        """A read verb must have no global side effect. The overlay belongs to
        lifespan startup and the write paths, so the effective cap in a
        subprocess never depends on whether anyone loaded the page."""
        assert client.get("/preferences").status_code == 200

        assert store["apply_env_overlay"].calls == []
        assert len(store["refresh_snapshot"].calls) == 1

    def test_get_never_blocks_the_event_loop(self, client, store):
        assert client.get("/preferences").status_code == 200

        for name in ("refresh_snapshot", "resolve_all", "store_status"):
            assert store[name].on_loop == [False], f"{name} ran on the event loop"


class TestPutPreference:
    def test_put_valid_calls_set_value_and_echoes_entry(self, client, store, registry):
        pref = registry[0]
        entry = _entry_for(pref, value=400, source="db", updated_by="user_2abc")
        store["set_value"].result = _FakeResolved(entry)

        response = client.put(f"/preferences/{pref.key}", json={"value": 400})

        assert response.status_code == 200
        assert response.json() == {"preference": entry, "store": STORE_OK}
        args, _ = store["set_value"].calls[0]
        assert args == (pref.key, 400, "trusted-local")

    def test_put_records_the_operator_id_forwarded_by_the_trusted_hop(
        self, client, store, registry
    ):
        """The only production caller is Next.js on loopback, which forwards
        the Clerk operator id as `updated_by` in the body. On the trusted-local
        path that id is the real principal — record it, not the literal
        'trusted-local'."""
        pref = registry[0]
        store["set_value"].result = _FakeResolved(_entry_for(pref, value=400, source="db"))

        response = client.put(
            f"/preferences/{pref.key}",
            json={"value": 400, "updated_by": "user_2abcDEF"},
        )

        assert response.status_code == 200
        args, _ = store["set_value"].calls[0]
        assert args == (pref.key, 400, "user_2abcDEF")

    def test_forwarded_actor_is_ignored_off_the_trusted_local_path(
        self, client, store, registry, monkeypatch
    ):
        """A self-asserted actor is worthless on an audit trail for risk caps:
        only the trusted Next.js hop may forward one. Any other caller that
        reaches the handler without an authenticated principal is labelled."""
        for module in _route_module_instances():
            monkeypatch.setattr(
                module, "is_trusted_local_request", lambda request: False
            )
        pref = registry[0]
        store["set_value"].result = _FakeResolved(_entry_for(pref, value=400, source="db"))

        response = client.put(
            f"/preferences/{pref.key}",
            json={"value": 400, "updated_by": "someone-else"},
        )

        assert response.status_code == 200
        args, _ = store["set_value"].calls[0]
        assert "someone-else" not in args
        assert args == (pref.key, 400, "trusted-local")

    def test_authenticated_principal_wins_over_a_forwarded_actor(self):
        """A JWT-authenticated caller is recorded by its own subject; a
        forwarded `updated_by` can never override it."""
        from types import SimpleNamespace

        from scripts.api.routes import preferences as preferences_module

        request = SimpleNamespace(
            state=SimpleNamespace(user={"sub": "user_real"}), query_params={}
        )

        actor = preferences_module._actor(request, {"updated_by": "someone-else"})

        assert actor == "user_real"

    def test_forwarded_actor_is_sanitized(self, client, store, registry):
        """A blank forwarded id falls back to the label; an oversized one is
        truncated to the audit column bound."""
        pref = registry[0]
        store["set_value"].result = _FakeResolved(_entry_for(pref, value=400, source="db"))

        client.put(f"/preferences/{pref.key}", json={"value": 400, "updated_by": "   "})
        client.put(
            f"/preferences/{pref.key}", json={"value": 400, "updated_by": "x" * 200}
        )

        first, _ = store["set_value"].calls[0]
        second, _ = store["set_value"].calls[1]
        assert first == (pref.key, 400, "trusted-local")
        assert second == (pref.key, 400, "x" * 64)

    def test_put_unknown_key_returns_404_with_code(self, client, store, prefs):
        store["set_value"].raises = prefs.PreferenceValidationError(
            "UNKNOWN_PREFERENCE", "unknown preference key 'FOO'", key="FOO"
        )

        response = client.put("/preferences/FOO", json={"value": 1})

        assert response.status_code == 404
        assert response.json()["detail"] == {
            "code": "UNKNOWN_PREFERENCE",
            "message": "unknown preference key 'FOO'",
        }

    def test_put_wrong_type_returns_422(self, client, store, prefs):
        store["set_value"].raises = prefs.PreferenceValidationError(
            "WRONG_TYPE",
            "RADON_MAX_ORDER_QTY expects an integer value",
            key="RADON_MAX_ORDER_QTY",
        )

        response = client.put("/preferences/RADON_MAX_ORDER_QTY", json={"value": 400.5})

        assert response.status_code == 422
        assert response.json()["detail"] == {
            "code": "WRONG_TYPE",
            "message": "RADON_MAX_ORDER_QTY expects an integer value",
        }

    def test_put_out_of_range_returns_422_with_allowed_range(self, client, store, prefs):
        store["set_value"].raises = prefs.PreferenceValidationError(
            "OUT_OF_RANGE",
            "RADON_MAX_ORDER_QTY must be between 1 and 5000 (got 9000)",
            key="RADON_MAX_ORDER_QTY",
            allowed_min=1,
            allowed_max=5000,
        )

        response = client.put("/preferences/RADON_MAX_ORDER_QTY", json={"value": 9000})

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "OUT_OF_RANGE"
        assert "1" in detail["message"] and "5000" in detail["message"]
        assert detail["allowed_min"] == 1
        assert detail["allowed_max"] == 5000

    def test_put_missing_value_returns_400(self, client, store, registry):
        response = client.put(f"/preferences/{registry[0].key}", json={"updated_by": "u"})

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "BAD_REQUEST"
        assert store["set_value"].calls == []

    def test_put_non_object_body_returns_400(self, client, store, registry):
        response = client.put(f"/preferences/{registry[0].key}", json=[1, 2, 3])

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "BAD_REQUEST"
        assert store["set_value"].calls == []

    def test_put_unparseable_body_returns_400(self, client, store, registry):
        response = client.put(
            f"/preferences/{registry[0].key}",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "BAD_REQUEST"
        assert store["set_value"].calls == []

    def test_put_store_failure_returns_503_not_500(self, client, store, prefs, registry):
        store["set_value"].raises = prefs.PreferenceStoreError("turso unreachable")

        response = client.put(f"/preferences/{registry[0].key}", json={"value": 400})

        assert response.status_code == 503
        assert response.json()["detail"] == {
            "code": "PREFERENCE_STORE_UNAVAILABLE",
            "message": "preferences store temporarily unavailable",
        }

    def test_db_work_is_offloaded_from_the_event_loop(self, client, store, registry):
        """A stub that can see a running loop was called inline on the loop.

        The handler is async, so an un-offloaded set_value would execute on the
        uvicorn loop thread and observe its running loop; asyncio.to_thread runs
        it on a worker thread where get_running_loop() raises.
        """
        pref = registry[0]
        store["set_value"].result = _FakeResolved(_entry_for(pref, value=400, source="db"))

        assert client.put(f"/preferences/{pref.key}", json={"value": 400}).status_code == 200

        assert store["set_value"].on_loop == [False]
        assert store["store_status"].on_loop == [False]


class TestDeletePreference:
    def test_delete_calls_clear_value_and_returns_fallback_entry(
        self, client, store, registry
    ):
        pref = registry[0]
        entry = _entry_for(pref, source="default")
        store["clear_value"].result = _FakeResolved(entry)

        response = client.delete(f"/preferences/{pref.key}?updated_by=user_2abcDEF")

        assert response.status_code == 200
        assert response.json() == {"preference": entry, "store": STORE_OK}
        args, _ = store["clear_value"].calls[0]
        assert args == (pref.key, "user_2abcDEF"), (
            "the trusted hop's forwarded operator id is the real principal"
        )

    def test_delete_is_idempotent_when_no_row_exists(self, client, store, registry):
        """Resetting a key with no stored override is a success, not an error."""
        pref = registry[0]
        store["clear_value"].result = _FakeResolved(_entry_for(pref, source="default"))

        response = client.delete(f"/preferences/{pref.key}")

        assert response.status_code == 200
        assert response.json()["preference"]["source"] == "default"
        args, _ = store["clear_value"].calls[0]
        assert args == (pref.key, "trusted-local")

    def test_delete_unknown_key_returns_404(self, client, store, prefs):
        store["clear_value"].raises = prefs.PreferenceValidationError(
            "UNKNOWN_PREFERENCE", "unknown preference key 'FOO'", key="FOO"
        )

        response = client.delete("/preferences/FOO")

        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "UNKNOWN_PREFERENCE"

    def test_delete_store_failure_returns_503(self, client, store, prefs, registry):
        store["clear_value"].raises = prefs.PreferenceStoreError("turso unreachable")

        response = client.delete(f"/preferences/{registry[0].key}")

        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "PREFERENCE_STORE_UNAVAILABLE"

    def test_delete_is_offloaded_from_the_event_loop(self, client, store, registry):
        pref = registry[0]
        store["clear_value"].result = _FakeResolved(_entry_for(pref, source="default"))

        assert client.delete(f"/preferences/{pref.key}").status_code == 200

        assert store["clear_value"].on_loop == [False]


class TestPreferencesPerimeter:
    def test_preferences_paths_are_not_auth_exempt(self):
        """These routes are operator-only; the global middleware must gate them."""
        from scripts.api.server import AUTH_EXEMPT_PATHS

        assert "/preferences" not in AUTH_EXEMPT_PATHS
        assert "/preferences/{key}" not in AUTH_EXEMPT_PATHS

    def test_routes_are_registered_on_the_app(self):
        from scripts.api.server import app

        paths = {
            (route.path, verb)
            for route in app.routes
            for verb in getattr(route, "methods", set()) or set()
        }
        assert ("/preferences", "GET") in paths
        assert ("/preferences/{key}", "PUT") in paths
        assert ("/preferences/{key}", "DELETE") in paths

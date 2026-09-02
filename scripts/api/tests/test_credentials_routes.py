"""Route tests for the operator credentials surface (/credentials).

The store is a real SecretStore on tmp paths (cheap local sqlite — no Turso),
the validator is stubbed on the route module. Wire discipline: an invalid
verdict must store NOTHING and return 422; plaintext must never appear in any
response body.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from credential_validators import ValidationResult  # noqa: E402
from secret_store import SecretStore  # noqa: E402


def _route_module_instances():
    from scripts.api.routes import credentials as credentials_module

    modules = [credentials_module]
    doppelganger = sys.modules.get("api.routes.credentials")
    if doppelganger is not None and doppelganger is not credentials_module:
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


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setenv("RADON_SECRET_STORE_PATH", str(tmp_path / "secrets.db"))
    monkeypatch.setenv(
        "RADON_SECRET_STORE_KEY_FILE", str(tmp_path / "secret_store.key")
    )
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    return tmp_path


@pytest.fixture()
def valid_verdict(monkeypatch):
    calls = []

    def _validate(service_id, values):
        calls.append((service_id, dict(values)))
        return ValidationResult("valid", "")

    for module in _route_module_instances():
        monkeypatch.setattr(module.credential_validators, "validate", _validate)
    return calls


@pytest.fixture()
def invalid_verdict(monkeypatch):
    def _validate(service_id, values):
        return ValidationResult("invalid", "the vendor said no")

    for module in _route_module_instances():
        monkeypatch.setattr(module.credential_validators, "validate", _validate)


@pytest.fixture
def client():
    # No `with` (no lifespan): these tests exercise the route handlers only,
    # and running the lifespan would leak RADON_API_TEST_MODE process-wide
    # (the documented test-ordering pollution class in this suite).
    from scripts.api.server import app

    return TestClient(app)


def _store() -> SecretStore:
    return SecretStore()


class TestList:
    def test_lists_every_registry_service(self, client):
        response = client.get("/credentials")
        assert response.status_code == 200
        payload = response.json()
        ids = {service["id"] for service in payload["services"]}
        assert {"anthropic", "unusual_whales", "turso", "menthorq"} <= ids
        assert payload["groups"]
        assert payload["generated_at"]

    def test_configured_field_reports_hint_never_value(self, client):
        _store().set_secret("UW_TOKEN", "uw-supersecret-value", actor="test")
        response = client.get("/credentials")
        body = response.text
        assert "uw-supersecret-value" not in body
        uw = next(
            service
            for service in response.json()["services"]
            if service["id"] == "unusual_whales"
        )
        (field,) = uw["fields"]
        assert field["configured"] is True
        assert field["hint"].endswith("alue")
        assert "value" not in field

    def test_env_fallback_flagged(self, client, monkeypatch):
        monkeypatch.setenv("UW_TOKEN", "from-dotenv")
        response = client.get("/credentials")
        uw = next(
            service
            for service in response.json()["services"]
            if service["id"] == "unusual_whales"
        )
        (field,) = uw["fields"]
        assert field["configured"] is False
        assert field["env_fallback"] is True
        assert field["exported_only"] is False

    def test_exported_only_after_delete(self, client, valid_verdict, monkeypatch):
        monkeypatch.delenv("UW_TOKEN", raising=False)
        client.put(
            "/credentials/unusual_whales",
            json={"values": {"UW_TOKEN": "uw-live-token"}, "updated_by": "op-1"},
        )
        client.delete("/credentials/unusual_whales/UW_TOKEN")
        import os

        assert os.environ.get("UW_TOKEN") == "uw-live-token"
        response = client.get("/credentials")
        uw = next(
            service
            for service in response.json()["services"]
            if service["id"] == "unusual_whales"
        )
        (field,) = uw["fields"]
        assert field["configured"] is False
        assert field["exported_only"] is True
        assert field["env_fallback"] is False


class TestPut:
    def test_valid_saves_and_applies_env(self, client, valid_verdict, monkeypatch):
        monkeypatch.delenv("UW_TOKEN", raising=False)
        response = client.put(
            "/credentials/unusual_whales",
            json={"values": {"UW_TOKEN": "uw-brand-new-token"}, "updated_by": "op-1"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["validation"]["status"] == "valid"
        assert "uw-brand-new-token" not in response.text
        assert _store().get_secret("UW_TOKEN") == "uw-brand-new-token"
        import os

        assert os.environ.get("UW_TOKEN") == "uw-brand-new-token"
        (call,) = valid_verdict
        assert call[0] == "unusual_whales"
        assert call[1]["UW_TOKEN"] == "uw-brand-new-token"

    def test_invalid_stores_nothing_and_422s(self, client, invalid_verdict, monkeypatch):
        monkeypatch.delenv("UW_TOKEN", raising=False)
        response = client.put(
            "/credentials/unusual_whales",
            json={"values": {"UW_TOKEN": "uw-bad-token"}},
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "CREDENTIAL_REJECTED"
        assert detail["message"] == "the vendor said no"
        assert _store().get_secret("UW_TOKEN") is None
        import os

        assert os.environ.get("UW_TOKEN") is None

    def test_partial_submit_merges_stored_values(self, client, valid_verdict):
        _store().set_secret("MENTHORQ_USER", "u@x.com", actor="test")
        response = client.put(
            "/credentials/menthorq",
            json={"values": {"MENTHORQ_PASS": "new-pass-123"}},
        )
        assert response.status_code == 200
        (call,) = valid_verdict
        assert call[1] == {"MENTHORQ_USER": "u@x.com", "MENTHORQ_PASS": "new-pass-123"}

    def test_unknown_service_404(self, client, valid_verdict):
        response = client.put(
            "/credentials/nope", json={"values": {"X_KEY": "v"}}
        )
        assert response.status_code == 404

    def test_field_not_in_service_400(self, client, valid_verdict):
        response = client.put(
            "/credentials/unusual_whales",
            json={"values": {"ANTHROPIC_API_KEY": "sk-x"}},
        )
        assert response.status_code == 400

    def test_empty_or_non_string_values_400(self, client, valid_verdict):
        for bad in ({}, {"UW_TOKEN": ""}, {"UW_TOKEN": 42}):
            response = client.put(
                "/credentials/unusual_whales", json={"values": bad}
            )
            assert response.status_code == 400, bad

    def test_error_verdict_saves_anyway(self, client, monkeypatch):
        """A vendor outage must not lock the operator out of saving."""
        for module in _route_module_instances():
            monkeypatch.setattr(
                module.credential_validators,
                "validate",
                lambda service_id, values: ValidationResult("error", "vendor 503"),
            )
        response = client.put(
            "/credentials/unusual_whales",
            json={"values": {"UW_TOKEN": "uw-token-during-outage"}},
        )
        assert response.status_code == 200
        assert response.json()["validation"]["status"] == "error"
        assert _store().get_secret("UW_TOKEN") == "uw-token-during-outage"


class TestValidateDryRun:
    def test_validate_does_not_write(self, client, valid_verdict):
        response = client.post(
            "/credentials/unusual_whales/validate",
            json={"values": {"UW_TOKEN": "uw-dry-run-token"}},
        )
        assert response.status_code == 200
        assert response.json()["validation"]["status"] == "valid"
        assert _store().get_secret("UW_TOKEN") is None

    def test_validate_merges_stored(self, client, valid_verdict):
        _store().set_secret("UW_TOKEN", "uw-already-stored", actor="test")
        response = client.post(
            "/credentials/unusual_whales/validate", json={}
        )
        assert response.status_code == 200
        (call,) = valid_verdict
        assert call[1]["UW_TOKEN"] == "uw-already-stored"


class TestValidateEgressPin:
    """Full flow with the REAL validator: a /validate body carrying ONLY a
    destination URL merges the stored/env token, so the probe destination is
    an egress decision — a non-Turso URL must refuse before any wire call."""

    def test_url_only_body_with_env_token_refuses_without_egress(
        self, client, monkeypatch
    ):
        monkeypatch.setenv("TURSO_AUTH_TOKEN", "SYNTHETIC-STORED-TOKEN-NOT-REAL")
        calls = []

        def _wire(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("credential egress attempted")

        seen = set()
        for module in _route_module_instances():
            cv_mod = module.credential_validators
            if id(cv_mod) in seen:
                continue
            seen.add(id(cv_mod))
            monkeypatch.setattr(cv_mod.requests, "post", _wire)
            monkeypatch.setattr(cv_mod.requests, "get", _wire)

        resp = client.post(
            "/credentials/turso/validate",
            json={"values": {"TURSO_DB_URL": "https://collector.example.net"}},
        )
        assert resp.status_code == 200
        assert resp.json()["validation"]["status"] == "invalid"
        assert calls == []


class TestDelete:
    def test_delete_removes_field(self, client):
        _store().set_secret("UW_TOKEN", "uw-to-delete", actor="test")
        response = client.delete("/credentials/unusual_whales/UW_TOKEN")
        assert response.status_code == 200
        assert _store().get_secret("UW_TOKEN") is None

    def test_delete_field_not_in_service_400(self, client):
        response = client.delete("/credentials/unusual_whales/ANTHROPIC_API_KEY")
        assert response.status_code == 400

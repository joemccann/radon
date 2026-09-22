"""F20260905-C06: a credential value os.environ cannot hold must be rejected
at the validation chokepoint, and a legacy stored row that slips past it must
never abort the lifespan bootstrap.

The store persisted BEFORE the env export, and the export raised for a NUL or
lone-surrogate value — so the poison row was saved, and the boot-time export
then raised inside the FastAPI lifespan on every restart.
"""
from __future__ import annotations

import os
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
    monkeypatch.delenv("UW_TOKEN", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def reset_validator_throttle(monkeypatch):
    for module in _route_module_instances():
        monkeypatch.setattr(module, "_validator_last_run", {}, raising=False)
        monkeypatch.setattr(module, "_validator_slots", None, raising=False)


@pytest.fixture()
def valid_verdict(monkeypatch):
    def _validate(service_id, values):
        return ValidationResult("valid", "")

    for module in _route_module_instances():
        monkeypatch.setattr(module.credential_validators, "validate", _validate)


@pytest.fixture
def client():
    # No `with` (no lifespan) — same convention as test_credentials_routes.py.
    from scripts.api.server import app

    return TestClient(app)


def _store() -> SecretStore:
    return SecretStore()


class TestUnencodableValueIsRejectedBeforePersist:
    def test_nul_byte_value_is_400_and_nothing_persisted(
        self, client, valid_verdict
    ):
        resp = client.put(
            "/credentials/unusual_whales",
            json={"values": {"UW_TOKEN": "AB\x00C"}},
        )
        assert resp.status_code == 400
        assert _store().get_secret("UW_TOKEN") is None, (
            "a value the environment cannot hold was persisted"
        )
        assert "UW_TOKEN" not in os.environ

    def test_lone_surrogate_value_is_400_and_nothing_persisted(
        self, client, valid_verdict
    ):
        # Sent as the raw JSON escape — json.loads turns \ud800 into a lone
        # surrogate the way a real request body would.
        resp = client.put(
            "/credentials/unusual_whales",
            content=b'{"values": {"UW_TOKEN": "AB\\ud800C"}}',
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert _store().get_secret("UW_TOKEN") is None
        assert "UW_TOKEN" not in os.environ


class TestBootstrapSurvivesAPoisonRow:
    def test_pre_seeded_nul_row_is_skipped_not_fatal(self, tmp_path):
        from scripts.api.routes.credentials import bootstrap_exported_names

        store = _store()
        # A legacy row saved before the chokepoint validation existed.
        store.set_secret("UW_TOKEN", "AB\x00C", actor="operator")
        store.set_secret("EXA_API_KEY", "exa-healthy-value", actor="operator")

        exported = bootstrap_exported_names()  # must not raise

        assert "EXA_API_KEY" in exported
        assert "UW_TOKEN" not in exported
        assert os.environ.get("EXA_API_KEY") == "exa-healthy-value"
        assert "UW_TOKEN" not in os.environ

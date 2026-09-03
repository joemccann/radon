"""T-390: the "store wins over .env" contract must hold when a STALE env
value already exists.

Every prior test deleted the conflicting env var before exercising the
export paths, so inverting the export to `if name not in os.environ` (a
rotated credential silently doing nothing until reboot) kept the suite
green. These tests set a stale env value FIRST and assert the stored value
overwrites it, on both export paths:

- bootstrap_exported_names() (lifespan boot, credentials.py ~line 347)
- PUT /credentials/{service_id} (_save, credentials.py ~line 266)
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

STALE = "uw-stale-dotenv-value"
STORED = "uw-rotated-store-value"


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
def isolate_session_exported():
    # bootstrap/PUT add names to the module-global _SESSION_EXPORTED; restore
    # it so later files (test_credentials_routes.py::test_env_fallback_flagged)
    # still see env-only names as fallback, not exported.
    saved = {
        module: set(module._SESSION_EXPORTED) for module in _route_module_instances()
    }
    yield
    for module, names in saved.items():
        module._SESSION_EXPORTED.clear()
        module._SESSION_EXPORTED.update(names)


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
    def _validate(service_id, values):
        return ValidationResult("valid", "")

    for module in _route_module_instances():
        monkeypatch.setattr(module.credential_validators, "validate", _validate)


@pytest.fixture
def client():
    # No lifespan on purpose (same as test_credentials_routes.py): route
    # handlers only, no RADON_API_TEST_MODE leak.
    from scripts.api.server import app

    return TestClient(app)


class TestStoreWinsOverEnv:
    def test_bootstrap_overwrites_stale_env_value(self, monkeypatch):
        monkeypatch.setenv("UW_TOKEN", STALE)
        SecretStore().set_secret("UW_TOKEN", STORED, actor="operator")

        from scripts.api.routes.credentials import bootstrap_exported_names

        exported = bootstrap_exported_names()

        assert "UW_TOKEN" in exported
        assert os.environ["UW_TOKEN"] == STORED, (
            "store must win over .env: a stale pre-existing env value "
            "survived bootstrap_exported_names()"
        )

    def test_put_overwrites_stale_env_value(self, client, valid_verdict, monkeypatch):
        monkeypatch.setenv("UW_TOKEN", STALE)

        response = client.put(
            "/credentials/unusual_whales",
            json={"values": {"UW_TOKEN": STORED}, "updated_by": "op-1"},
        )

        assert response.status_code == 200
        assert SecretStore().get_secret("UW_TOKEN") == STORED
        assert os.environ["UW_TOKEN"] == STORED, (
            "a rotated credential must apply to the running process even "
            "when a stale env value already exists"
        )

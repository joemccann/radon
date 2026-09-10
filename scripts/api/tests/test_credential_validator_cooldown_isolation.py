"""The per-service credential validation cooldown must not leak across tests.

`routes.credentials._validator_last_run` is process-wide: every accepted
PUT /credentials/{service_id} stamps the service, and a second PUT for the
same service inside VALIDATOR_COOLDOWN_S (5 s) is a 429 VALIDATION_COOLDOWN.
Only test_credentials_routes.py reset it; test_t390_store_wins_over_env.py
and test_rel189_credentials_routes_degraded.py PUT `unusual_whales` too, so
under `pytest -n auto --dist loadfile` the file-to-worker assignment decided
who ran second and got the 429 (2026-09-03, run 33775920298:
test_put_overwrites_stale_env_value asserted 200 and got 429).

These two cases pin the isolation: the first deliberately arms the cooldown,
the second must still be handed a clean one.
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

SERVICE = "unusual_whales"


def _route_module_instances():
    from scripts.api.routes import credentials as credentials_module

    modules = [credentials_module]
    doppelganger = sys.modules.get("api.routes.credentials")
    if doppelganger is not None and doppelganger is not credentials_module:
        modules.append(doppelganger)
    return modules


@pytest.fixture
def trusted_client(monkeypatch, tmp_path):
    from scripts.api import auth, server

    monkeypatch.setenv("RADON_SECRET_STORE_PATH", str(tmp_path / "secrets.db"))
    monkeypatch.setenv(
        "RADON_SECRET_STORE_KEY_FILE", str(tmp_path / "secret_store.key")
    )
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    for module in _route_module_instances():
        monkeypatch.setattr(
            module, "is_trusted_local_request", lambda request: True, raising=False
        )
        monkeypatch.setattr(
            module.credential_validators,
            "validate",
            lambda service_id, values: ValidationResult("valid", ""),
        )
    return TestClient(server.app)


def _put(client):
    return client.put(
        f"/credentials/{SERVICE}",
        json={"values": {"UW_TOKEN": "uw-cooldown-probe"}, "updated_by": "op-1"},
    )


def test_a_case_may_arm_the_validation_cooldown(trusted_client):
    assert _put(trusted_client).status_code == 200

    refused = _put(trusted_client)
    assert refused.status_code == 429
    assert refused.json()["detail"]["code"] == "VALIDATION_COOLDOWN"


def test_the_next_case_starts_from_a_clean_cooldown(trusted_client):
    assert _put(trusted_client).status_code == 200

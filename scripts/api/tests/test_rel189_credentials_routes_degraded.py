"""REL-189 route half: rotation survives a corrupted sibling row, and store
I/O failures surface as the crafted 503, never a raw 500."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from credential_validators import ValidationResult  # noqa: E402
from secret_store import SecretStore  # noqa: E402

MENTHORQ_PASS = "-".join(("mq", "pass", "1234"))
MENTHORQ_USER = "operator@example.com"


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


@pytest.fixture
def client():
    from scripts.api.server import app

    return TestClient(app)


def _seed(tmp_path) -> SecretStore:
    store = SecretStore(
        db_path=tmp_path / "secrets.db", key_path=tmp_path / "secret_store.key"
    )
    store.set_secret("MENTHORQ_USER", MENTHORQ_USER, actor="operator")
    store.set_secret("MENTHORQ_PASS", MENTHORQ_PASS, actor="operator")
    return store


def _corrupt(tmp_path, name):
    conn = sqlite3.connect(tmp_path / "secrets.db")
    conn.execute(
        "UPDATE secrets SET ciphertext = X'DEADBEEF' WHERE name = ?", (name,)
    )
    conn.commit()
    conn.close()


class TestRotationSurvivesACorruptSibling:
    def test_put_one_field_with_corrupt_sibling_is_not_a_500(
        self, client, tmp_path, valid_verdict
    ):
        _seed(tmp_path)
        _corrupt(tmp_path, "MENTHORQ_USER")
        response = client.put(
            "/credentials/menthorq",
            json={"values": {"MENTHORQ_PASS": "-".join(("new", "pass", "5678"))}},
        )
        assert response.status_code == 200, response.text
        merged = valid_verdict[0][1]
        assert merged["MENTHORQ_PASS"] == "-".join(("new", "pass", "5678"))
        assert "MENTHORQ_USER" not in merged  # corrupt row treated as absent


class TestStoreIoFailuresAre503:
    def test_unopenable_store_is_the_crafted_503_on_put(
        self, client, tmp_path, valid_verdict, monkeypatch
    ):
        (tmp_path / "secrets.db").mkdir()  # unopenable: raw sqlite error pre-fix
        response = client.put(
            "/credentials/unusual_whales",
            json={"values": {"UW_TOKEN": "-".join(("uw", "tok", "1234"))}},
        )
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "CREDENTIAL_STORE_UNAVAILABLE"

    def test_key_mismatch_is_the_crafted_503_not_a_raw_500(
        self, client, tmp_path, valid_verdict
    ):
        _seed(tmp_path)
        (tmp_path / "secret_store.key").unlink()
        response = client.put(
            "/credentials/menthorq",
            json={"values": {"MENTHORQ_PASS": "-".join(("new", "pass", "9999"))}},
        )
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "CREDENTIAL_STORE_UNAVAILABLE"
        assert not (tmp_path / "secret_store.key").exists(), "nothing minted"

    def test_get_list_with_unopenable_store_is_503(self, client, tmp_path):
        (tmp_path / "secrets.db").mkdir()
        response = client.get("/credentials")
        assert response.status_code == 503, response.text


class TestOsErrorConstructorFailuresAre503:
    """REL-217 (R-592): OSError-class constructor failures (key-file path is
    a directory) were raw 500s, violating the R-521/R-522 contract."""

    def test_key_file_as_directory_is_a_503(self, tmp_path, monkeypatch, client):
        key_dir = tmp_path / "keydir"
        key_dir.mkdir()
        monkeypatch.setenv("RADON_SECRET_STORE_DB", str(tmp_path / "secrets.db"))
        monkeypatch.setenv("RADON_SECRET_STORE_KEY_FILE", str(key_dir))
        response = client.get("/credentials")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "CREDENTIAL_STORE_UNAVAILABLE"

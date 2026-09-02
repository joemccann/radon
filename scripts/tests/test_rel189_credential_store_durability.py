"""REL-189 (R-521, R-522, R-538, R-539): the credential store degrades per-row,
binds its key, and radon-api always boots.

Pre-fix: one corrupted row crashed radon-api at boot forever (the export loop
sat outside the bootstrap try); a missing key file silently minted a new key
and orphaned every stored credential; a corrupted sibling row 500'd rotation
of a healthy field; raw sqlite errors bypassed the CREDENTIAL_STORE_UNAVAILABLE
503 path; and a mid-save failure left earlier fields stored AND exported.
"""
import logging
import os
import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from secret_store import (  # noqa: E402
    SecretKeyMismatchError,
    SecretStore,
    SecretStoreError,
    SecretValidationError,
)

UW_SAMPLE = "-".join(("uw", "abc123secret"))
EXA_SAMPLE = "-".join(("exa", "def456value"))


def _store(tmp_path):
    return SecretStore(
        db_path=tmp_path / "secrets.db", key_path=tmp_path / "secret_store.key"
    )


def _corrupt_row(tmp_path, name):
    conn = sqlite3.connect(tmp_path / "secrets.db")
    conn.execute(
        "UPDATE secrets SET ciphertext = X'DEADBEEF' WHERE name = ?", (name,)
    )
    conn.commit()
    conn.close()


class TestKeyBinding:
    def test_missing_key_file_with_rows_refuses_to_mint(self, tmp_path):
        store = _store(tmp_path)
        store.set_secret("UW_TOKEN", UW_SAMPLE, actor="operator")
        (tmp_path / "secret_store.key").unlink()
        with pytest.raises(SecretKeyMismatchError):
            _store(tmp_path)
        assert not (tmp_path / "secret_store.key").exists(), (
            "a new key was minted over rows encrypted under the lost key"
        )

    def test_replaced_key_file_refuses_writes(self, tmp_path):
        store = _store(tmp_path)
        store.set_secret("UW_TOKEN", UW_SAMPLE, actor="operator")
        (tmp_path / "secret_store.key").write_bytes(os.urandom(32))
        second = _store(tmp_path)
        with pytest.raises(SecretKeyMismatchError):
            second.set_secret("EXA_API_KEY", EXA_SAMPLE, actor="operator")

    def test_missing_key_with_empty_db_still_mints(self, tmp_path):
        store = _store(tmp_path)
        assert store.get_secret("UW_TOKEN") is None
        assert (tmp_path / "secret_store.key").exists()

    def test_o_excl_race_loser_reads_the_winners_key(self, tmp_path, monkeypatch):
        first = _store(tmp_path)
        first.set_secret("UW_TOKEN", UW_SAMPLE, actor="operator")
        # Simulate losing the create race: is_file says absent, O_EXCL loses.
        real_is_file = Path.is_file

        def race_is_file(self):
            if self.name == "secret_store.key":
                return False
            return real_is_file(self)

        monkeypatch.setattr(Path, "is_file", race_is_file)
        second = _store(tmp_path)
        assert second.get_secret("UW_TOKEN") == UW_SAMPLE


class TestSqliteErrorsAreStoreErrors:
    def test_unopenable_db_raises_store_error_not_sqlite(self, tmp_path):
        (tmp_path / "secrets.db").mkdir()  # a directory is unopenable
        store = SecretStore(
            db_path=tmp_path / "secrets.db",
            key_path=tmp_path / "secret_store.key",
        )
        with pytest.raises(SecretStoreError):
            store.get_secret("UW_TOKEN")
        with pytest.raises(SecretStoreError):
            store.set_secret("UW_TOKEN", UW_SAMPLE, actor="operator")
        with pytest.raises(SecretStoreError):
            store.list_secrets()


class TestAtomicMultiFieldSave:
    def test_set_secrets_writes_all_in_one_transaction(self, tmp_path):
        store = _store(tmp_path)
        store.set_secrets(
            {"MENTHORQ_USER": "user@example.com", "MENTHORQ_PASS": UW_SAMPLE},
            actor="operator",
        )
        assert store.get_secret("MENTHORQ_USER") == "user@example.com"
        assert store.get_secret("MENTHORQ_PASS") == UW_SAMPLE

    def test_one_invalid_field_stores_nothing(self, tmp_path):
        store = _store(tmp_path)
        with pytest.raises(SecretValidationError):
            store.set_secrets(
                {"MENTHORQ_USER": "user@example.com", "MENTHORQ_PASS": ""},
                actor="operator",
            )
        assert store.get_secret("MENTHORQ_USER") is None


class TestBootSurvivesACorruptedRow:
    def _bootstrap(self):
        from scripts.api.routes.credentials import bootstrap_exported_names

        return bootstrap_exported_names()

    def test_corrupt_row_is_skipped_and_named_others_export(
        self, tmp_path, monkeypatch, caplog
    ):
        monkeypatch.setenv("RADON_SECRET_STORE_PATH", str(tmp_path / "secrets.db"))
        monkeypatch.setenv(
            "RADON_SECRET_STORE_KEY_FILE", str(tmp_path / "secret_store.key")
        )
        monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
        monkeypatch.delenv("UW_TOKEN", raising=False)
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        store = _store(tmp_path)
        store.set_secret("UW_TOKEN", UW_SAMPLE, actor="operator")
        store.set_secret("EXA_API_KEY", EXA_SAMPLE, actor="operator")
        _corrupt_row(tmp_path, "UW_TOKEN")
        with caplog.at_level(logging.WARNING, logger="radon.credentials"):
            exported = self._bootstrap()
        assert "EXA_API_KEY" in exported
        assert "UW_TOKEN" not in exported
        assert os.environ.get("EXA_API_KEY") == EXA_SAMPLE
        assert "UW_TOKEN" not in os.environ
        assert any("UW_TOKEN" in record.message for record in caplog.records), (
            "the skipped row must be named in the log"
        )

    def test_unconstructible_store_is_skipped_not_fatal(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RADON_SECRET_STORE_PATH", str(tmp_path / "secrets.db"))
        monkeypatch.setenv(
            "RADON_SECRET_STORE_KEY_FILE", str(tmp_path / "secret_store.key")
        )
        monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
        store = _store(tmp_path)
        store.set_secret("UW_TOKEN", UW_SAMPLE, actor="operator")
        (tmp_path / "secret_store.key").unlink()  # key mismatch at construction
        assert self._bootstrap() == []

"""Secret store contract (scripts/secret_store.py).

Host-local encrypted credential store: AES-256-GCM values in a local SQLite
file, master key auto-generated 0600 (systemd LoadCredential preferred).
Secrets deliberately never touch Turso — they stay on the host that uses them.
"""

import os
import sqlite3
import stat

import pytest

from secret_store import (
    SecretIntegrityError,
    SecretStore,
    SecretStoreError,
    SecretValidationError,
)



# Fixture values are BUILT at runtime: a token-shaped literal on the same
# line as a *_TOKEN/*_KEY identifier trips the gitleaks generic-api-key rule
# (Secret scan CI job), and these are synthetic samples, not credentials.
UW_SAMPLE = "-".join(("uw", "abc123secret"))
UW_LONG = "-".join(("uw", "abc123", "long", "enough", "secret"))
UW_VISIBLE = "-".join(("uw", "plainly", "visible", "token"))
UW_SWAP = "-".join(("value", "for", "uw", "1234"))
EXA_FIRST = "-".join(("first", "value", "1234"))
EXA_SECOND = "-".join(("second", "value", "5678"))
EXA_SWAP = "-".join(("value", "for", "exa", "5678"))
GENERIC_SAMPLE = "-".join(("some", "value", "1234"))

@pytest.fixture()
def store(tmp_path):
    return SecretStore(
        db_path=tmp_path / "secrets.db",
        key_path=tmp_path / "secret_store.key",
    )


class TestRoundtrip:
    def test_set_then_get_returns_plaintext(self, store):
        store.set_secret("UW_TOKEN", UW_SAMPLE, actor="operator")
        assert store.get_secret("UW_TOKEN") == UW_SAMPLE

    def test_get_missing_returns_none(self, store):
        assert store.get_secret("ANTHROPIC_API_KEY") is None

    def test_overwrite_replaces_value_and_bumps_version(self, store):
        store.set_secret("EXA_API_KEY", EXA_FIRST, actor="operator")
        store.set_secret("EXA_API_KEY", EXA_SECOND, actor="operator")
        assert store.get_secret("EXA_API_KEY") == EXA_SECOND
        (entry,) = store.list_secrets()
        assert entry["version"] == 2

    def test_delete_removes_secret(self, store):
        store.set_secret("UW_TOKEN", UW_SAMPLE, actor="operator")
        store.delete_secret("UW_TOKEN", actor="operator")
        assert store.get_secret("UW_TOKEN") is None
        assert store.list_secrets() == []

    def test_delete_missing_is_noop(self, store):
        store.delete_secret("UW_TOKEN", actor="operator")
        assert store.list_secrets() == []

    def test_has_secret(self, store):
        assert store.has_secret("UW_TOKEN") is False
        store.set_secret("UW_TOKEN", UW_SAMPLE, actor="operator")
        assert store.has_secret("UW_TOKEN") is True


class TestListNeverLeaksPlaintext:
    def test_list_masks_value_to_last_four(self, store):
        store.set_secret("UW_TOKEN", UW_LONG, actor="operator")
        (entry,) = store.list_secrets()
        assert entry["name"] == "UW_TOKEN"
        assert entry["hint"] == "\u2022\u2022\u2022\u2022cret"
        assert "value" not in entry
        assert UW_LONG not in str(entry)

    def test_short_values_fully_masked(self, store):
        store.set_secret("PIN", "1234567", actor="operator")
        (entry,) = store.list_secrets()
        assert entry["hint"] == "\u2022\u2022\u2022\u2022"
        assert "1234" not in entry["hint"]

    def test_list_carries_metadata(self, store):
        store.set_secret("UW_TOKEN", UW_SAMPLE, actor="op-1")
        (entry,) = store.list_secrets()
        assert entry["updated_by"] == "op-1"
        assert entry["updated_at"]
        assert entry["version"] == 1


class TestEncryptionAtRest:
    def test_plaintext_absent_from_db_file(self, store, tmp_path):
        store.set_secret("UW_TOKEN", UW_VISIBLE, actor="operator")
        raw = (tmp_path / "secrets.db").read_bytes()
        assert UW_VISIBLE.encode("utf-8") not in raw

    def test_ciphertext_bound_to_name(self, store, tmp_path):
        """AAD = name: swapping ciphertext rows must fail decryption."""
        store.set_secret("UW_TOKEN", UW_SWAP, actor="operator")
        store.set_secret("EXA_API_KEY", EXA_SWAP, actor="operator")
        conn = sqlite3.connect(tmp_path / "secrets.db")
        row = conn.execute(
            "SELECT ciphertext, nonce FROM secrets WHERE name = 'UW_TOKEN'"
        ).fetchone()
        conn.execute(
            "UPDATE secrets SET ciphertext = ?, nonce = ? WHERE name = 'EXA_API_KEY'",
            row,
        )
        conn.commit()
        conn.close()
        with pytest.raises(SecretIntegrityError):
            store.get_secret("EXA_API_KEY")

    def test_key_file_created_0600(self, store, tmp_path):
        store.set_secret("UW_TOKEN", UW_SAMPLE, actor="operator")
        mode = stat.S_IMODE(os.stat(tmp_path / "secret_store.key").st_mode)
        assert mode == 0o600

    def test_db_file_created_0600(self, store, tmp_path):
        store.set_secret("UW_TOKEN", UW_SAMPLE, actor="operator")
        mode = stat.S_IMODE(os.stat(tmp_path / "secrets.db").st_mode)
        assert mode == 0o600

    def test_existing_loose_permissions_reasserted_0600(self, tmp_path):
        db = tmp_path / "secrets.db"
        key = tmp_path / "k.key"
        first = SecretStore(db_path=db, key_path=key)
        first.set_secret("UW_TOKEN", UW_SAMPLE, actor="operator")
        os.chmod(db, 0o644)
        os.chmod(key, 0o644)
        second = SecretStore(db_path=db, key_path=key)
        assert second.get_secret("UW_TOKEN") == UW_SAMPLE
        assert stat.S_IMODE(os.stat(db).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(key).st_mode) == 0o600

    def test_second_store_instance_reuses_key(self, tmp_path):
        first = SecretStore(
            db_path=tmp_path / "secrets.db", key_path=tmp_path / "k.key"
        )
        first.set_secret("UW_TOKEN", UW_SAMPLE, actor="operator")
        second = SecretStore(
            db_path=tmp_path / "secrets.db", key_path=tmp_path / "k.key"
        )
        assert second.get_secret("UW_TOKEN") == UW_SAMPLE

    def test_wrong_key_raises_integrity_error(self, tmp_path):
        # REL-189 rewrite, same intent (wrong key must never decrypt): the
        # key file now EXISTS with wrong bytes. The old shape (a missing
        # second key path over a DB with rows) is the R-522 mint-refusal
        # path and raises SecretKeyMismatchError at construction instead —
        # covered by test_rel189_credential_store_durability.py.
        first = SecretStore(
            db_path=tmp_path / "secrets.db", key_path=tmp_path / "k1.key"
        )
        first.set_secret("UW_TOKEN", UW_SAMPLE, actor="operator")
        (tmp_path / "k2.key").write_bytes(os.urandom(32))
        second = SecretStore(
            db_path=tmp_path / "secrets.db", key_path=tmp_path / "k2.key"
        )
        with pytest.raises(SecretIntegrityError):
            second.get_secret("UW_TOKEN")


class TestSystemdCredential:
    def test_credentials_directory_key_preferred(self, tmp_path, monkeypatch):
        cred_dir = tmp_path / "creds"
        cred_dir.mkdir()
        key = os.urandom(32)
        (cred_dir / "radon-secret-store-key").write_bytes(key)
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(cred_dir))
        store = SecretStore(db_path=tmp_path / "secrets.db")
        store.set_secret("UW_TOKEN", UW_SAMPLE, actor="operator")
        assert store.get_secret("UW_TOKEN") == UW_SAMPLE
        # No fallback key file materialized anywhere under tmp_path
        assert not (tmp_path / "secret_store.key").exists()


class TestValidation:
    @pytest.mark.parametrize(
        "bad_name",
        ["", "lower_case", "1LEADING_DIGIT", "HAS-DASH", "HAS SPACE", "A" * 65],
    )
    def test_invalid_names_rejected(self, store, bad_name):
        with pytest.raises(SecretValidationError):
            store.set_secret(bad_name, GENERIC_SAMPLE, actor="operator")

    def test_empty_value_rejected(self, store):
        with pytest.raises(SecretValidationError):
            store.set_secret("UW_TOKEN", "", actor="operator")

    def test_oversize_value_rejected(self, store):
        with pytest.raises(SecretValidationError):
            store.set_secret("UW_TOKEN", "x" * 8193, actor="operator")

    def test_non_string_value_rejected(self, store):
        with pytest.raises(SecretValidationError):
            store.set_secret("UW_TOKEN", 12345, actor="operator")


class TestAudit:
    def test_set_and_delete_are_audited(self, store, tmp_path):
        store.set_secret("UW_TOKEN", UW_SAMPLE, actor="op-1")
        store.delete_secret("UW_TOKEN", actor="op-2")
        conn = sqlite3.connect(tmp_path / "secrets.db")
        rows = conn.execute(
            "SELECT name, action, actor FROM secret_events ORDER BY id"
        ).fetchall()
        conn.close()
        assert rows == [("UW_TOKEN", "set", "op-1"), ("UW_TOKEN", "delete", "op-2")]

    def test_audit_rows_never_contain_value(self, store, tmp_path):
        store.set_secret("UW_TOKEN", UW_SAMPLE, actor="operator")
        conn = sqlite3.connect(tmp_path / "secrets.db")
        cols = [c[1] for c in conn.execute("PRAGMA table_info(secret_events)")]
        conn.close()
        assert "value" not in cols
        assert "old_value" not in cols
        assert "new_value" not in cols

    def test_noop_delete_not_audited(self, store, tmp_path):
        store.delete_secret("UW_TOKEN", actor="operator")
        conn = sqlite3.connect(tmp_path / "secrets.db")
        (count,) = conn.execute("SELECT COUNT(*) FROM secret_events").fetchone()
        conn.close()
        assert count == 0


class TestHintThreshold:
    """A password-length value must reveal nothing; only long, high-entropy
    values show their last four."""

    def test_nineteen_char_value_reveals_nothing(self, store):
        value = "-".join(("pw", "notreal", "value", "19"))
        assert len(value) == 19
        store.set_secret("GATEWAY_PASSWORD", value, actor="operator")
        (entry,) = store.list_secrets()
        assert entry["hint"] == "••••"

    def test_twenty_char_value_reveals_at_most_last_four(self, store):
        value = "-".join(("tok", "notreal", "value", "20"))
        assert len(value) == 20
        store.set_secret("UW_TOKEN", value, actor="operator")
        (entry,) = store.list_secrets()
        assert entry["hint"] == "••••" + value[-4:]
        assert value[:-4] not in entry["hint"]

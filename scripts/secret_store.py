#!/usr/bin/env python3
"""Host-local encrypted credential store (the profile Credentials tab backend).

API keys and login credentials are AES-256-GCM-encrypted values in a local
SQLite file that never leaves the host. This is deliberate: unlike scanner and
journal data, secrets must NOT be written to Turso — the store's whole purpose
is that the plaintext and its ciphertext stay on the machine that uses them
(operator decision 2026-09-01, PR #125).

Master key resolution, MOST -> LEAST preferred:

  1. systemd credential ``radon-secret-store-key`` in ``$CREDENTIALS_DIRECTORY``
     (production: ``LoadCredentialEncrypted=`` — TPM-sealed where available).
  2. Key file at ``key_path`` / ``$RADON_SECRET_STORE_KEY_FILE`` (default
     ``~/.radon/secret_store.key``), auto-generated 0600 on first use so a
     fresh clone works with zero manual backend steps.

Encryption is per-value random 96-bit nonce with the secret NAME as AAD, so a
ciphertext copied onto another row fails decryption instead of silently
decrypting to the wrong credential. Every mutation writes an audit row FIRST
(same transaction); audit rows never carry values, old or new.
"""

from __future__ import annotations

import functools
import hashlib
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DEFAULT_DB_PATH = Path.home() / ".radon" / "secrets.db"
DEFAULT_KEY_PATH = Path.home() / ".radon" / "secret_store.key"
SYSTEMD_CREDENTIAL_NAME = "radon-secret-store-key"

_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_VALUE_MAX_BYTES = 8192
_ACTOR_MAX_LEN = 64
_NONCE_BYTES = 12
_KEY_BYTES = 32

_SCHEMA = """
CREATE TABLE IF NOT EXISTS secrets (
    name TEXT PRIMARY KEY,
    ciphertext BLOB NOT NULL,
    nonce BLOB NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS secret_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS key_binding (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    fingerprint TEXT NOT NULL
);
"""


class SecretStoreError(RuntimeError):
    """Base error for store failures (I/O, key material)."""


class SecretValidationError(ValueError):
    """Rejected input: bad name, empty/oversize/non-string value."""


class SecretIntegrityError(SecretStoreError):
    """Ciphertext failed authentication: wrong key or tampered row."""


class SecretKeyMismatchError(SecretStoreError):
    """The DB is bound to a different master key than the one loaded.

    Raised instead of silently minting a fresh key over rows encrypted under
    a lost one (which would permanently orphan every stored credential), and
    instead of writing new-key ciphertext into a DB whose rows the loaded
    key cannot decrypt (which would split the store across two keys). The
    operator must choose: restore the old key, or delete the DB.
    """


def _sqlite_guarded(fn):
    """Raw sqlite failures (disk full, locked, read-only FS) become
    SecretStoreError so callers' store-unavailable handling is reachable."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except sqlite3.Error as exc:
            raise SecretStoreError(f"secret store I/O failed: {exc}") from exc

    return wrapper


def _ensure_private_file(path: Path) -> None:
    if not path.is_file():
        return
    mode = path.stat().st_mode & 0o777
    if mode != 0o600:
        os.chmod(path, 0o600)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mask(value: str) -> str:
    if len(value) >= 8:
        return "\u2022" * 4 + value[-4:]
    return "\u2022" * 4


class SecretStore:
    def __init__(
        self,
        db_path: Optional[Path] = None,
        key_path: Optional[Path] = None,
    ) -> None:
        self._db_path = Path(
            db_path
            or os.environ.get("RADON_SECRET_STORE_PATH")
            or DEFAULT_DB_PATH
        )
        self._key_path = Path(
            key_path
            or os.environ.get("RADON_SECRET_STORE_KEY_FILE")
            or DEFAULT_KEY_PATH
        )
        key = self._load_or_create_key()
        self._key_fingerprint = hashlib.sha256(key).hexdigest()
        self._aesgcm = AESGCM(key)

    # -- key material ------------------------------------------------------

    def _load_or_create_key(self) -> bytes:
        cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
        if cred_dir:
            cred_file = Path(cred_dir) / SYSTEMD_CREDENTIAL_NAME
            if cred_file.is_file():
                key = cred_file.read_bytes()
                if len(key) != _KEY_BYTES:
                    raise SecretStoreError(
                        f"systemd credential {SYSTEMD_CREDENTIAL_NAME} must be "
                        f"{_KEY_BYTES} bytes, got {len(key)}"
                    )
                return key
        if self._key_path.is_file():
            _ensure_private_file(self._key_path)
            key = self._key_path.read_bytes()
            if len(key) != _KEY_BYTES:
                raise SecretStoreError(
                    f"key file {self._key_path} must be {_KEY_BYTES} bytes, "
                    f"got {len(key)}"
                )
            return key
        # is_file said absent — re-read once before concluding the key is
        # gone, so losing the first-use create race hands us the winner's key.
        try:
            key = self._key_path.read_bytes()
        except OSError:
            pass
        else:
            if len(key) != _KEY_BYTES:
                raise SecretStoreError(
                    f"key file {self._key_path} must be {_KEY_BYTES} bytes, "
                    f"got {len(key)}"
                )
            return key
        self._refuse_mint_over_existing_rows()
        key = os.urandom(_KEY_BYTES)
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(
                self._key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError:
            # Lost the first-use create race: read the winner's key instead
            # of surfacing a raw FileExistsError (a 500 to the route).
            key = self._key_path.read_bytes()
            if len(key) != _KEY_BYTES:
                raise SecretStoreError(
                    f"key file {self._key_path} must be {_KEY_BYTES} bytes, "
                    f"got {len(key)}"
                )
            return key
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        return key

    def _refuse_mint_over_existing_rows(self) -> None:
        """A missing key file with stored rows means the key was LOST, not
        that this is first use. Minting here would orphan every credential
        under a fresh key with no warning (R-522)."""
        if not self._db_path.is_file():
            return
        try:
            conn = sqlite3.connect(self._db_path)
            try:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                has_rows = (
                    "secrets" in tables
                    and conn.execute("SELECT 1 FROM secrets LIMIT 1").fetchone()
                    is not None
                )
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise SecretStoreError(f"secret store I/O failed: {exc}") from exc
        if has_rows:
            raise SecretKeyMismatchError(
                f"key mismatch: {self._db_path} holds secrets but the master "
                f"key at {self._key_path} is missing; restore the key or "
                "delete the DB — refusing to mint a new key over them"
            )

    # -- connection --------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        existed = self._db_path.is_file()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        if existed:
            _ensure_private_file(self._db_path)
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.executescript(_SCHEMA)
        if not existed:
            os.chmod(self._db_path, 0o600)
        return conn

    def _assert_key_binding(self, conn: sqlite3.Connection) -> None:
        """Refuse to write under a key the DB is not bound to (R-522).

        A replaced key file (wrong restore, systemd-vs-CLI key split) would
        otherwise mix ciphertexts from two keys in one DB. A legacy DB with
        rows but no binding is bound to the current key only after proving
        the key decrypts an existing row.
        """
        row = conn.execute(
            "SELECT fingerprint FROM key_binding WHERE id = 1"
        ).fetchone()
        if row is not None:
            if row[0] != self._key_fingerprint:
                raise SecretKeyMismatchError(
                    f"key mismatch: {self._db_path} is bound to a different "
                    "master key than the one loaded; restore the original "
                    "key or delete the DB"
                )
            return
        sample = conn.execute(
            "SELECT name, ciphertext, nonce FROM secrets LIMIT 1"
        ).fetchone()
        if sample is not None:
            name, ciphertext, nonce = sample
            try:
                self._aesgcm.decrypt(
                    bytes(nonce), bytes(ciphertext), name.encode("utf-8")
                )
            except InvalidTag as exc:
                raise SecretKeyMismatchError(
                    f"key mismatch: the loaded key cannot decrypt existing "
                    f"rows in {self._db_path}; refusing to mix keys"
                ) from exc
        conn.execute(
            "INSERT OR IGNORE INTO key_binding (id, fingerprint) VALUES (1, ?)",
            (self._key_fingerprint,),
        )

    # -- validation --------------------------------------------------------

    @staticmethod
    def _check_name(name: Any) -> str:
        if not isinstance(name, str) or not _NAME_RE.match(name):
            raise SecretValidationError(
                "secret name must match ^[A-Z][A-Z0-9_]{0,63}$"
            )
        return name

    @staticmethod
    def _check_value(value: Any) -> str:
        if not isinstance(value, str):
            raise SecretValidationError("secret value must be a string")
        if not value:
            raise SecretValidationError("secret value must not be empty")
        if len(value.encode("utf-8")) > _VALUE_MAX_BYTES:
            raise SecretValidationError(
                f"secret value exceeds {_VALUE_MAX_BYTES} bytes"
            )
        return value

    @staticmethod
    def _check_actor(actor: Any) -> str:
        if not isinstance(actor, str) or not actor.strip():
            raise SecretValidationError("actor must be a non-empty string")
        return actor.strip()[:_ACTOR_MAX_LEN]

    # -- CRUD ----------------------------------------------------------------

    def set_secret(self, name: str, value: str, actor: str) -> None:
        self.set_secrets({name: value}, actor)

    @_sqlite_guarded
    def set_secrets(self, values: Dict[str, str], actor: str) -> None:
        """All-or-nothing multi-field write: validate everything, then one
        transaction. A failure on any field stores none of them (R-539)."""
        actor = self._check_actor(actor)
        items = [
            (self._check_name(name), self._check_value(value))
            for name, value in values.items()
        ]
        now = _now()
        conn = self._connect()
        try:
            with conn:
                self._assert_key_binding(conn)
                for name, value in items:
                    nonce = os.urandom(_NONCE_BYTES)
                    ciphertext = self._aesgcm.encrypt(
                        nonce, value.encode("utf-8"), name.encode("utf-8")
                    )
                    conn.execute(
                        "INSERT INTO secret_events (name, action, actor, at) "
                        "VALUES (?, 'set', ?, ?)",
                        (name, actor, now),
                    )
                    conn.execute(
                        "INSERT INTO secrets "
                        "(name, ciphertext, nonce, version, created_at, updated_at, updated_by) "
                        "VALUES (?, ?, ?, 1, ?, ?, ?) "
                        "ON CONFLICT(name) DO UPDATE SET "
                        "ciphertext = excluded.ciphertext, nonce = excluded.nonce, "
                        "version = secrets.version + 1, "
                        "updated_at = excluded.updated_at, "
                        "updated_by = excluded.updated_by",
                        (name, ciphertext, nonce, now, now, actor),
                    )
        finally:
            conn.close()

    @_sqlite_guarded
    def get_secret(self, name: str) -> Optional[str]:
        name = self._check_name(name)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT ciphertext, nonce FROM secrets WHERE name = ?",
                (name,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        ciphertext, nonce = row
        try:
            plaintext = self._aesgcm.decrypt(
                bytes(nonce), bytes(ciphertext), name.encode("utf-8")
            )
        except InvalidTag as exc:
            raise SecretIntegrityError(
                f"secret {name} failed authentication (wrong key or tampered row)"
            ) from exc
        return plaintext.decode("utf-8")

    @_sqlite_guarded
    def has_secret(self, name: str) -> bool:
        name = self._check_name(name)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM secrets WHERE name = ?", (name,)
            ).fetchone()
        finally:
            conn.close()
        return row is not None

    @_sqlite_guarded
    def list_secrets(self) -> List[Dict[str, Any]]:
        """Metadata + masked hint only. Plaintext is never in the result."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT name, ciphertext, nonce, version, created_at, "
                "updated_at, updated_by FROM secrets ORDER BY name"
            ).fetchall()
        finally:
            conn.close()
        entries: List[Dict[str, Any]] = []
        for name, ciphertext, nonce, version, created_at, updated_at, updated_by in rows:
            try:
                plaintext = self._aesgcm.decrypt(
                    bytes(nonce), bytes(ciphertext), name.encode("utf-8")
                ).decode("utf-8")
                hint = _mask(plaintext)
            except InvalidTag:
                hint = "\u2022" * 4
            entries.append(
                {
                    "name": name,
                    "hint": hint,
                    "version": version,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "updated_by": updated_by,
                }
            )
        return entries

    @_sqlite_guarded
    def delete_secret(self, name: str, actor: str) -> bool:
        name = self._check_name(name)
        actor = self._check_actor(actor)
        conn = self._connect()
        try:
            with conn:
                existing = conn.execute(
                    "SELECT 1 FROM secrets WHERE name = ?", (name,)
                ).fetchone()
                if existing is None:
                    return False
                conn.execute(
                    "INSERT INTO secret_events (name, action, actor, at) "
                    "VALUES (?, 'delete', ?, ?)",
                    (name, actor, _now()),
                )
                conn.execute("DELETE FROM secrets WHERE name = ?", (name,))
        finally:
            conn.close()
        return True

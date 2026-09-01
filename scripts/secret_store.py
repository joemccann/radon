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
"""


class SecretStoreError(RuntimeError):
    """Base error for store failures (I/O, key material)."""


class SecretValidationError(ValueError):
    """Rejected input: bad name, empty/oversize/non-string value."""


class SecretIntegrityError(SecretStoreError):
    """Ciphertext failed authentication: wrong key or tampered row."""


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
        self._aesgcm = AESGCM(self._load_or_create_key())

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
            key = self._key_path.read_bytes()
            if len(key) != _KEY_BYTES:
                raise SecretStoreError(
                    f"key file {self._key_path} must be {_KEY_BYTES} bytes, "
                    f"got {len(key)}"
                )
            return key
        key = os.urandom(_KEY_BYTES)
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            self._key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        return key

    # -- connection --------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        existed = self._db_path.is_file()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.executescript(_SCHEMA)
        if not existed:
            os.chmod(self._db_path, 0o600)
        return conn

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
        name = self._check_name(name)
        value = self._check_value(value)
        actor = self._check_actor(actor)
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = self._aesgcm.encrypt(
            nonce, value.encode("utf-8"), name.encode("utf-8")
        )
        now = _now()
        conn = self._connect()
        try:
            with conn:
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

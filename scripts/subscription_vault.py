#!/usr/bin/env python3
"""Subscription-token vault broker (DS-2026-10-05-05).

``radon-subscription-tokens`` runs third-party agent CLIs (agy, grok, codex)
out of radon-writable directories, so it must never hold the secret-store
master key. This broker is the one process that opens the encrypted store on
its behalf. It runs inside ``radon-app-runtime`` (image code, key staged
``root:radon-secrets 0040``) and serves exactly the four CLI credential slots
(``subscription_tokens.PROVIDERS``) over a unix socket in a radon-only
directory. Every other secret in the store is out of its reach by
construction: requests name a provider, never a secret.

Protocol: one JSON object per connection, the client half-closes, the broker
answers one JSON object and closes. ``{"op": "ping"}``,
``{"op": "get", "provider": p}`` and ``{"op": "seal", "provider": p,
"value": v}``. Replies are ``{"ok": true, ...}`` or
``{"ok": false, "error": <class>}``. No value is ever logged.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import stat
import sys
import threading
import time
from typing import Any, Optional

try:  # package import (python -m scripts.subscription_vault)
    from .subscription_tokens import PROVIDERS, VaultUnavailable
except ImportError:  # pragma: no cover - direct script execution
    from subscription_tokens import PROVIDERS, VaultUnavailable  # type: ignore

log = logging.getLogger("subscription_vault")

SOCKET_ENV = "RADON_SUBSCRIPTION_VAULT_SOCKET"
DEFAULT_SOCKET = "/run/radon-subscription-vault/vault.sock"
# A sealed credential file is a few KiB; the cap only bounds a hostile client.
MAX_REQUEST_BYTES = 1 << 20
MAX_REPLY_BYTES = MAX_REQUEST_BYTES + 4096
IO_TIMEOUT_SECONDS = 10.0
# The broker container takes a few seconds to come up after systemd starts it.
CONNECT_WAIT_SECONDS = 30.0


def _error(message: str) -> dict:
    return {"ok": False, "error": message}


def handle(vault: Any, request: Any) -> dict:
    if not isinstance(request, dict):
        return _error("malformed request")
    op = request.get("op")
    if op == "ping":
        return {"ok": True}
    if op not in ("get", "seal"):
        return _error("unknown op")
    provider = request.get("provider")
    if not isinstance(provider, str) or provider not in PROVIDERS:
        return _error("unknown provider")
    try:
        if op == "get":
            return {"ok": True, "value": vault.get(provider)}
        value = request.get("value")
        if not isinstance(value, str):
            return _error("malformed request")
        vault.seal(provider, value)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 - never leak a path or value
        log.error("%s %s failed: %s", op, provider, type(exc).__name__)
        return _error(f"store error: {type(exc).__name__}")


def _read_request(conn: socket.socket) -> Any:
    data = b""
    received = 0
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        received += len(chunk)
        # Keep draining (bounded, and under the I/O timeout) so the client
        # reads the refusal instead of a reset; stop storing past the cap.
        if received > 4 * MAX_REQUEST_BYTES:
            return _TOO_LARGE
        if received <= MAX_REQUEST_BYTES:
            data += chunk
    if received > MAX_REQUEST_BYTES:
        return _TOO_LARGE
    try:
        return json.loads(data)
    except ValueError:
        return _MALFORMED


_TOO_LARGE = object()
_MALFORMED = object()


def _serve_one(conn: socket.socket, vault: Any) -> None:
    conn.settimeout(IO_TIMEOUT_SECONDS)
    request = _read_request(conn)
    if request is _TOO_LARGE:
        reply = _error("request too large")
    elif request is _MALFORMED:
        reply = _error("malformed request")
    else:
        reply = handle(vault, request)
    conn.sendall(json.dumps(reply).encode())


def _clear_stale_socket(path: str) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(info.st_mode):
        log.error("refusing to replace a non-socket at the vault socket path")
        raise SystemExit(78)
    os.unlink(path)


def serve(
    path: str,
    vault: Any,
    *,
    stop: Optional[threading.Event] = None,
    ready: Optional[threading.Event] = None,
) -> None:
    _clear_stale_socket(path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old_umask = os.umask(0o177)
    try:
        server.bind(path)
    finally:
        os.umask(old_umask)
    os.chmod(path, 0o600)
    server.listen(8)
    server.settimeout(0.5)
    log.info("subscription vault listening")
    if ready is not None:
        ready.set()
    try:
        while stop is None or not stop.is_set():
            try:
                conn, _addr = server.accept()
            except socket.timeout:
                continue
            with conn:
                try:
                    _serve_one(conn, vault)
                except OSError as exc:
                    log.warning("vault connection failed: %s", type(exc).__name__)
    finally:
        server.close()
        try:
            os.unlink(path)
        except OSError:
            pass


class BrokerVault:
    """The ``Vault`` interface ``subscription_tokens`` uses, over the socket."""

    def __init__(self, path: str) -> None:
        self._path = path

    def _call(self, request: dict) -> dict:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(IO_TIMEOUT_SECONDS)
                conn.connect(self._path)
                conn.sendall(json.dumps(request).encode())
                conn.shutdown(socket.SHUT_WR)
                data = b""
                while chunk := conn.recv(65536):
                    data += chunk
                    if len(data) > MAX_REPLY_BYTES:
                        raise VaultUnavailable("vault broker reply too large")
            reply = json.loads(data)
        except VaultUnavailable:
            raise
        except (OSError, ValueError) as exc:
            raise VaultUnavailable(f"vault broker unreachable: {type(exc).__name__}") from None
        if not isinstance(reply, dict) or reply.get("ok") is not True:
            error = reply.get("error") if isinstance(reply, dict) else None
            raise VaultUnavailable(f"vault broker refused: {error}")
        return reply

    def get(self, provider: str) -> Optional[str]:
        value = self._call({"op": "get", "provider": provider}).get("value")
        if value is not None and not isinstance(value, str):
            raise VaultUnavailable("vault broker returned a non-string value")
        return value

    def seal(self, provider: str, value: str) -> None:
        self._call({"op": "seal", "provider": provider, "value": value})


def connect_vault(path: str, wait_seconds: Optional[float] = None) -> BrokerVault:
    """A pinged broker client, or VaultUnavailable after ``wait_seconds``."""
    vault = BrokerVault(path)
    deadline = time.monotonic() + (CONNECT_WAIT_SECONDS if wait_seconds is None else wait_seconds)
    while True:
        try:
            vault._call({"op": "ping"})
            return vault
        except VaultUnavailable:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.subscription_vault")
    parser.add_argument("--serve", action="store_true", required=True)
    parser.add_argument("--socket", default=os.environ.get(SOCKET_ENV) or DEFAULT_SOCKET)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from .secret_store import SecretStore
        from .subscription_tokens import Vault
    except ImportError:  # pragma: no cover - direct script execution
        from secret_store import SecretStore  # type: ignore
        from subscription_tokens import Vault  # type: ignore
    try:
        vault = Vault(SecretStore())
    except Exception as exc:  # noqa: BLE001 - systemd restarts; the daemon reports 78
        log.error("secret store could not be opened: %s", type(exc).__name__)
        return 78
    serve(args.socket, vault)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

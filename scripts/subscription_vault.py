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
import contextvars
import json
import logging
import math
import os
import socket
import stat
import struct
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
    if op == "model-key":
        provider = request.get("provider")
        if set(request) != {"op", "provider"} or provider not in {"nvidia", "cerebras", "cloudflare-token", "cloudflare-account"}:
            return _error("unknown model credential provider")
        try:
            return {"ok": True, "value": vault.model_key(provider)}
        except Exception as exc:
            log.error("model credential read failed: %s", type(exc).__name__)
            return _error("model credential store unavailable")
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


def _peer_uid(conn: socket.socket) -> int:
    """The connected peer's uid, resolved as the model gateway resolves it."""
    if hasattr(socket, "SO_PEERCRED"):
        _pid, uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        return uid
    if hasattr(conn, "getpeereid"):
        return conn.getpeereid()[0]
    return os.getuid()  # Private filesystem permissions on development platforms.


def _serve_one(conn: socket.socket, vault: Any) -> None:
    conn.settimeout(IO_TIMEOUT_SECONDS)
    # Admission is the peer identity, not the socket mode alone: a request is
    # not read, let alone answered with a credential, for any other account.
    if _peer_uid(conn) != os.getuid():
        log.warning("vault connection refused: peer uid is not the service account")
        conn.sendall(json.dumps(_error("unauthorized peer")).encode())
        return
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

    def __init__(self, path: str, *, timeout_seconds=None, cancelled=None) -> None:
        self._path = path
        self._timeout = timeout_seconds if callable(timeout_seconds) else None
        self._cancelled = cancelled
        self._deadline = None
        if timeout_seconds is not None and self._timeout is None:
            if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
                raise ValueError("invalid vault deadline")
            self._deadline = time.monotonic() + timeout_seconds

    def _remaining(self):
        if self._cancelled is not None and self._cancelled():
            raise VaultUnavailable("vault broker request cancelled")
        remaining = IO_TIMEOUT_SECONDS
        if self._deadline is not None:
            remaining = min(remaining, self._deadline - time.monotonic())
        if self._timeout is not None:
            remaining = min(remaining, self._timeout())
        if not math.isfinite(remaining) or remaining <= 0:
            raise VaultUnavailable("vault broker deadline exceeded")
        return remaining

    def _call(self, request: dict) -> dict:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(self._remaining())
                stopped = threading.Event()
                failures = []
                watcher = None
                if self._timeout is not None or self._deadline is not None or self._cancelled is not None:
                    # Preserve the caller's request context in the watchdog.
                    # It interrupts silent connect/read as well as drip-fed replies.
                    context = contextvars.copy_context()
                    def watch():
                        while not stopped.wait(.01):
                            try:
                                context.run(self._remaining)
                            except Exception as exc:
                                failures.append(exc)
                                try: conn.shutdown(socket.SHUT_RDWR)
                                except OSError: pass
                                conn.close()
                                return
                    watcher = threading.Thread(target=watch, daemon=True)
                    watcher.start()
                try:
                    conn.connect(self._path)
                    self._remaining()
                    conn.sendall(json.dumps(request).encode())
                    conn.shutdown(socket.SHUT_WR)
                    data = b""
                    while chunk := conn.recv(65536):
                        self._remaining()
                        data += chunk
                        if len(data) > MAX_REPLY_BYTES:
                            raise VaultUnavailable("vault broker reply too large")
                    self._remaining()
                finally:
                    stopped.set()
                    if watcher is not None: watcher.join(.1)
                    if failures: raise failures[0]
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

    def model_key(self, provider: str) -> Optional[str]:
        value = self._call({"op": "model-key", "provider": provider}).get("value")
        if value is not None and not isinstance(value, str):
            raise VaultUnavailable("vault broker returned an invalid model credential")
        return value

    def seal(self, provider: str, value: str) -> None:
        self._call({"op": "seal", "provider": provider, "value": value})


def connect_vault(path: str, wait_seconds: Optional[float] = None, *, timeout_seconds=None, cancelled=None) -> BrokerVault:
    """A pinged broker client, or VaultUnavailable after ``wait_seconds``."""
    vault = BrokerVault(path, timeout_seconds=timeout_seconds, cancelled=cancelled)
    deadline = time.monotonic() + (CONNECT_WAIT_SECONDS if wait_seconds is None else wait_seconds)
    while True:
        try:
            vault._call({"op": "ping"})
            return vault
        except VaultUnavailable:
            vault._remaining()
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(.25, vault._remaining()))


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

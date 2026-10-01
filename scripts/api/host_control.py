"""Client for the host control daemon (``radon-control.service``).

In production radon-api runs inside a locked-down container with no
``systemctl`` and no ``sudo``. The host daemon listens on a unix socket that
``radon-app-runtime`` bind-mounts into the radon-api container only, and runs
the allowlisted ``sudo -n /usr/local/bin/radon ...`` verbs on its behalf.

Wire format: one JSON object per line in each direction. Stdlib only, so the
daemon can share this module's constants without importing the API stack.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from pathlib import Path

DEFAULT_SOCKET_PATH = "/run/radon-control/control.sock"
SOCKET_ENV = "RADON_CONTROL_SOCKET"
# A status reply carries ~150 unit rows; a mutation reply is one short line.
MAX_REPLY_BYTES = 2 * 1024 * 1024


class HostControlError(Exception):
    """The daemon is unreachable, timed out, or replied with garbage."""


def socket_path() -> str:
    return (os.environ.get(SOCKET_ENV) or DEFAULT_SOCKET_PATH).strip()


def socket_present() -> bool:
    """True when the bind-mounted socket exists. Cheap, no connect."""
    try:
        return Path(socket_path()).is_socket()
    except OSError:
        return False


def call(request: dict, timeout: float) -> dict:
    """Send one request and return the decoded reply. Raises HostControlError."""
    path = socket_path()
    body = (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    deadline = time.monotonic() + timeout
    try:
        sock.connect(path)
        sock.sendall(body)
        chunks: list[bytes] = []
        size = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HostControlError(f"host control timed out after {timeout:.0f}s")
            sock.settimeout(remaining)
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_REPLY_BYTES:
                raise HostControlError("host control reply is too large")
            if chunk.endswith(b"\n"):
                break
    except HostControlError:
        raise
    except socket.timeout as exc:
        raise HostControlError(f"host control timed out after {timeout:.0f}s") from exc
    except OSError as exc:
        raise HostControlError(f"host control unreachable at {path}: {exc}") from exc
    finally:
        sock.close()
    try:
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise HostControlError("host control reply is not JSON") from exc
    if not isinstance(payload, dict):
        raise HostControlError("host control reply is not an object")
    return payload


async def acall(request: dict, timeout: float) -> dict:
    return await asyncio.to_thread(call, request, timeout)

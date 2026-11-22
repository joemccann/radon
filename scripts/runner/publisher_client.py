#!/usr/bin/env python3
"""Client for the local credential publisher.

The caller holds no GitHub token, deploy key, or Pushover key. It sends
one JSON request on the publisher socket and reads one JSON response.
The daemon refuses merges, force-pushes, and branches outside the
caller's `<prefix>/<YYYY-MM-DD>`.
"""

from __future__ import annotations

import json
import socket

DEFAULT_SOCKET = "/Library/Radon/publisher/run/publisher.sock"
MAX_REQUEST = 256 * 1024


class PublisherError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PublisherClient:
    def __init__(self, socket_path: str = DEFAULT_SOCKET, timeout: float = 120.0) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    def call(self, payload: dict) -> dict:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        if len(raw) > MAX_REQUEST:
            raise PublisherError("too_large")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
            sock.sendall(raw)
            sock.shutdown(socket.SHUT_WR)
            chunks: list[bytes] = []
            total = 0
            while total <= MAX_REQUEST:
                blob = sock.recv(65536)
                if not blob:
                    break
                total += len(blob)
                chunks.append(blob)
        except OSError as exc:
            raise PublisherError("unavailable") from exc
        finally:
            sock.close()
        try:
            response = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PublisherError("bad_response") from exc
        if not isinstance(response, dict) or response.get("ok") is not True:
            code = response.get("error") if isinstance(response, dict) else "rejected"
            raise PublisherError(str(code or "rejected"))
        return response

    def push(self, *, caller: str, branch: str, bundle: str) -> dict:
        return self.call({"op": "push", "caller": caller, "branch": branch, "bundle": bundle})

    def open_draft(self, *, caller: str, branch: str, title: str, body: str) -> dict:
        return self.call(
            {
                "op": "draft_pr",
                "action": "open",
                "caller": caller,
                "branch": branch,
                "title": title,
                "body": body,
            }
        )

    def edit_draft(self, *, caller: str, branch: str, pr: str, title: str, body: str) -> dict:
        return self.call(
            {
                "op": "draft_pr",
                "action": "edit",
                "caller": caller,
                "branch": branch,
                "pr": pr,
                "title": title,
                "body": body,
            }
        )

    def publish_report(self, *, caller: str, phase: str, day: str, path: str) -> dict:
        return self.call(
            {
                "op": "publish_report",
                "caller": caller,
                "phase": phase,
                "date": day,
                "path": path,
            }
        )

    def notify(self, *, caller: str, title: str, message: str, url: str = "", url_title: str = "") -> dict:
        return self.call(
            {
                "op": "notify",
                "caller": caller,
                "title": title,
                "message": message,
                "url": url,
                "url_title": url_title,
            }
        )

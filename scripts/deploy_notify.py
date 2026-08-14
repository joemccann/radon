"""Normal-priority Pushover after a release passes the live deploy gate.

Never P1. A live-update page must not retrigger the Grok incident
responder. Best-effort: a channel miss must not fail the deploy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"
MAX_SUBJECT_CHARS = 120


def _load_env_file(path: Optional[str]) -> None:
    if not path:
        return
    env_path = Path(path)
    if not env_path.is_file():
        return
    try:
        from dotenv import dotenv_values
    except ImportError:
        return
    for key, value in dotenv_values(env_path, interpolate=False).items():
        if key and value is not None and key not in os.environ:
            os.environ[key] = value


def _creds() -> Optional[tuple[str, str]]:
    user = os.environ.get("PUSHOVER_USER")
    token = os.environ.get("PUSHOVER_TOKEN")
    return (user, token) if user and token else None


def build_live_payload(
    *, user: str, token: str, sha: str, subject: str
) -> dict:
    short = (sha or "unknown")[:7]
    cleaned = " ".join((subject or "release").split())
    if len(cleaned) > MAX_SUBJECT_CHARS:
        cleaned = cleaned[: MAX_SUBJECT_CHARS - 1] + "..."
    return {
        "token": token,
        "user": user,
        "title": "radon deploy live",
        "message": f"{short} is live: {cleaned}",
        "priority": 0,
    }


def _http_post(url: str, payload: dict) -> tuple[int, bytes]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib_request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib_error.HTTPError as exc:
        return exc.code, exc.read() if hasattr(exc, "read") else b""


def notify_deploy_live(*, sha: str, subject: str) -> Optional[str]:
    creds = _creds()
    if not creds:
        return None
    user, token = creds
    payload = build_live_payload(
        user=user, token=token, sha=sha, subject=subject
    )
    try:
        status, body = _http_post(PUSHOVER_API_URL, payload)
    except Exception as exc:  # noqa: BLE001 — notify must not fail deploy
        return f"pushover transport failed: {exc}"
    if status >= 400:
        excerpt = body[:200].decode("utf-8", "replace").strip() if body else "no body"
        return f"pushover {status}: {excerpt}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deploy_notify")
    parser.add_argument("--sha", required=True)
    parser.add_argument("--subject", default="")
    parser.add_argument("--env-file", default=os.environ.get("RADON_DEPLOY_ENV_FILE"))
    args = parser.parse_args(argv)
    _load_env_file(args.env_file)
    error = notify_deploy_live(sha=args.sha, subject=args.subject)
    if error:
        print(error, file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

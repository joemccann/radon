"""Normal-priority Pushover after a weekend-loop phase finishes.

One page per phase (audit, remediate) so a hung phase is visible
immediately. Never P1: a P1 retriggers the Grok incident responder.
Best-effort — this must never fail the run that called it, so main()
always returns 0 and errors go to stderr only.
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


def build_weekend_payload(
    *,
    user: str,
    token: str,
    loop: str,
    phase: str,
    status: str,
    pr_url: str,
    detail: str,
) -> dict:
    lines = [" ".join((status or "UNKNOWN").split())]
    cleaned_detail = " ".join((detail or "").split())
    if cleaned_detail:
        lines.append(cleaned_detail)
    cleaned_url = (pr_url or "").strip()
    if cleaned_url:
        lines.append(cleaned_url)
    return {
        "token": token,
        "user": user,
        "title": f"radon {loop} {phase}",
        "message": "\n".join(lines),
        # Always 0. A P1 retriggers the Grok incident responder.
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


def notify_weekend_phase(
    *, loop: str, phase: str, status: str, pr_url: str, detail: str
) -> Optional[str]:
    creds = _creds()
    if not creds:
        return None
    user, token = creds
    payload = build_weekend_payload(
        user=user,
        token=token,
        loop=loop,
        phase=phase,
        status=status,
        pr_url=pr_url,
        detail=detail,
    )
    try:
        status_code, body = _http_post(PUSHOVER_API_URL, payload)
    except Exception as exc:  # noqa: BLE001 — notify must not fail the run
        return f"pushover transport failed: {exc}"
    if status_code >= 400:
        excerpt = body[:200].decode("utf-8", "replace").strip() if body else "no body"
        return f"pushover {status_code}: {excerpt}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="weekend_notify")
    parser.add_argument("--loop", required=True, choices=["reliability", "testing"])
    parser.add_argument("--phase", required=True, choices=["audit", "remediate"])
    parser.add_argument("--status", required=True)
    parser.add_argument("--pr-url", default="")
    parser.add_argument("--detail", default="")
    parser.add_argument("--env-file", default=None)
    args = parser.parse_args(argv)
    _load_env_file(args.env_file)
    error = notify_weekend_phase(
        loop=args.loop,
        phase=args.phase,
        status=args.status,
        pr_url=args.pr_url,
        detail=args.detail,
    )
    if error:
        print(error, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

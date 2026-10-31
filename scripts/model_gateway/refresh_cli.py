"""A secret-free systemd timer client for gateway-owned subscription renewal."""
from __future__ import annotations

import re
import sys
import time
from typing import Callable

WAIT_SECONDS = 1600.0
POLL_SECONDS = 2.0


def main(
    argv: list[str] | None = None,
    *,
    request: Callable | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    del argv
    if request is None:
        from .client import request
    deadline = monotonic() + WAIT_SECONDS
    try:
        accepted = request("refresh", {"mode": "once"}, timeout_seconds=15.0)
        job_id = accepted.get("job_id") if isinstance(accepted, dict) else None
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", job_id):
            print("subscription refresh: invalid gateway job", file=sys.stderr)
            return 78
        while monotonic() < deadline:
            result = request("refresh-status", {"job_id": job_id}, timeout_seconds=15.0)
            status = result.get("status") if isinstance(result, dict) else None
            if status == "complete":
                code = result.get("exit_code")
                if type(code) is int and code in (0, 1, 78):
                    print(f"subscription refresh: completed ({code})", file=sys.stderr)
                    return code
                break
            if status not in ("queued", "running"):
                break
            sleep(POLL_SECONDS)
    except Exception as exc:  # Never echo provider responses, paths or tokens.
        print(f"subscription refresh: gateway unavailable ({type(exc).__name__})", file=sys.stderr)
        return 78
    print("subscription refresh: gateway job unfinished", file=sys.stderr)
    return 78


if __name__ == "__main__":
    raise SystemExit(main())

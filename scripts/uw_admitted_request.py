"""Killable worker for the API's closed, admitted Next UW request surface.

Only the authenticated API constructs arguments. Credentials are hydrated in
this process and are never accepted in CLI arguments or returned in errors.
"""
from __future__ import annotations

import json
import sys


def main(argv: list[str] | None = None) -> int:
    from clients.uw_client import UWClient, UWAPIError
    from uw_stream.config import hydrate_settings

    args = sys.argv[1:] if argv is None else argv
    result = {"status": 503, "data": {"error": "Research provider request unavailable"}}
    try:
        if len(args) != 2:
            raise ValueError("Invalid admitted request")
        params = json.loads(args[1])
        if not isinstance(params, dict):
            raise ValueError("Invalid admitted query")
        hydrate_settings()
        with UWClient(timeout=10, max_retries=0, max_response_bytes=4 * 1024 * 1024) as client:
            result = {"status": 200, "data": client.get(args[0], params, priority="interactive", wait_timeout=3)}
    except UWAPIError as exc:
        result["status"] = exc.status_code or 503
    except Exception:
        # No provider body, URL, token or secret-store detail crosses the API.
        pass
    print(json.dumps(result, allow_nan=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

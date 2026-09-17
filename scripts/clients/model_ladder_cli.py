#!/usr/bin/env python3.13
"""stdin JSON -> stdout JSON via the shared model ladder.

Used by ``scripts/newsfeed/tagger.js`` so the Node text tagger cannot drift
from ``clients.model_ladder`` order. Logs go to stderr. stdout is the result
object only. Soft-fails with ``{"ok": false}`` and exit 0 when the ladder
is exhausted or the request is unusable.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, TextIO

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from clients.model_ladder import (  # noqa: E402
    ModelLadderExhausted,
    accept_distill_payload,
    accept_tags_payload,
    complete_text_json,
)

_ACCEPT = {
    "tags": accept_tags_payload,
    "distill": accept_distill_payload,
}


def main(
    argv: list[str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    complete: Callable[..., Any] | None = None,
) -> int:
    del argv  # reserved for future flags; payload is stdin JSON
    raw = (stdin or sys.stdin).read()
    out = stdout or sys.stdout
    try:
        req = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        json.dump({"ok": False, "error": "invalid_request"}, out)
        out.write("\n")
        return 0
    if not isinstance(req, dict) or not isinstance(req.get("instruction"), str):
        json.dump({"ok": False, "error": "invalid_request"}, out)
        out.write("\n")
        return 0

    accept_name = req.get("accept")
    accept = _ACCEPT.get(accept_name) if isinstance(accept_name, str) else None
    runner = complete or complete_text_json
    try:
        result = runner(
            req["instruction"],
            system=str(req.get("system") or ""),
            max_tokens=int(req.get("max_tokens") or 800),
            read_timeout=float(req.get("timeout") or 30),
            require_end_turn=False,
            accept=accept,
            log_prefix="newsfeed-tagger",
        )
    except ModelLadderExhausted:
        json.dump({"ok": False, "error": "exhausted"}, out)
        out.write("\n")
        return 0
    except Exception as exc:  # noqa: BLE001 — CLI must never raise to Node
        json.dump({"ok": False, "error": type(exc).__name__}, out)
        out.write("\n")
        return 0

    json.dump(
        {
            "ok": True,
            "data": result.data,
            "provider": result.provider,
            "model": result.model,
        },
        out,
    )
    out.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

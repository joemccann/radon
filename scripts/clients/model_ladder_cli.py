#!/usr/bin/env python3.13
"""stdin JSON -> stdout JSON via the shared model ladder.

Used by ``scripts/newsfeed/tagger.js`` so the Node text tagger cannot drift
from ``clients.model_ladder`` order. Logs go to stderr. stdout is the result
object only. Soft-fails with ``{"ok": false}`` and exit 0 when the ladder
is exhausted or the request is unusable.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from clients.model_ladder import (  # noqa: E402
    LadderResult,
    ModelLadderExhausted,
    accept_distill_payload,
    accept_tags_payload,
    complete_text_json,
)
from newsfeed.slm.modes import (  # noqa: E402
    SLM_MODE_PREFER,
    SLM_MODE_PRIMARY,
    SLM_MODE_SHADOW,
    SLM_SHADOW_PROBE_ORDER,
    normalise_mode,
    providers_for_mode,
)
from newsfeed.slm.shadow import should_sample_ladder, write_shadow_row  # noqa: E402

_ACCEPT = {
    "tags": accept_tags_payload,
    "distill": accept_distill_payload,
}


def _slm_status(result: LadderResult | None, error: str | None = None) -> str:
    if result is not None:
        for item in result.attempted:
            if item.startswith("slm-tagger:"):
                return item.split(":", 1)[1]
        if result.provider == "slm-tagger":
            return "ok"
    if error == "exhausted":
        return "unavailable"
    return error or "unavailable"


def _emit_shadow(
    *,
    write_shadow: Callable[..., Any] | None,
    req: dict[str, Any],
    mode: str,
    slm_result: LadderResult | None,
    ladder_result: LadderResult | None,
    slm_status: str,
    ladder_sampled: bool,
) -> None:
    writer = write_shadow or write_shadow_row
    slm_tags = slm_result.data.get("tags") if slm_result and isinstance(slm_result.data, dict) else None
    ladder_tags = (
        ladder_result.data.get("tags") if ladder_result and isinstance(ladder_result.data, dict) else None
    )
    exact3 = None
    jaccard = None
    if slm_tags and ladder_tags:
        a, b = set(slm_tags), set(ladder_tags)
        union = a | b
        exact3 = 1 if a == b and len(a) == 3 else 0
        jaccard = (len(a & b) / len(union)) if union else 0.0
    try:
        writer(
            {
                "post_id": req.get("post_id") or req.get("instruction") or "",
                "model_version": "1.0.0",
                "mode": mode,
                "tags_slm": slm_tags if slm_status == "ok" else None,
                "tags_slm_raw": slm_result.text if slm_result else None,
                "tags_ladder": ladder_tags,
                "ladder_provider": ladder_result.provider if ladder_result else None,
                "ladder_sampled": 1 if ladder_sampled else 0,
                "slm_status": slm_status,
                "exact3": exact3,
                "jaccard": jaccard,
            }
        )
    except Exception:
        pass


def main(
    argv: list[str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    complete: Callable[..., Any] | None = None,
    env: Mapping[str, str] | None = None,
    write_shadow: Callable[..., Any] | None = None,
) -> int:
    del argv  # reserved for future flags; payload is stdin JSON
    raw = (stdin or sys.stdin).read()
    out = stdout or sys.stdout
    src = env if env is not None else os.environ
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
    mode = normalise_mode(src.get("RADON_SLM_TAGGER_MODE"))
    kwargs: dict[str, Any] = {
        "system": str(req.get("system") or ""),
        "max_tokens": int(req.get("max_tokens") or 800),
        "read_timeout": float(req.get("timeout") or 30),
        "require_end_turn": False,
        "accept": accept,
        "log_prefix": "newsfeed-tagger",
    }
    tags_contract = accept_name == "tags"
    slm_active = tags_contract and mode in {SLM_MODE_SHADOW, SLM_MODE_PREFER, SLM_MODE_PRIMARY}

    def _run(**extra: Any) -> LadderResult:
        return runner(req["instruction"], **kwargs, **extra)

    try:
        slm_result: LadderResult | None = None
        slm_status = "unavailable"
        ladder_sampled = False
        if slm_active and mode == SLM_MODE_SHADOW:
            try:
                slm_result = _run(providers=list(SLM_SHADOW_PROBE_ORDER))
                slm_status = _slm_status(slm_result)
            except ModelLadderExhausted:
                slm_status = "unavailable"
            result = _run()
            _emit_shadow(
                write_shadow=write_shadow,
                req=req,
                mode=mode,
                slm_result=slm_result,
                ladder_result=result,
                slm_status=slm_status,
                ladder_sampled=False,
            )
        elif slm_active:
            order = providers_for_mode(mode)
            result = _run(providers=list(order) if order else None)
            slm_status = _slm_status(result)
            slm_result = result if result.provider == "slm-tagger" else None
            if should_sample_ladder(req.get("post_id"), req.get("instruction") or ""):
                ladder_sampled = True
                try:
                    sampled = _run()
                    _emit_shadow(
                        write_shadow=write_shadow,
                        req=req,
                        mode=mode,
                        slm_result=slm_result or result,
                        ladder_result=sampled,
                        slm_status=slm_status,
                        ladder_sampled=True,
                    )
                except ModelLadderExhausted:
                    _emit_shadow(
                        write_shadow=write_shadow,
                        req=req,
                        mode=mode,
                        slm_result=slm_result or result,
                        ladder_result=None,
                        slm_status=slm_status,
                        ladder_sampled=True,
                    )
            else:
                _emit_shadow(
                    write_shadow=write_shadow,
                    req=req,
                    mode=mode,
                    slm_result=slm_result or result,
                    ladder_result=None if result.provider == "slm-tagger" else result,
                    slm_status=slm_status,
                    ladder_sampled=False,
                )
        else:
            result = _run()
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

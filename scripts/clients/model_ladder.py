"""Credential-free compatibility client for Radon's standalone model gateway.

Provider credentials, native model runtimes and provider failover belong to
the service. Application-specific acceptance remains local; a
rejected response resumes the server's opaque continuation.
"""
from __future__ import annotations

import base64
import os
import json
import time
import mimetypes
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from model_gateway.client import GatewayError, request as gateway_request
from model_gateway.contracts import (
    MODEL_LADDER_ORDER, MODEL_LADDER_TIERS, LadderResult, ModelLadderExhausted,
    ModelResponseError, VisionResult, accept_distill_payload, accept_tags_payload,
    parse_json_object, parse_json_rows, safe_error_message,
)


def _image(label: str, source: Path | bytes) -> dict[str, str]:
    if isinstance(source, bytes):
        raw, media = source, "image/png"
    else:
        raw = Path(source).read_bytes()
        media = mimetypes.guess_type(str(source))[0] or "image/png"
    if media not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        raise ValueError("unsupported image media type")
    if not raw or len(raw) > 5_000_000:
        raise ValueError("image exceeds gateway byte limit")
    return {"label": str(label), "media_type": media,
            "data": base64.b64encode(raw).decode("ascii")}


def wired_providers(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Return gateway capability inventory; consumer environments carry no grants."""
    result = gateway_request("status", {}, timeout_seconds=10)
    return tuple(result.get("providers") or result.get("wired_providers") or ())


def complete_multimodal_json(
    instruction: str, images: Sequence[tuple[str, Path | bytes]] = (), *, system: str = "",
    env: Mapping[str, str] | None = None, post: Callable[..., Any] | None = None,
    model_override: str | None = None, max_tokens: int = 6000,
    stream_anthropic: bool = True, read_timeout: float = 120.0,
    max_response_bytes: int = 2_000_000, require_end_turn: bool = True,
    accept: Callable[[Any], bool] | None = None, log_prefix: str = "research",
    providers: Sequence[str] | None = None,
) -> LadderResult:
    # env/post are legacy caller seams; neither credentials nor transports cross
    # this boundary. Inject gateway_request in tests instead.
    payload: dict[str, Any] = {
        "instruction": instruction, "system": system,
        "images": [_image(label, source) for label, source in images],
        "providers": list(providers) if providers is not None else None,
        "model": model_override, "max_tokens": max_tokens,
        "read_timeout": read_timeout, "max_response_bytes": max_response_bytes,
        "require_end_turn": require_end_turn, "stream_anthropic": stream_anthropic,
        "log_prefix": log_prefix,
    }
    seen: set[str] = set()
    observed_attempts: list[str] = []
    deadline = time.monotonic() + read_timeout + 10
    for _ in range(32):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ModelLadderExhausted("Gateway request deadline exhausted")
        try:
            response = gateway_request("json", payload, timeout_seconds=remaining)
        except GatewayError as exc:
            if exc.code in {"exhausted", "deadline_exhausted"}:
                attempts = observed_attempts + list(exc.attempted)
                tried = " ".join(attempts) or "none"
                raise ModelLadderExhausted(f"Model ladder exhausted. tried={tried};") from exc
            if exc.code in {"incomplete", "oversize", "response_too_large", "request_too_large", "invalid_request", "malformed", "invalid_response"}:
                raise ModelResponseError(exc.code, "Gateway returned an incomplete or invalid model response") from exc
            raise ModelLadderExhausted("Model gateway unavailable") from exc
        if (not isinstance(response, dict) or "data" not in response
                or not isinstance(response.get("text"), str)
                or not isinstance(response.get("provider"), str) or not response["provider"]
                or not isinstance(response.get("model"), str)
                or not isinstance(response.get("attempted", []), (list, tuple))
                or any(not isinstance(item, str) for item in response.get("attempted", []))):
            raise ModelResponseError("malformed", "Invalid model gateway result envelope")
        result = LadderResult(data=response.get("data"), text=str(response.get("text") or ""),
                              provider=str(response.get("provider") or ""),
                              model=str(response.get("model") or ""),
                              attempted=tuple(response.get("attempted") or ()))
        if accept is None or accept(result.data):
            return result
        observed_attempts.extend(entry.removesuffix(":ok") + ":unparseable" if entry.endswith(":ok") else entry
                                 for entry in result.attempted)
        if not result.attempted:
            observed_attempts.append(result.provider + ":unparseable")
        continuation = response.get("continuation")
        if not isinstance(continuation, str) or not continuation or continuation in seen:
            raise ModelLadderExhausted("Model ladder exhausted. tried=" + " ".join(observed_attempts) + ";")
        seen.add(continuation)
        payload = {**payload, "continuation": continuation}
    raise ModelLadderExhausted("Gateway continuation limit exhausted")


def complete_text_json(
    instruction: str, *, system: str = "", env: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None, model_override: str | None = None,
    max_tokens: int = 800, read_timeout: float = 30.0,
    max_response_bytes: int = 2_000_000, require_end_turn: bool = False,
    accept: Callable[[Any], bool] | None = None, log_prefix: str = "text",
    providers: Sequence[str] | None = None,
) -> LadderResult:
    return complete_multimodal_json(
        instruction, system=system, env=env, post=post, model_override=model_override,
        max_tokens=max_tokens, read_timeout=read_timeout,
        max_response_bytes=max_response_bytes, require_end_turn=require_end_turn,
        accept=accept, log_prefix=log_prefix, providers=providers,
    )


def extract_via_vision(png_bytes: bytes, prompt: str, *, env=None, post=None,
                       max_response_bytes: int = 2_000_000) -> VisionResult:
    try:
        reply = gateway_request("vision", {"image": base64.b64encode(png_bytes).decode("ascii"),
                                "prompt": prompt, "max_response_bytes": max_response_bytes},
                                timeout_seconds=130)
    except GatewayError as exc:
        if exc.code in {"exhausted", "deadline_exhausted"}:
            raise ModelLadderExhausted("Model ladder exhausted. tried=" + (" ".join(exc.attempted) or "none") + ";") from exc
        if exc.code in {"incomplete", "oversize", "response_too_large", "request_too_large", "invalid_request", "malformed", "invalid_response"}:
            raise ModelResponseError(exc.code, "Gateway returned an incomplete or invalid vision response") from exc
        raise ModelLadderExhausted("Model gateway unavailable") from exc
    if (not isinstance(reply, dict) or not isinstance(reply.get("rows", reply.get("data")), list)
            or any(not isinstance(row, dict) for row in reply.get("rows", reply.get("data", [])))
            or not isinstance(reply.get("provider"), str) or not reply["provider"]
            or not isinstance(reply.get("model"), str)
            or not isinstance(reply.get("attempted", []), (list, tuple))
            or any(not isinstance(item, str) for item in reply.get("attempted", []))):
        raise ModelResponseError("malformed", "Invalid model gateway vision envelope")
    return VisionResult(rows=reply.get("rows", reply.get("data")), provider=reply["provider"],
                        model=reply["model"], attempted=tuple(reply.get("attempted", [])))


def accept_slm_tags_payload(obj: Any, taxonomy: Sequence[str] | None = None) -> bool:
    from newsfeed.slm.contract import classify_slm_tags
    code, _ = classify_slm_tags(obj, taxonomy if taxonomy is not None else _load_slm_taxonomy(os.environ))
    return code == "ok"


__all__ = ["MODEL_LADDER_ORDER", "MODEL_LADDER_TIERS", "LadderResult", "VisionResult",
           "ModelLadderExhausted", "ModelResponseError", "complete_text_json",
           "complete_multimodal_json", "extract_via_vision", "wired_providers",
           "accept_distill_payload", "accept_tags_payload", "accept_slm_tags_payload",
           "parse_json_object", "parse_json_rows", "safe_error_message"]


def _load_slm_taxonomy(env: Mapping[str, str]) -> list[str]:
    raw = (env.get("RADON_SLM_TAXONOMY_JSON") or "").strip()
    if raw:
        parsed = json.loads(raw)
        return [str(t) for t in parsed] if isinstance(parsed, list) else []
    path = Path(env.get("RADON_SLM_TAXONOMY_PATH") or "data/tag_taxonomy.json")
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        tags = payload.get("tags") if isinstance(payload, dict) else payload
        if isinstance(tags, list):
            return [str(t) for t in tags if isinstance(t, str)]
    try:
        from db.hrana_http import hrana_query

        rows = hrana_query("SELECT tag FROM tag_taxonomy", ())
        out: list[str] = []
        for row in rows:
            if isinstance(row, dict):
                tag = row.get("tag") or row.get("name")
            else:
                tag = row[0] if row else None
            if tag:
                out.append(str(tag))
        return out
    except Exception:
        return []

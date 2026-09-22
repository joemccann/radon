"""LLM distillation of knowledge docs into normalized searchable summaries.

Uses the shared model ladder (``clients.model_ladder.complete_text_json``):
subscription credentials only for Anthropic/Grok/Codex/Gemini (prepaid wallets
skipped unless ``RADON_LADDER_ALLOW_PREPAID=1``), then NVIDIA, then Cerebras
last. Distillation is best-effort by contract: any failure returns None and
never raises. The raw content is FTS-searchable regardless, and a stored row
without a summary is re-attempted on the next ingest run (ingest.py's
pre-filter only skips unchanged docs that already HAVE one).

Keys come from process env first, then root .env, then web/.env. Never logged.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from clients.model_ladder import (
    ModelLadderExhausted,
    accept_distill_payload,
    complete_text_json,
)
from credential_redaction import scrub_credential_text

MAX_CONTENT_CHARS = 6000
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_FILES = (_PROJECT_ROOT / ".env", _PROJECT_ROOT / "web" / ".env")

_SYSTEM_PROMPT = (
    "You distill documents from a trading system's knowledge base into "
    "normalized retrieval keys. Reply with ONLY a JSON object, no prose, "
    'shaped {"summary": string, "tickers": [string]}. summary is one line: '
    "a searchable question this document answers, followed by a 2-3 sentence "
    "normalized summary of its substance. tickers lists the uppercase "
    "stock/ETF/index symbols the document actually discusses, [] when none."
)

# Redact secret / PII shapes from document text BEFORE it egresses to a
# third-party model API. Eval/journal/incident content can carry the real IB
# account id (U\d{6,}), a Turso URL, or a token; none of it helps distillation,
# and the operator's data-handling rule is to never ship account ids/secrets to
# third parties. The raw content still lives in the LOCAL operator-only corpus —
# this only scrubs the copy that leaves the box. The base pattern set is the
# canonical scripts/credential_redaction.py (reused, not duplicated); the
# patterns below are distill-only supplements for header/assignment shapes.
_EGRESS_SCRUB_PATTERNS = (
    (
        re.compile(
            r"(\bauthorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+",
            re.IGNORECASE,
        ),
        r"\1[redacted-secret]",
    ),
    (
        re.compile(
            r"([\"']?(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|secret|token)"
            r"[\"']?\s*[:=]\s*[\"']?)[^\"'\s,;&]+",
            re.IGNORECASE,
        ),
        r"\1[redacted-secret]",
    ),
)


def _scrub_for_egress(text: str) -> str:
    # Distill-only header/assignment shapes first (they consume the full
    # value), then the canonical credential scrubber.
    for pattern, repl in _EGRESS_SCRUB_PATTERNS:
        text = pattern.sub(repl, text)
    return scrub_credential_text(text)


def distill(
    title: str | None,
    content: str,
    *,
    timeout: int = 20,
    env: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
    complete: Callable[..., Any] | None = None,
) -> dict | None:
    """Return {"summary": str, "tickers": list[str]} or None on any failure
    (no keyed provider, exhausted ladder, unparseable model output)."""
    src = dict(env) if env is not None else _ladder_env()
    document = f"Title: {title}\n\n{content}" if title else content
    # Scrub BEFORE truncating: a token straddling the truncation boundary must
    # never leave the box half-redacted.
    instruction = _scrub_for_egress(document)[:MAX_CONTENT_CHARS]
    runner = complete or complete_text_json
    try:
        result = runner(
            instruction,
            system=_SYSTEM_PROMPT,
            env=src,
            post=post,
            max_tokens=800,
            read_timeout=float(timeout),
            require_end_turn=False,
            accept=accept_distill_payload,
            log_prefix="knowledge-distill",
        )
    except ModelLadderExhausted:
        print("[knowledge-distill] model ladder exhausted — skipping", file=sys.stderr)
        return None
    except Exception as exc:  # noqa: BLE001 — graceful degradation by contract
        print(f"[knowledge-distill] ladder failed: {exc}", file=sys.stderr)
        return None
    return _normalize_distillation(getattr(result, "data", result))


def _ladder_env() -> dict[str, str]:
    merged = {key: value for key, value in os.environ.items() if value}
    try:
        from dotenv import dotenv_values  # noqa: PLC0415
    except ImportError:
        return merged
    for env_file in _ENV_FILES:
        if env_file.is_file():
            for key, value in dotenv_values(env_file).items():
                if value and key not in merged:
                    merged[key] = value
    return merged


def _normalize_distillation(data: Any) -> dict | None:
    if not isinstance(data, dict):
        print("[knowledge-distill] model output was not a JSON object", file=sys.stderr)
        return None
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        print("[knowledge-distill] model output missing summary", file=sys.stderr)
        return None
    raw_tickers = data.get("tickers")
    tickers = [
        ticker.strip().upper()
        for ticker in (raw_tickers if isinstance(raw_tickers, list) else [])
        if isinstance(ticker, str) and ticker.strip()
    ]
    return {"summary": summary.strip(), "tickers": tickers}

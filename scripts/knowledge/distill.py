"""LLM distillation of knowledge docs into normalized searchable summaries.

Cerebras chat-completions (OpenAI-compatible) via plain `requests` — no SDK
(plan §Dependencies). Distillation is best-effort by contract: any failure
returns None and never raises. The raw content is FTS-searchable regardless,
and a stored row without a summary is re-attempted on the next ingest run
(ingest.py's pre-filter only skips unchanged docs that already HAVE one).

The API key comes from CEREBRAS_API_KEY — process env first, then root .env,
then web/.env (where it is provisioned). The key is never logged.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import requests

CEREBRAS_CHAT_COMPLETIONS_URL = "https://api.cerebras.ai/v1/chat/completions"
#: Verified live against /v1/models on 2026-07-18 (gemma-4-31b, zai-glm-4.7,
#: gpt-oss-120b). Override with RADON_KB_DISTILL_MODEL.
DEFAULT_DISTILL_MODEL = "gpt-oss-120b"
MODEL_ENV = "RADON_KB_DISTILL_MODEL"
MAX_CONTENT_CHARS = 6000
_KEY_ENV = "CEREBRAS_API_KEY"
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

# Redact secret / PII shapes from document text BEFORE it egresses to the
# third-party Cerebras API. Eval/journal/incident content can carry the real IB
# account id (U\d{6,}), a Turso URL, or a token; none of it helps distillation,
# and the operator's data-handling rule is to never ship account ids/secrets to
# third parties. The raw content still lives in the LOCAL operator-only corpus —
# this only scrubs the copy that leaves the box. Mirrors server.py's
# _SECRET_SCRUB_PATTERNS.
_EGRESS_SCRUB_PATTERNS = (
    (re.compile(r"libsql://[^\s'\"]+", re.IGNORECASE), "[redacted-db-url]"),
    (re.compile(r"https://[a-z0-9.-]+\.turso\.io[^\s'\"]*", re.IGNORECASE), "[redacted-db-url]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]*"), "[redacted-jwt]"),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{6,}"), "[redacted-key]"),
    (re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{6,}\b"), "[redacted-key]"),
    (re.compile(r"\bU\d{6,}\b"), "[redacted-account]"),
)


def _scrub_for_egress(text: str) -> str:
    for pattern, repl in _EGRESS_SCRUB_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def distill(title: str | None, content: str, *, timeout: int = 20) -> dict | None:
    """Return {"summary": str, "tickers": list[str]} or None on any failure
    (missing key, network/API error twice, unparseable model output)."""
    key = _load_key()
    if not key:
        print("[knowledge-distill] CEREBRAS_API_KEY not configured — skipping",
              file=sys.stderr)
        return None
    payload = _request_payload(title, content)
    for attempt in ("first", "retry"):
        result = _request_distillation(key, payload, timeout, attempt)
        if result is not None:
            return result
    return None


def _load_key() -> str | None:
    key = os.environ.get(_KEY_ENV)
    if key:
        return key
    try:
        from dotenv import dotenv_values  # noqa: PLC0415
    except ImportError:
        return None
    for env_file in _ENV_FILES:
        if env_file.is_file():
            key = dotenv_values(env_file).get(_KEY_ENV)
            if key:
                return key
    return None


def _request_payload(title: str | None, content: str) -> dict:
    document = f"Title: {title}\n\n{content}" if title else content
    return {
        "model": os.environ.get(MODEL_ENV) or DEFAULT_DISTILL_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _scrub_for_egress(document[:MAX_CONTENT_CHARS])},
        ],
        "temperature": 0,
    }


def _request_distillation(
    key: str, payload: dict, timeout: int, attempt: str
) -> dict | None:
    try:
        response = requests.post(
            CEREBRAS_CHAT_COMPLETIONS_URL,
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
        if response.status_code != 200:
            print(
                f"[knowledge-distill] {attempt} attempt: HTTP {response.status_code}",
                file=sys.stderr,
            )
            return None
        message = response.json()["choices"][0]["message"]["content"]
        return _parse_distillation(message)
    except Exception as exc:  # noqa: BLE001 — graceful degradation by contract
        print(f"[knowledge-distill] {attempt} attempt failed: {exc}", file=sys.stderr)
        return None


def _parse_distillation(message: str) -> dict | None:
    data = _parse_json_object(_strip_code_fences(message))
    if data is None:
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


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[A-Za-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _parse_json_object(text: str) -> dict | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        embedded = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if embedded is None:
            return None
        try:
            data = json.loads(embedded.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None

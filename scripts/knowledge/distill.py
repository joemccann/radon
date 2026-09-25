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

import json
import os
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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


# Optional summaries must not consume the oneshot's 1800s raw-ingest window.
# A read timeout is not an elapsed deadline (streaming and CLI providers can
# outlive it), so each small wave runs in its own killable process group.
ENRICHMENT_SECONDS = 120.0
ENRICHMENT_WORKERS = 4


class EnrichmentBudget:
    """One cumulative optional-work allowance per ingest run, including retries."""

    def __init__(self, seconds=ENRICHMENT_SECONDS, *, runner=None, clock=None):
        self.remaining = max(0.0, float(seconds))
        self.runner = runner or run_distill_batch
        self.clock = clock or time.monotonic
        self.exhausted = False

    def run(self, docs):
        results = []
        while len(results) < len(docs) and self.remaining > 0 and not self.exhausted:
            wave = docs[len(results):len(results) + ENRICHMENT_WORKERS]
            started = self.clock()
            try:
                completed = self.runner(wave, self.remaining)
            finally:
                self.remaining = max(0.0, self.remaining - (self.clock() - started))
            results.extend(completed)
            # An entire exhausted ladder wave is enough evidence to stop
            # retrying the same unavailable providers for this process run.
            if not any(result is not None for result in completed):
                self.exhausted = True
        return results, len(docs) - len(results)


def _kill_worker_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_distill_batch(docs, timeout, *, clock=None):
    """Return completed results in input order; unfinished attempts are None.

    Documents travel on stdin; credentials remain inherited environment only.
    stdout is an incremental result protocol so a timeout keeps prior successes.
    """
    clock = clock or time.monotonic
    started = clock()
    results = [None] * len(docs)
    process = None
    output = ""
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "knowledge.distill", "--worker"],
            cwd=str(_PROJECT_ROOT / "scripts"),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, start_new_session=True,
        )
        try:
            output, _ = process.communicate(json.dumps(docs), timeout=max(0.001, timeout - (clock() - started)))
        except subprocess.TimeoutExpired as exc:
            output = exc.output or ""
            _kill_worker_group(process)
            output, _ = process.communicate(timeout=5)
    except subprocess.TimeoutExpired as exc:
        output = exc.output or output
        print("[knowledge-distill] worker cleanup deadline reached", file=sys.stderr)
    except (OSError, subprocess.SubprocessError):
        print("[knowledge-distill] bounded worker failed — deferring enrichment", file=sys.stderr)
    finally:
        if process is not None:
            # Also kill CLI descendants after normal exit or parent cancellation.
            _kill_worker_group(process)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print("[knowledge-distill] worker reap deadline reached", file=sys.stderr)
            finally:
                for pipe in (process.stdin, process.stdout):
                    if pipe is not None:
                        pipe.close()
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    for line in output.splitlines():
        try:
            index, payload = json.loads(line)
            if isinstance(index, int) and 0 <= index < len(results):
                results[index] = _normalize_distillation(payload) if payload is not None else None
        except (ValueError, TypeError):
            # Corrupt output cannot turn optional enrichment into ingest failure.
            continue
    return results


def _worker():
    docs = json.load(sys.stdin)
    with ThreadPoolExecutor(max_workers=ENRICHMENT_WORKERS) as pool:
        pending = {pool.submit(distill, title, content): index for index, (title, content) in enumerate(docs)}
        for future in as_completed(pending):
            try:
                result = future.result()
            except Exception:
                result = None
            print(json.dumps([pending[future], result]), flush=True)


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit("internal knowledge distillation worker")
    _worker()

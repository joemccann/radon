#!/usr/bin/env python3.13
"""Held-out posts -> llama-server -> predictions JSONL (arms B and C)."""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from newsfeed.slm.contract import (  # noqa: E402
    SLM_DEFAULT_URL,
    SLM_MAX_TOKENS,
    SLM_RESPONSE_FORMAT,
    SLM_SYSTEM,
    SLM_TAGGER_NAME,
    SLM_TEMPERATURE,
)
from newsfeed.slm.eval import load_jsonl  # noqa: E402


def user_content(row: dict) -> str:
    for msg in row.get("messages") or []:
        if msg.get("role") == "user":
            return str(msg.get("content") or "")
    return str(row.get("user") or "")


def predict_one(url: str, user: str, *, system: str, timeout: float) -> tuple[dict, float]:
    body = {
        "model": SLM_TAGGER_NAME,
        "temperature": SLM_TEMPERATURE,
        "max_tokens": SLM_MAX_TOKENS,
        "response_format": SLM_RESPONSE_FORMAT,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    req = urllib.request.Request(
        f"{url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    latency = time.perf_counter() - started
    text = (((payload.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
    try:
        pred = json.loads(text)
    except json.JSONDecodeError:
        pred = {"tags": []}
    return pred, latency


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--arm", choices=("B", "C"), default="C")
    parser.add_argument("--url", default=SLM_DEFAULT_URL)
    parser.add_argument("--system-file", default="")
    parser.add_argument("--timeout", type=float, default=12)
    args = parser.parse_args(argv)
    system = SLM_SYSTEM
    if args.arm == "B" and args.system_file:
        system = Path(args.system_file).read_text(encoding="utf-8")
    rows = load_jsonl(Path(args.gold))
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as handle:
        for row in rows:
            try:
                pred, latency = predict_one(args.url, user_content(row), system=system, timeout=args.timeout)
                record = {"id": row.get("id"), "pred": pred, "latency_s": latency, "arm": args.arm}
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                record = {
                    "id": row.get("id"),
                    "pred": {"tags": []},
                    "slm_status": "unavailable",
                    "error": type(exc).__name__,
                    "arm": args.arm,
                }
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

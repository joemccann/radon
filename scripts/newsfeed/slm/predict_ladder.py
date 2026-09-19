#!/usr/bin/env python3.13
"""Held-out posts -> complete_text_json (subscription only) -> predictions JSONL (arm A).

    RADON_LADDER_ALLOW_PREPAID must be unset. About 200-1400 subscription calls.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from clients.model_ladder import accept_tags_payload, complete_text_json  # noqa: E402
from newsfeed.slm.eval import load_jsonl  # noqa: E402
from newsfeed.slm.predict_slm import user_content  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--system-file", default="")
    args = parser.parse_args(argv)
    if (os.environ.get("RADON_LADDER_ALLOW_PREPAID") or "").strip():
        sys.stderr.write("predict_ladder.py refuses RADON_LADDER_ALLOW_PREPAID (arm A is subscription only)\n")
        return 2
    system = Path(args.system_file).read_text(encoding="utf-8") if args.system_file else ""
    rows = load_jsonl(Path(args.gold))
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as handle:
        for row in rows:
            started = time.perf_counter()
            try:
                result = complete_text_json(
                    user_content(row),
                    system=system,
                    accept=accept_tags_payload,
                    log_prefix="slm-bakeoff-A",
                )
                latency = time.perf_counter() - started
                record = {
                    "id": row.get("id"),
                    "pred": result.data,
                    "latency_s": latency,
                    "provider": result.provider,
                    "arm": "A",
                    "cost_per_1k_usd": None,
                    "subscription_calls_per_1k": 1000.0,
                }
            except Exception as exc:  # noqa: BLE001
                record = {
                    "id": row.get("id"),
                    "pred": {"tags": []},
                    "slm_status": "unparseable",
                    "error": type(exc).__name__,
                    "arm": "A",
                }
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

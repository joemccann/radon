#!/usr/bin/env python3
"""Phase 2 one-shot: copy data/menthorq_cache/cta_*.json into menthorq_cta table.

Idempotent (ON CONFLICT DO UPDATE). Safe to re-run."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(PROJECT_ROOT / "web" / ".env")

from db.writer import upsert_menthorq_cta  # noqa: E402

CACHE_DIR = PROJECT_ROOT / "data" / "menthorq_cache"
PATTERN = re.compile(r"^cta_(\d{4}-\d{2}-\d{2})\.json$")


def main() -> int:
    if not CACHE_DIR.exists():
        print(f"[bootstrap] no cache dir at {CACHE_DIR}", file=sys.stderr)
        return 0

    files = sorted(CACHE_DIR.glob("cta_*.json"))
    print(f"[bootstrap] found {len(files)} CTA files")

    written = 0
    for f in files:
        m = PATTERN.match(f.name)
        if not m:
            continue
        date = m.group(1)
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[bootstrap] skip {f.name}: {exc}", file=sys.stderr)
            continue
        fetched_at = payload.get("fetched_at") if isinstance(payload, dict) else None
        upsert_menthorq_cta(date, payload, fetched_at=fetched_at)
        written += 1

    print(f"[bootstrap] upserted {written} cache rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

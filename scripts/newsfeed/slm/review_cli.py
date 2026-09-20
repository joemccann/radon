#!/usr/bin/env python3.13
"""C.4 human-ok capture. Prints title, excerpt, tags; records y / n / c TAGS.

    python3.13 scripts/newsfeed/slm/review_cli.py --in data/slm/tagger/v1/test.jsonl \\
        --out data/slm/tagger/v1/gold_human.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

_SCRIPTS = Path(__file__).resolve().parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from newsfeed.slm.contract import normalise_tags  # noqa: E402
from newsfeed.slm.eval import gold_tags, load_jsonl  # noqa: E402


# Scraped post text reaches the reviewer's terminal verbatim; C0/C1 control
# bytes (ANSI escapes, bare newlines) could spoof the review display and bias
# the promotion gate. Strip them before echoing.
_TTY_UNSAFE_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def tty_safe(text: str) -> str:
    return _TTY_UNSAFE_RE.sub(" ", text)


def parse_review(line: str) -> tuple[str, list[str] | None] | None:
    text = (line or "").strip()
    if not text:
        return None
    lower = text.lower()
    if lower in {"y", "yes"}:
        return "y", None
    if lower in {"n", "no"}:
        return "n", None
    if lower.startswith("c ") or lower.startswith("c\t"):
        raw = text[1:].strip().replace(",", " ")
        tags = normalise_tags([part for part in raw.split() if part])
        if len(tags) != 3:
            return None
        return "c", tags
    return None


def review_rows(
    rows: list[dict],
    *,
    stdin: TextIO,
    stdout: TextIO,
    now: datetime | None = None,
) -> list[dict]:
    reviewed_at = (now or datetime.now(timezone.utc)).isoformat()
    out: list[dict] = []
    for row in rows:
        tags = gold_tags(row)
        title = ""
        body = ""
        for msg in row.get("messages") or []:
            if msg.get("role") == "user":
                content = str(msg.get("content") or "")
                if content.startswith("Title: "):
                    title, _, rest = content.partition("\n")
                    title = title[len("Title: ") :]
                    body = rest[len("Body: ") :] if rest.startswith("Body: ") else rest
        stdout.write(f"id={row.get('id')}\nTitle: {tty_safe(title)}\nBody: {tty_safe(body[:240])}\nTags: {tags}\n[y/n/c TAG1,TAG2,TAG3]\n")
        stdout.flush()
        verdict = parse_review(stdin.readline())
        if verdict is None:
            continue
        decision, corrected = verdict
        record = {
            **row,
            "human": decision,
            "reviewed_at": reviewed_at,
            "gold": tags,
        }
        if corrected is not None:
            record["corrected_tags"] = corrected
            record["gold"] = corrected
        out.append(record)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="src", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    rows = load_jsonl(Path(args.src))
    reviewed = review_rows(rows, stdin=sys.stdin, stdout=sys.stdout)
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as handle:
        for row in reviewed:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

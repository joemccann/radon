#!/usr/bin/env python3.13
"""Extract the SLM tagger corpus from Turso ``posts`` only (HR-8, HR-4).

Operator command (credentialed host with TURSO_DB_URL + TURSO_AUTH_TOKEN):

    python3.13 scripts/newsfeed/slm/build_dataset.py --out data/slm/tagger/v1

This module has one input: POSTS_SQL. Tests inject a row iterator. The
corpus is never committed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

_SCRIPTS = Path(__file__).resolve().parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from newsfeed.slm.contract import (  # noqa: E402
    BODY_CHAR_LIMIT,
    MIN_BODY_CHARS,
    SLM_SYSTEM,
    SOURCES,
    build_user_prompt,
    collapse_ws,
    normalise_tags,
    prompt_contract_sha256,
    strip_pii,
)

POSTS_SQL = """
SELECT p.id, p.title, p.content, p.timestamp, p.tags, p.tags_text, p.tags_vision, p.images,
       CASE WHEN s.post_id IS NULL THEN 0 ELSE 1 END AS is_research
FROM posts p
LEFT JOIN research_post_sources s ON s.post_id = p.id
WHERE p.id > ? ORDER BY p.id LIMIT 200;
"""

TEST_DAYS = 60
VALID_DAYS = 30
TRAIN_MIN_ROWS = 1500
TEST_DAYS_SHRUNK = 45


def parse_tags_text(raw: Any) -> list[str] | None:
    """Return tags_text only when it is exactly 3 strings that survive normalise."""
    value = raw
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, list) or len(value) != 3:
        return None
    if not all(isinstance(item, str) for item in value):
        return None
    normalised = normalise_tags(value)
    if normalised != list(value):
        return None
    return normalised


def parse_timestamp(raw: Any) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def content_hash(title: str, content: str) -> str:
    blob = f"{title}\n{content}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def apply_row_rules(row: dict[str, Any]) -> dict[str, Any] | None:
    """C.2 rules 1-6. Returns a working row or None to drop."""
    if int(row.get("is_research") or 0) == 1:
        return None
    if row.get("label_source") == "slm":
        return None
    labels = parse_tags_text(row.get("tags_text"))
    if labels is None:
        return None
    title = str(row.get("title") or "")
    content = str(row.get("content") or "")
    cleaned, email_n, url_n = strip_pii(content)
    if len(collapse_ws(cleaned)) < MIN_BODY_CHARS:
        return None
    ts = parse_timestamp(row.get("timestamp"))
    if ts is None:
        return None
    user = build_user_prompt(title, cleaned)
    return {
        "id": str(row.get("id") or ""),
        "title": title,
        "content": cleaned,
        "timestamp": ts.isoformat(),
        "ts": ts,
        "tags_text": labels,
        "tags_vision": row.get("tags_vision"),
        "images": row.get("images"),
        "publisher": row.get("publisher") or "market-ear",
        "label_source": "ladder",
        "pii_emails": email_n,
        "pii_urls": url_n,
        "content_sha256": content_hash(title, cleaned),
        "user": user,
    }


def dedupe_earliest(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row["content_sha256"]
        prev = best.get(key)
        if prev is None or row["ts"] < prev["ts"]:
            best[key] = row
    return sorted(best.values(), key=lambda r: (r["ts"], r["id"]))


def split_by_time(
    rows: Sequence[dict[str, Any]],
    *,
    extract_at: datetime,
    test_days: int = TEST_DAYS,
    valid_days: int = VALID_DAYS,
) -> dict[str, list[dict[str, Any]]]:
    test_cut = extract_at - timedelta(days=test_days)
    valid_cut = test_cut - timedelta(days=valid_days)
    train: list[dict[str, Any]] = []
    valid: list[dict[str, Any]] = []
    test: list[dict[str, Any]] = []
    for row in rows:
        if row["ts"] >= test_cut:
            test.append(row)
        elif row["ts"] >= valid_cut:
            valid.append(row)
        else:
            train.append(row)
    if (
        len(rows) >= TRAIN_MIN_ROWS
        and len(train) < TRAIN_MIN_ROWS
        and test_days > TEST_DAYS_SHRUNK
    ):
        return split_by_time(
            rows,
            extract_at=extract_at,
            test_days=TEST_DAYS_SHRUNK,
            valid_days=valid_days,
        )
    publishers = {row.get("publisher") or "market-ear" for row in rows}
    if len(publishers) > 1:
        newest = max(publishers)
        held = [row for row in rows if row.get("publisher") == newest]
        rest = [row for row in rows if row.get("publisher") != newest]
        inner = split_by_time(
            rest,
            extract_at=extract_at,
            test_days=test_days,
            valid_days=valid_days,
        )
        inner["held_publisher"] = held
        inner["held_publisher_name"] = newest
        return inner
    return {"train": train, "valid": valid, "test": test}


def _tag_counts(rows: Sequence[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        counts.update(row["tags_text"])
    return counts


def rare_tags(train_rows: Sequence[dict[str, Any]]) -> list[str]:
    counts = _tag_counts(train_rows)
    eligible = {tag: n for tag, n in counts.items() if n >= 3}
    if not eligible:
        return []
    values = sorted(eligible.values())
    q1 = values[max(0, len(values) // 4 - 1)]
    return sorted(tag for tag, n in eligible.items() if n <= q1)


def chat_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "messages": [
            {"role": "system", "content": SLM_SYSTEM},
            {"role": "user", "content": row["user"]},
            {
                "role": "assistant",
                "content": json.dumps({"tags": row["tags_text"]}, separators=(",", ":")),
            },
        ],
        "id": row["id"],
        "timestamp": row["timestamp"],
        "label_source": row["label_source"],
        "publisher": row.get("publisher") or "market-ear",
    }


def write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(chat_record(row), separators=(",", ":")) + "\n")


def fetch_posts(execute: Callable[[str, tuple[Any, ...]], Sequence[Any]]) -> list[dict[str, Any]]:
    """Paginate POSTS_SQL via an injected execute(sql, args) -> rows."""
    columns = (
        "id",
        "title",
        "content",
        "timestamp",
        "tags",
        "tags_text",
        "tags_vision",
        "images",
        "is_research",
    )
    cursor = ""
    out: list[dict[str, Any]] = []
    while True:
        raw_rows = list(execute(POSTS_SQL, (cursor,)))
        if not raw_rows:
            break
        for raw in raw_rows:
            if isinstance(raw, dict):
                row = raw
            else:
                row = dict(zip(columns, raw))
            out.append(row)
            cursor = str(row.get("id") or cursor)
        if len(raw_rows) < 200:
            break
    return out


def build_dataset(
    raw_rows: Sequence[dict[str, Any]],
    *,
    taxonomy: Sequence[str],
    extract_at: datetime | None = None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    extract_at = extract_at or datetime.now(timezone.utc)
    kept: list[dict[str, Any]] = []
    pii_emails = 0
    pii_urls = 0
    for raw in raw_rows:
        row = apply_row_rules(raw)
        if row is None:
            continue
        pii_emails += int(row["pii_emails"])
        pii_urls += int(row["pii_urls"])
        kept.append(row)
    kept = dedupe_earliest(kept)
    splits = split_by_time(kept, extract_at=extract_at)
    train, valid, test = splits["train"], splits["valid"], splits["test"]
    taxo = [str(t) for t in taxonomy]
    taxo_blob = json.dumps(sorted(taxo), separators=(",", ":")).encode("utf-8")
    rare = rare_tags(train)
    label_distribution = dict(_tag_counts(train))
    cutoff_dates = {
        "extract_at": extract_at.isoformat(),
        "test_start": (extract_at - timedelta(days=TEST_DAYS)).isoformat(),
        "valid_start": (extract_at - timedelta(days=TEST_DAYS + VALID_DAYS)).isoformat(),
    }
    if train and valid and test:
        cutoff_dates["max_train"] = max(r["timestamp"] for r in train)
        cutoff_dates["min_valid"] = min(r["timestamp"] for r in valid)
        cutoff_dates["max_valid"] = max(r["timestamp"] for r in valid)
        cutoff_dates["min_test"] = min(r["timestamp"] for r in test)
    extract_src = Path(__file__).read_bytes()
    manifest = {
        "sources": list(SOURCES),
        "rows": len(kept),
        "split_counts": {
            "train": len(train),
            "valid": len(valid),
            "test": len(test),
        },
        "taxonomy_size": len(taxo),
        "taxonomy_sha256": hashlib.sha256(taxo_blob).hexdigest(),
        "cutoff_dates": cutoff_dates,
        "extract_sha256": hashlib.sha256(extract_src).hexdigest(),
        "extracted_at": extract_at.isoformat(),
        "pii_email_count": pii_emails,
        "pii_url_count": pii_urls,
        "rare_tags": rare,
        "label_distribution": label_distribution,
        "prompt_contract_sha256": prompt_contract_sha256(),
        "body_char_limit": BODY_CHAR_LIMIT,
        "label_source_counts": {"ladder": len(kept)},
    }
    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        write_jsonl(out_dir / "train.jsonl", train)
        write_jsonl(out_dir / "valid.jsonl", valid)
        write_jsonl(out_dir / "test.jsonl", test)
        gold = out_dir / "gold_human.jsonl"
        if not gold.exists():
            gold.write_text("", encoding="utf-8")
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return {"manifest": manifest, "splits": splits, "rows": kept}


def derive_taxonomy(rows: Sequence[dict[str, Any]]) -> list[str]:
    tags: set[str] = set()
    for row in rows:
        tags.update(row["tags_text"])
    return sorted(tags)


def main(argv: list[str] | None = None) -> int:
    """Operator entry: posts rows come from fetch_posts(execute).

    Wire Turso in ``extract_cli.py`` so this module stays single-input.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/slm/tagger/v1")
    parser.parse_args(argv)
    sys.stderr.write(
        "Use python3.13 scripts/newsfeed/slm/extract_cli.py --out "
        "data/slm/tagger/v1 on a host with TURSO_DB_URL and TURSO_AUTH_TOKEN.\n"
    )
    return 2

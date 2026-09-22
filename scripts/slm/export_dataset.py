"""Export the SLM fine-tune corpus from Turso as chat-format JSONL.

One writer for every SLM task corpus. Each task reproduces the exact prompts
the live caller sends, so a trained adapter is a drop-in ladder rung:

  tagger   posts.tags_text (text-tagger output) under the tagger.js system
           and user prompts. Time split (HR-4): test = newest 60 days,
           valid = 30 days before, train = older.
  distill  knowledge.summary + metadata.tickers under distill.py's prompt,
           egress-scrubbed. Deterministic hash split on the DOCUMENT key
           (never the chunk) so chunks of one doc never straddle splits.

Rows that would not pass the live acceptance rules (exactly 3 normalised
tags; non-empty summary) are dropped so the model never learns a rejected
answer. Layout (gitignored, never committed):

  <out>/tagger/v1/{train,valid,test}.jsonl + manifest.json
  <out>/distill/v1/{train,valid,test}.jsonl + manifest.json

Usage: python3.13 scripts/slm/export_dataset.py [--out data/slm]
Requires TURSO_DB_URL and TURSO_AUTH_TOKEN (Hetzner or a credentialed host).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from knowledge.distill import (  # noqa: E402
    MAX_CONTENT_CHARS,
    _SYSTEM_PROMPT as DISTILL_SYSTEM_PROMPT,
    _scrub_for_egress,
)
from newsfeed.slm.contract import (  # noqa: E402
    BODY_CHAR_LIMIT,
    MIN_BODY_CHARS,
    build_user_prompt,
    collapse_ws,
    normalise_tags,
    prompt_contract_sha256,
    strip_pii,
    tagger_system_prompt,
)

TASKS = ("tagger", "distill")
PAGE = 200  # Hrana rule 1: paginate on an id cursor, never one unbounded SELECT
TEST_DAYS = 60
VALID_DAYS = 30
TRAIN_MIN_ROWS = 1500
TEST_DAYS_SHRUNK = 45

# Research posts (migration 0071) are a different distribution and provenance;
# v1 is the Market Ear tagger.
POSTS_SQL = (
    "SELECT p.id, p.title, p.content, p.timestamp, p.images, p.tags, p.tags_text,"
    " CASE WHEN s.post_id IS NULL THEN 0 ELSE 1 END AS is_research"
    " FROM posts p LEFT JOIN research_post_sources s ON s.post_id = p.id"
    f" WHERE p.id > ? ORDER BY p.id LIMIT {PAGE}"
)
KNOWLEDGE_SQL = (
    "SELECT id, source, doc_key, chunk_ix, title, summary, content, metadata FROM knowledge"
    " WHERE summary IS NOT NULL AND TRIM(summary) <> ''"
    f" AND id > ? ORDER BY id LIMIT {PAGE}"
)


# ── helpers ────────────────────────────────────────────────────────────────

def _paginate(db: Any, sql: str, first_cursor: Any) -> list[tuple]:
    cursor = first_cursor
    out: list[tuple] = []
    while True:
        rows = list(db.execute(sql, (cursor,)).fetchall())
        out.extend(rows)
        if len(rows) < PAGE:
            return out
        cursor = rows[-1][0]


def _json_list(raw: Any) -> list | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, list) else None


def _parse_ts(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _example(ex_id: str, system: str, user: str, answer: dict, **extra: Any) -> dict:
    return {
        "id": ex_id,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
            {"role": "assistant", "content": json.dumps(answer, separators=(",", ":"))},
        ],
        **extra,
    }


def read_taxonomy(db: Any) -> list[str]:
    rows = db.execute("SELECT tag FROM tag_taxonomy ORDER BY tag COLLATE NOCASE").fetchall()
    return normalise_tags([r[0] for r in rows])


# ── tagger ─────────────────────────────────────────────────────────────────

def _tagger_label(images: Any, tags: Any, tags_text: Any) -> list[str] | None:
    raw = _json_list(tags_text)
    if raw is None:
        # Merged tags stand in for the text tagger only when no vision tagger
        # could have contributed.
        if _json_list(images):
            return None
        raw = _json_list(tags)
    if raw is None or len(raw) != 3:
        return None
    normalised = normalise_tags(raw)
    return normalised if len(normalised) == 3 else None


def build_tagger_rows(db: Any) -> tuple[list[dict], dict[str, int]]:
    """Row rules: research excluded, exactly 3 tags, PII stripped, min body,
    dedupe on content keeping the earliest timestamp."""
    pii = {"emails": 0, "urls": 0}
    best: dict[str, dict] = {}
    for pid, title, content, ts_raw, images, tags, tags_text, is_research in _paginate(db, POSTS_SQL, ""):
        if int(is_research or 0):
            continue
        label = _tagger_label(images, tags, tags_text)
        ts = _parse_ts(ts_raw)
        if label is None or ts is None:
            continue
        cleaned, n_email, n_url = strip_pii(str(content or ""))
        if len(collapse_ws(cleaned)) < MIN_BODY_CHARS:
            continue
        pii["emails"] += n_email
        pii["urls"] += n_url
        key = hashlib.sha256(f"{title or ''}\n{cleaned}".encode()).hexdigest()
        row = {"id": str(pid), "ts": ts, "title": str(title or ""), "content": cleaned, "tags": label}
        if key not in best or ts < best[key]["ts"]:
            best[key] = row
    return sorted(best.values(), key=lambda r: (r["ts"], r["id"])), pii


def split_by_time(rows: Sequence[dict], *, extract_at: datetime, test_days: int = TEST_DAYS) -> dict[str, list[dict]]:
    test_cut = extract_at - timedelta(days=test_days)
    valid_cut = test_cut - timedelta(days=VALID_DAYS)
    splits: dict[str, list[dict]] = {"train": [], "valid": [], "test": []}
    for row in rows:
        bucket = "test" if row["ts"] >= test_cut else "valid" if row["ts"] >= valid_cut else "train"
        splits[bucket].append(row)
    if len(rows) >= TRAIN_MIN_ROWS and len(splits["train"]) < TRAIN_MIN_ROWS and test_days > TEST_DAYS_SHRUNK:
        return split_by_time(rows, extract_at=extract_at, test_days=TEST_DAYS_SHRUNK)
    splits["test_days"] = test_days  # type: ignore[assignment]
    return splits


def rare_tags(train_rows: Sequence[dict]) -> list[str]:
    counts = Counter(t for r in train_rows for t in r["tags"])
    eligible = {tag: n for tag, n in counts.items() if n >= 3}
    if not eligible:
        return []
    values = sorted(eligible.values())
    q1 = values[max(0, len(values) // 4 - 1)]
    return sorted(tag for tag, n in eligible.items() if n <= q1)


def export_tagger(db: Any, out_dir: Path, *, extract_at: datetime | None = None) -> dict:
    extract_at = extract_at or datetime.now(timezone.utc)
    taxonomy = read_taxonomy(db)
    system = tagger_system_prompt(taxonomy)
    rows, pii = build_tagger_rows(db)
    splits = split_by_time(rows, extract_at=extract_at)
    test_days = splits.pop("test_days")
    for name, part in splits.items():
        _write_jsonl(out_dir / f"{name}.jsonl", [
            _example(r["id"], system, build_user_prompt(r["title"], r["content"]), {"tags": r["tags"]},
                     timestamp=r["ts"].isoformat(), label_source="ladder")
            for r in part
        ])
    train = splits["train"]
    manifest = {
        "task": "tagger",
        "sources": ["turso.posts"],
        "rows": len(rows),
        "split_counts": {k: len(v) for k, v in splits.items()},
        "cutoff_dates": {
            "extract_at": extract_at.isoformat(),
            "test_start": (extract_at - timedelta(days=test_days)).isoformat(),
            "valid_start": (extract_at - timedelta(days=test_days + VALID_DAYS)).isoformat(),
        },
        "taxonomy_size": len(taxonomy),
        "taxonomy_sha256": hashlib.sha256(json.dumps(taxonomy, separators=(",", ":")).encode()).hexdigest(),
        "prompt_contract_sha256": prompt_contract_sha256(),
        "body_char_limit": BODY_CHAR_LIMIT,
        "pii_email_count": pii["emails"],
        "pii_url_count": pii["urls"],
        "rare_tags": rare_tags(train),
        "label_distribution": dict(Counter(t for r in train for t in r["tags"])),
        "label_source_counts": {"ladder": len(rows)},
        "extract_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "extracted_at": extract_at.isoformat(),
    }
    _write_manifest(out_dir, manifest)
    return manifest


# ── distill ────────────────────────────────────────────────────────────────

def _doc_bucket(source: str, doc_key: str) -> str:
    digest = hashlib.sha256(f"{source}:{doc_key}".encode()).digest()
    frac = int.from_bytes(digest[:4], "big") / 2**32
    return "test" if frac < 0.1 else "valid" if frac < 0.2 else "train"


def export_distill(db: Any, out_dir: Path) -> dict:
    splits: dict[str, list[dict]] = {"train": [], "valid": [], "test": []}
    for _id, source, doc_key, chunk_ix, title, summary, content, metadata in _paginate(db, KNOWLEDGE_SQL, 0):
        document = f"Title: {title}\n\n{content}" if title else content
        user = _scrub_for_egress(document[:MAX_CONTENT_CHARS])
        try:
            meta = json.loads(metadata) if isinstance(metadata, str) else (metadata or {})
        except json.JSONDecodeError:
            meta = {}
        tickers = meta.get("tickers") if isinstance(meta, dict) else None
        tickers = [t for t in tickers if isinstance(t, str)] if isinstance(tickers, list) else []
        ex = _example(f"{source}:{doc_key}:{chunk_ix}", DISTILL_SYSTEM_PROMPT, user,
                      {"summary": summary.strip(), "tickers": tickers}, doc_key=f"{source}:{doc_key}")
        splits[_doc_bucket(source, doc_key)].append(ex)
    for name, part in splits.items():
        _write_jsonl(out_dir / f"{name}.jsonl", part)
    manifest = {
        "task": "distill",
        "sources": ["turso.knowledge"],
        "rows": sum(len(v) for v in splits.values()),
        "split_counts": {k: len(v) for k, v in splits.items()},
        "split": "sha256(source:doc_key) 80/10/10, document-level",
        "extract_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_manifest(out_dir, manifest)
    return manifest


# ── io ─────────────────────────────────────────────────────────────────────

def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _write_manifest(out_dir: Path, manifest: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def export(db: Any, out_root: Path, *, extract_at: datetime | None = None) -> dict:
    out_root = Path(out_root)
    return {
        "tagger": export_tagger(db, out_root / "tagger" / "v1", extract_at=extract_at),
        "distill": export_distill(db, out_root / "distill" / "v1"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(_SCRIPTS_DIR.parent / "data" / "slm"))
    args = parser.parse_args(argv)
    from db.client import get_db

    manifests = export(get_db(), Path(args.out))
    print(json.dumps({k: v["split_counts"] for k, v in manifests.items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

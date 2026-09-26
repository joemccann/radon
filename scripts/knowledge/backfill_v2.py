"""Backfill knowledge.embedding_v2 for rows the 2048-d index does not have yet.

The migration creates idx_knowledge_embedding_v2 before any of these writes.
Rows are selected with WHERE embedding_v2 IS NULL, so a re-run is a no-op
and a stopped run resumes at the next null row. Dry-run counts candidates
and does not call the embeddings API.

    python -m knowledge.backfill_v2 --dry-run
    python -m knowledge.backfill_v2 --batch-size 16 --min-interval 0.25
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from knowledge.embed import EMBEDDING_DIM_V2, embed_passages, embedding_text  # noqa: E402
from knowledge.store import backfill_embedding_v2_batch  # noqa: E402


def backfill_missing_v2(
    db,
    embed=None,
    *,
    batch_size: int = 16,
    limit: int | None = None,
    dry_run: bool = False,
    min_interval: float = 0.0,
    sleep=None,
    progress=None,
) -> dict:
    """Fill embedding_v2 where it is null. embed defaults to embed_passages."""
    embed = embed_passages if embed is None else embed
    batch_size = max(1, int(batch_size))
    total = int(db.execute("SELECT COUNT(*) FROM knowledge WHERE embedding_v2 IS NULL").fetchone()[0])
    target = total if limit is None else min(total, max(0, int(limit)))
    if dry_run:
        result = {"candidates": target, "updated": 0, "remaining": total, "dry_run": True}
        _progress(progress, result)
        return result
    updated = 0
    processed = 0
    last_id = 0
    row_errors: list[dict] = []
    while processed < target:
        take = min(batch_size, target - processed)
        rows = db.execute(
            "SELECT id, title, summary, content FROM knowledge "
            "WHERE embedding_v2 IS NULL AND id > ? ORDER BY id LIMIT ?",
            (last_id, take),
        ).fetchall()
        if not rows:
            break
        vectors = embed([embedding_text(title, summary, content) for _id, title, summary, content in rows])
        if len(vectors) != len(rows):
            raise RuntimeError("embedding batch length does not match the row batch")
        pairs = []
        for (row_id, _title, _summary, _content), vector in zip(rows, vectors):
            if len(vector) != EMBEDDING_DIM_V2:
                raise RuntimeError(f"expected {EMBEDDING_DIM_V2} dims, got {len(vector)}")
            pairs.append((row_id, vector))
        # Advance past a row that cannot fit so one oversized vector cannot
        # stall the id cursor. The other rows in the batch still commit.
        batch_errors = backfill_embedding_v2_batch(db, pairs)
        failed = {item["id"] for item in batch_errors}
        row_errors.extend(batch_errors)
        for row_id, _vector in pairs:
            last_id = row_id
            processed += 1
            if row_id not in failed:
                updated += 1
        if updated < target and min_interval > 0:
            if sleep is not None:
                sleep(min_interval)
            else:
                time.sleep(min_interval)
    remaining = int(db.execute("SELECT COUNT(*) FROM knowledge WHERE embedding_v2 IS NULL").fetchone()[0])
    result = {"candidates": target, "updated": updated, "remaining": remaining, "dry_run": False}
    if row_errors:
        result["row_errors"] = row_errors
    _progress(progress, result)
    return result


def _progress(progress, result: dict) -> None:
    line = (
        f"backfill_v2 candidates={result['candidates']} updated={result['updated']} "
        f"remaining={result['remaining']} dry_run={result['dry_run']}"
    )
    if progress is not None:
        progress(line)
    else:
        print(line, file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--min-interval", type=float, default=0.25)
    args = parser.parse_args(argv)
    from db.client import get_db

    result = backfill_missing_v2(
        get_db(),
        batch_size=args.batch_size,
        limit=args.limit,
        dry_run=args.dry_run,
        min_interval=args.min_interval,
    )
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

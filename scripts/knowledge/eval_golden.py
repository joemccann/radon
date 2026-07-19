"""Golden-set retrieval eval (Phase 1; gates the Phase 2 query surfaces).

Runs each golden question through hybrid_search and scores hit@5: a question
is a hit when any returned result's (source, doc_key) matches one of the
question's expected patterns (doc_key_pattern is a regex, re.search
semantics). Phase 2 ships only if overall hit@5 >= 0.8 on the curated set.

The JSON summary key is always "overall_hit_at_5" — the canonical gate metric
— even when a caller overrides `limit` for exploration.

CLI (reads the PRODUCTION Turso DB, read-only):
    .venv/bin/python scripts/knowledge/eval_golden.py [path/to/golden_set.json]
Per-question table goes to stderr, JSON summary to stdout. Embeddings come
from knowledge.embed.get_embedder() when available; otherwise the eval
degrades to the FTS-only leg.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Callable, Sequence

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from knowledge.retrieve import hybrid_search  # noqa: E402

DEFAULT_GOLDEN_PATH = Path(__file__).with_name("golden_set.json")
DEFAULT_LIMIT = 5

QueryEmbedder = Callable[[str], Sequence[float]]


def run_golden(
    db,
    golden,
    *,
    query_embedder: QueryEmbedder | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict:
    """Evaluate every golden question; returns
    {"overall_hit_at_5": rate, "per_question": [...]} best kept JSON-serializable."""
    questions = golden.get("questions", []) if isinstance(golden, dict) else list(golden)
    per_question = [
        _evaluate_question(db, entry, query_embedder, limit) for entry in questions
    ]
    hits = sum(1 for outcome in per_question if outcome["hit"])
    overall = hits / len(per_question) if per_question else 0.0
    return {"overall_hit_at_5": overall, "per_question": per_question}


def _evaluate_question(db, entry: dict, query_embedder: QueryEmbedder | None, limit: int) -> dict:
    question = entry["question"]
    embedding = query_embedder(question) if query_embedder is not None else None
    results = hybrid_search(
        db,
        question,
        query_embedding=embedding,
        scopes=entry.get("scopes"),
        limit=limit,
    )
    matched = _first_expected_match(entry["expected"], results)
    return {
        "question": question,
        "hit": matched is not None,
        "matched": matched,
        "results": [
            {"source": row["source"], "doc_key": row["doc_key"], "score": row["score"]}
            for row in results
        ],
    }


def _first_expected_match(expected: Sequence[dict], results: Sequence[dict]) -> dict | None:
    for row in results:
        for want in expected:
            if row["source"] == want["source"] and re.search(
                want["doc_key_pattern"], row["doc_key"]
            ):
                return {
                    "source": row["source"],
                    "doc_key": row["doc_key"],
                    "pattern": want["doc_key_pattern"],
                }
    return None


# ── CLI against the production DB ────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Golden-set retrieval eval: hit@5 against the production knowledge table."
    )
    parser.add_argument(
        "golden_path",
        nargs="?",
        type=Path,
        default=DEFAULT_GOLDEN_PATH,
        help="path to golden_set.json (default: the shipped set)",
    )
    args = parser.parse_args(argv)
    golden = json.loads(args.golden_path.read_text(encoding="utf-8"))
    if isinstance(golden, dict) and golden.get("draft"):
        print(
            "warning: golden set is marked draft:true — curate before trusting the gate",
            file=sys.stderr,
        )
    summary = run_golden(_production_db(), golden, query_embedder=_load_query_embedder())
    _print_table(summary, file=sys.stderr)
    json.dump(summary, sys.stdout, indent=2)
    print()


def _production_db():
    import os

    os.environ.setdefault("RADON_DB_NO_REPLICA", "1")
    from dotenv import load_dotenv

    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env")
    scripts_dir = str(project_root / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from db.client import get_db

    return get_db()


def _load_query_embedder() -> QueryEmbedder | None:
    try:
        from knowledge.embed import get_embedder

        embedder = get_embedder()
    except Exception as exc:  # missing module/deps/model — FTS-only is a valid mode
        print(f"embedder unavailable ({exc}); running FTS-only", file=sys.stderr)
        return None
    embed_call = embedder if callable(embedder) else getattr(embedder, "embed", None)
    if embed_call is None:
        print("embedder has no callable interface; running FTS-only", file=sys.stderr)
        return None
    return lambda text: _as_vector(embed_call([text]))


def _as_vector(value) -> list[float]:
    """Accept a plain vector or a batch-of-one (list/generator of vectors)."""
    values = list(value)
    if len(values) == 1 and not isinstance(values[0], (int, float)):
        values = list(values[0])
    return [float(component) for component in values]


def _print_table(summary: dict, *, file) -> None:
    per_question = summary["per_question"]
    for outcome in per_question:
        mark = "HIT " if outcome["hit"] else "MISS"
        top = ", ".join(
            f"{row['source']}:{row['doc_key']}" for row in outcome["results"][:3]
        )
        print(f"{mark}  {outcome['question'][:64]:<64}  {top}", file=file)
    hits = sum(1 for outcome in per_question if outcome["hit"])
    print(
        f"overall hit@5: {summary['overall_hit_at_5']:.2f} ({hits}/{len(per_question)})",
        file=file,
    )


if __name__ == "__main__":
    main()

"""Golden-set retrieval eval (v2).

Scores each question under hybrid / vector / keyword retrieval. Hybrid keeps
the production fusion path. Vector and keyword score a single raw leg (dedup
only; no recency, no per-source cap) so a miss is attributable to that leg.

Metrics per mode: hit@1, hit@5, recall@10, MRR, nDCG@10 (graded). Also broken
down by question category and by relevant-doc source. Top-10 results are kept
per question. `overall_hit_at_5` remains the hybrid hit@5 alias.

CLI (production Turso, read-only):
    .venv/bin/python scripts/knowledge/eval_golden.py --mode all
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from knowledge.embed import BACKEND_ENV, EMBEDDING_DIM, EMBEDDING_DIM_V2  # noqa: E402
from knowledge.retrieve import hybrid_search  # noqa: E402

DEFAULT_GOLDEN_PATH = Path(__file__).with_name("golden_set.json")
DEFAULT_LIMIT = 10
DEFAULT_MAX_DROP = 0.03
MODES = ("hybrid", "vector", "keyword")
CATEGORIES = ("exact_term", "paraphrase", "numeric", "recent", "cross_source")
SOURCES = ("journal", "evals", "docs", "newsfeed", "incidents")
SCOPES = ("trading", "research", "ops")
GRADES = (1, 2, 3)
REGRESSION_KEYS = ("hit_at_5", "mrr")

QueryEmbedder = Callable[[str], Sequence[float]]


def relevant_labels(entry: dict) -> list[dict]:
    """Normalize `relevant` or the legacy `expected` form (grade 3)."""
    raw = entry.get("relevant")
    if raw is None:
        raw = [
            {**want, "grade": 3}
            for want in entry.get("expected", [])
        ]
    labels = []
    for want in raw:
        pattern = want.get("doc_key_pattern") or want.get("doc_key") or ""
        grade = int(want.get("grade", 3))
        labels.append(
            {
                "source": want["source"],
                "doc_key_pattern": pattern,
                "grade": grade,
            }
        )
    return labels


def first_relevant_match(
    labels: Sequence[dict], results: Sequence[dict]
) -> tuple[int | None, dict | None]:
    """Return 1-based rank of the first relevant result and the match payload."""
    for rank, row in enumerate(results, 1):
        for want in labels:
            if _row_matches(row, want):
                return rank, {
                    "source": row["source"],
                    "doc_key": row["doc_key"],
                    "pattern": want["doc_key_pattern"],
                    "grade": want["grade"],
                    "rank": rank,
                }
    return None, None


def hit_at_k(ranks: Sequence[int | None], k: int) -> float:
    if not ranks:
        return 0.0
    return sum(1 for rank in ranks if rank is not None and rank <= k) / len(ranks)


def mean_reciprocal_rank(ranks: Sequence[int | None]) -> float:
    if not ranks:
        return 0.0
    return sum((1.0 / rank) if rank else 0.0 for rank in ranks) / len(ranks)


def recall_at_k(
    retrieved: Sequence[Sequence[dict]],
    labels_per_q: Sequence[Sequence[dict]],
    k: int,
) -> float:
    """Mean |relevant ∩ top-k| / |relevant| over questions with labels."""
    scores = []
    for results, labels in zip(retrieved, labels_per_q):
        if not labels:
            continue
        found = sum(
            1
            for want in labels
            if any(_row_matches(row, want) for row in results[:k])
        )
        scores.append(found / len(labels))
    return sum(scores) / len(scores) if scores else 0.0


def ndcg_at_k(
    retrieved: Sequence[Sequence[dict]],
    labels_per_q: Sequence[Sequence[dict]],
    k: int,
) -> float:
    scores = [
        _ndcg_one(results[:k], labels, k)
        for results, labels in zip(retrieved, labels_per_q)
        if labels
    ]
    return sum(scores) / len(scores) if scores else 0.0


def _ndcg_one(results: Sequence[dict], labels: Sequence[dict], k: int) -> float:
    gains = [_best_grade(row, labels) for row in results[:k]]
    dcg = _dcg(gains)
    ideal = sorted((int(want["grade"]) for want in labels), reverse=True)[:k]
    idcg = _dcg(ideal)
    return dcg / idcg if idcg else 0.0


def _dcg(gains: Sequence[float]) -> float:
    return sum(gain / math.log2(idx + 2) for idx, gain in enumerate(gains))


def _best_grade(row: dict, labels: Sequence[dict]) -> int:
    matched = [int(want["grade"]) for want in labels if _row_matches(row, want)]
    return max(matched) if matched else 0


def _row_matches(row: dict, want: dict) -> bool:
    pattern = want.get("doc_key_pattern") or ""
    if not pattern:
        return False
    return row["source"] == want["source"] and re.search(pattern, row["doc_key"]) is not None


def aggregate_metrics(
    ranks: Sequence[int | None],
    retrieved: Sequence[Sequence[dict]],
    labels_per_q: Sequence[Sequence[dict]],
) -> dict:
    return {
        "hit_at_1": hit_at_k(ranks, 1),
        "hit_at_5": hit_at_k(ranks, 5),
        "recall_at_10": recall_at_k(retrieved, labels_per_q, 10),
        "mrr": mean_reciprocal_rank(ranks),
        "ndcg_at_10": ndcg_at_k(retrieved, labels_per_q, 10),
    }


def compare_baseline(
    current: dict,
    baseline: dict,
    *,
    max_drop: float = DEFAULT_MAX_DROP,
) -> list[str]:
    """Return human-readable regression lines. Empty = no drop past max_drop."""
    if baseline.get("placeholder"):
        return ["baseline is a placeholder; write it after the first live run"]
    regressions = []
    current_modes = current.get("modes") or {}
    baseline_modes = baseline.get("modes") or {}
    for mode in current_modes:
        if mode not in baseline_modes:
            continue
        for key in REGRESSION_KEYS:
            before = baseline_modes[mode].get(key)
            after = current_modes[mode].get(key)
            if before is None or after is None:
                continue
            if float(before) - float(after) > max_drop + 1e-12:
                regressions.append(
                    f"{mode}.{key} {float(before):.3f} -> {float(after):.3f} "
                    f"(drop {float(before) - float(after):.3f} > {max_drop})"
                )
    return regressions


def baseline_payload(summary: dict) -> dict:
    """Compact metrics snapshot for --write-baseline."""
    return {
        "placeholder": False,
        "version": summary.get("version"),
        "backend_requested": summary.get("backend_requested") or summary.get("backend"),
        "backend_used": summary.get("backend_used"),
        "fallback": summary.get("fallback"),
        "modes": {
            mode: {key: metrics[key] for key in (*REGRESSION_KEYS, "hit_at_1", "recall_at_10", "ndcg_at_10")}
            for mode, metrics in (summary.get("modes") or {}).items()
        },
    }


def run_golden(
    db,
    golden,
    *,
    query_embedder: QueryEmbedder | None = None,
    limit: int = DEFAULT_LIMIT,
    modes: Sequence[str] = ("hybrid",),
    now: datetime | None = None,
    backend_requested: str | None = None,
    backend_used: str | None = None,
    fallback: bool = False,
) -> dict:
    questions = golden.get("questions", []) if isinstance(golden, dict) else list(golden)
    requested = tuple(modes)
    unknown = set(requested) - set(MODES)
    if unknown:
        raise ValueError(f"unknown eval modes: {sorted(unknown)}")
    per_question = [
        _evaluate_question(db, entry, query_embedder, limit, requested, now)
        for entry in questions
    ]
    mode_metrics = {
        mode: _metrics_for_mode(per_question, mode)
        for mode in requested
    }
    hybrid = mode_metrics.get("hybrid") or next(iter(mode_metrics.values()), {})
    summary = {
        "overall_hit_at_5": hybrid.get("hit_at_5", 0.0),
        "per_question": per_question,
        "modes": mode_metrics,
        "backend_requested": backend_requested,
        "backend_used": backend_used,
        "fallback": bool(fallback),
        "version": golden.get("version") if isinstance(golden, dict) else None,
    }
    return summary


def _metrics_for_mode(per_question: Sequence[dict], mode: str) -> dict:
    ranks = [outcome["modes"][mode]["rank"] for outcome in per_question]
    retrieved = [outcome["modes"][mode]["results"] for outcome in per_question]
    labels = [outcome["relevant"] for outcome in per_question]
    metrics = aggregate_metrics(ranks, retrieved, labels)
    metrics["by_category"] = _breakdown(per_question, mode, "category")
    metrics["by_source"] = _breakdown_by_source(per_question, mode)
    return metrics


def _breakdown(per_question: Sequence[dict], mode: str, key: str) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for outcome in per_question:
        groups[str(outcome.get(key) or "unknown")].append(outcome)
    return {
        name: aggregate_metrics(
            [row["modes"][mode]["rank"] for row in rows],
            [row["modes"][mode]["results"] for row in rows],
            [row["relevant"] for row in rows],
        )
        for name, rows in groups.items()
    }


def _breakdown_by_source(per_question: Sequence[dict], mode: str) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for outcome in per_question:
        sources = {label["source"] for label in outcome["relevant"]}
        for source in sources or {"unknown"}:
            groups[source].append(outcome)
    return {
        name: aggregate_metrics(
            [row["modes"][mode]["rank"] for row in rows],
            [row["modes"][mode]["results"] for row in rows],
            [row["relevant"] for row in rows],
        )
        for name, rows in groups.items()
    }


def _evaluate_question(
    db,
    entry: dict,
    query_embedder: QueryEmbedder | None,
    limit: int,
    modes: Sequence[str],
    now: datetime | None,
) -> dict:
    question = entry["question"]
    labels = relevant_labels(entry)
    embedding = query_embedder(question) if query_embedder is not None else None
    mode_outcomes = {}
    for mode in modes:
        results = _search(db, question, embedding, entry.get("scopes"), mode, limit, now)
        rank, matched = first_relevant_match(labels, results)
        mode_outcomes[mode] = {
            "hit": rank is not None and rank <= 5,
            "rank": rank,
            "matched": matched,
            "results": [
                {"source": row["source"], "doc_key": row["doc_key"], "score": row["score"]}
                for row in results[:10]
            ],
        }
    primary = mode_outcomes.get("hybrid") or next(iter(mode_outcomes.values()))
    return {
        "id": entry.get("id"),
        "question": question,
        "category": entry.get("category"),
        "relevant": labels,
        "hit": primary["hit"],
        "matched": primary["matched"],
        "results": primary["results"],
        "modes": mode_outcomes,
    }


def _search(db, question, embedding, scopes, mode, limit, now):
    if mode == "keyword":
        return hybrid_search(
            db,
            question,
            query_embedding=None,
            scopes=scopes,
            limit=limit,
            now=now,
            with_neighbors=False,
            legs=("fts",),
            apply_recency=False,
            apply_source_cap=False,
        )
    if mode == "vector":
        return hybrid_search(
            db,
            question,
            query_embedding=embedding,
            scopes=scopes,
            limit=limit,
            now=now,
            with_neighbors=False,
            legs=("vector",),
            apply_recency=False,
            apply_source_cap=False,
        )
    return hybrid_search(
        db,
        question,
        query_embedding=embedding,
        scopes=scopes,
        limit=limit,
        now=now,
        with_neighbors=False,
    )


def validate_golden_set(golden: dict) -> list[str]:
    errors = []
    if golden.get("draft") is not True:
        errors.append("draft must be true until a human reviews the set")
    if not golden.get("version"):
        errors.append("version field is required")
    questions = golden.get("questions") or []
    if len(questions) < 100:
        errors.append(f"need >= 100 questions, have {len(questions)}")
    ids = []
    categories = set()
    sources = set()
    for entry in questions:
        qid = entry.get("id")
        if not qid:
            errors.append(f"missing id: {entry.get('question', '')[:48]}")
        else:
            ids.append(qid)
        if not (entry.get("question") or "").strip():
            errors.append(f"{qid}: empty question")
        category = entry.get("category")
        if category not in CATEGORIES:
            errors.append(f"{qid}: bad category {category!r}")
        else:
            categories.add(category)
        labels = relevant_labels(entry)
        if not labels:
            errors.append(f"{qid}: no relevant labels")
        for want in labels:
            if want["source"] not in SOURCES:
                errors.append(f"{qid}: bad source {want['source']!r}")
            else:
                sources.add(want["source"])
            try:
                re.compile(want["doc_key_pattern"])
            except re.error as exc:
                errors.append(f"{qid}: invalid doc_key_pattern ({exc})")
            if want["grade"] not in GRADES:
                errors.append(f"{qid}: bad grade {want['grade']!r}")
        if "scopes" in entry and not set(entry["scopes"]) <= set(SCOPES):
            errors.append(f"{qid}: bad scopes {entry.get('scopes')!r}")
    dupes = {qid for qid in ids if ids.count(qid) > 1}
    if dupes:
        errors.append(f"duplicate ids: {sorted(dupes)}")
    missing_cats = set(CATEGORIES) - categories
    if missing_cats:
        errors.append(f"missing categories: {sorted(missing_cats)}")
    missing_sources = set(SOURCES) - sources
    if missing_sources:
        errors.append(f"missing sources: {sorted(missing_sources)}")
    return errors


# ── CLI against the production DB ────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Golden-set retrieval eval: hit@1/5, recall@10, MRR, nDCG@10 "
            "against the production knowledge table."
        )
    )
    parser.add_argument(
        "golden_path",
        nargs="?",
        type=Path,
        default=DEFAULT_GOLDEN_PATH,
        help="path to golden_set.json (default: the shipped set)",
    )
    parser.add_argument(
        "--backend",
        choices=("local", "nvidia"),
        default=None,
        help="query embedding backend; nvidia uses embedding_v2 and falls back to local bge",
    )
    parser.add_argument(
        "--mode",
        choices=(*MODES, "all"),
        default="all",
        help="retrieval mode to score (default: all)",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="compare metrics to this baseline JSON; exit 1 on a regression",
    )
    parser.add_argument(
        "--max-drop",
        type=float,
        default=DEFAULT_MAX_DROP,
        help="max allowed drop on hit@5 and MRR per mode (default: 0.03)",
    )
    parser.add_argument(
        "--write-baseline",
        type=Path,
        default=None,
        help="write a compact metrics baseline to PATH",
    )
    parser.add_argument(
        "--write-results",
        type=Path,
        default=None,
        help="write the full JSON report to PATH (directory -> dated file)",
    )
    parser.add_argument(
        "--strict-backend",
        action="store_true",
        help="exit 2 when the requested nvidia backend falls back to local",
    )
    args = parser.parse_args(argv)
    if args.backend:
        os.environ.update({BACKEND_ENV: args.backend})
    golden = json.loads(args.golden_path.read_text(encoding="utf-8"))
    if isinstance(golden, dict) and golden.get("draft"):
        print(
            "warning: golden set is marked draft:true — curate before trusting the gate",
            file=sys.stderr,
        )
    db = _production_db()
    tracker = _load_query_embedder(db)
    requested_modes = MODES if args.mode == "all" else (args.mode,)
    from knowledge.embed import embed_backend
    requested_backend = args.backend or embed_backend()
    summary = run_golden(
        db,
        golden,
        query_embedder=tracker,
        modes=requested_modes,
        backend_requested=requested_backend,
        backend_used="fts",
        fallback=False,
    )
    # Keyword args are evaluated before run_golden, so read the tracker after
    # it has actually embedded. Ranking is unchanged.
    if tracker is not None:
        summary["backend_used"] = tracker.backend_used
        summary["fallback"] = bool(tracker.fallback)
    summary["backend"] = requested_backend
    summary["hit_at_k"] = summary["overall_hit_at_5"]
    if args.strict_backend and summary["fallback"]:
        print(
            f"strict-backend: requested {summary['backend_requested']} "
            f"fell back to {summary['backend_used']}",
            file=sys.stderr,
        )
        return 2
    _print_table(summary, file=sys.stderr)
    if args.write_baseline:
        args.write_baseline.write_text(
            json.dumps(baseline_payload(summary), indent=2) + "\n",
            encoding="utf-8",
        )
    results_path = _resolve_results_path(args.write_results)
    if results_path:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {results_path}", file=sys.stderr)
    json.dump(summary, sys.stdout, indent=2)
    print()
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        regressions = compare_baseline(summary, baseline, max_drop=args.max_drop)
        if regressions:
            print("regression:", file=sys.stderr)
            for line in regressions:
                print(f"  {line}", file=sys.stderr)
            return 1
    return 0


def _resolve_results_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    if path.exists() and path.is_dir() or str(path).endswith("/"):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
        return path / f"eval_golden-{stamp}.json"
    return path


def _production_db():
    os.environ.setdefault("RADON_DB_NO_REPLICA", "1")
    from dotenv import load_dotenv

    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env")
    scripts_dir = str(project_root / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from db.client import get_db

    return get_db()


class _TrackingEmbedder:
    """Wraps the CLI embedder so the report records the vector that ran."""

    def __init__(self, embed_one: QueryEmbedder, requested: str):
        self._embed_one = embed_one
        self.requested = requested
        self.backend_used = "fts"
        self.fallback = False

    def __call__(self, text: str) -> Sequence[float]:
        vector = self._embed_one(text)
        used = _backend_from_dim(len(vector))
        self.backend_used = used
        if self.requested == "nvidia" and used == "local":
            self.fallback = True
        return vector


def _backend_from_dim(dim: int) -> str:
    if dim == EMBEDDING_DIM_V2:
        return "nvidia"
    if dim == EMBEDDING_DIM:
        return "local"
    return f"dim{dim}"


def _load_query_embedder(db=None) -> _TrackingEmbedder | None:
    from knowledge.embed import embed_backend, get_embedder, resolve_query_vector, v2_coverage_ready

    local = None
    try:
        local = get_embedder()
    except Exception as exc:  # missing module/deps/model — FTS-only is a valid mode
        print(f"embedder unavailable ({exc}); running FTS-only", file=sys.stderr)
    requested = embed_backend()
    if local is None and requested != "nvidia":
        return None

    def _v2_ready() -> bool:
        if db is None:
            return True
        try:
            return v2_coverage_ready(db)
        except Exception as exc:
            print(f"embedding_v2 coverage check failed ({exc}); local bge", file=sys.stderr)
            return False

    def embed_one(text: str):
        vector = resolve_query_vector(
            text,
            local_embedder=local,
            coverage_ready=_v2_ready if db is not None else None,
            on_nvidia_error=lambda exc: print(
                f"nvidia embed failed ({exc}); falling back", file=sys.stderr
            ),
        )
        if not vector:
            raise RuntimeError("embedding unavailable")
        return vector

    return _TrackingEmbedder(embed_one, requested)


def _as_vector(value) -> list[float]:
    """Accept a plain vector or a batch-of-one (list/generator of vectors)."""
    values = list(value)
    if len(values) == 1 and not isinstance(values[0], (int, float)):
        values = list(values[0])
    return [float(component) for component in values]


def _print_table(summary: dict, *, file) -> None:
    per_question = summary["per_question"]
    primary = "hybrid" if "hybrid" in (summary.get("modes") or {}) else next(iter(summary.get("modes") or {"hybrid": {}}))
    for outcome in per_question:
        mode_out = outcome["modes"][primary]
        mark = "HIT " if mode_out["hit"] else "MISS"
        top = ", ".join(
            f"{row['source']}:{row['doc_key']}" for row in mode_out["results"][:3]
        )
        print(f"{mark}  {outcome['question'][:64]:<64}  {top}", file=file)
    for mode, metrics in (summary.get("modes") or {}).items():
        print(
            f"{mode} hit@5={metrics['hit_at_5']:.3f} mrr={metrics['mrr']:.3f} "
            f"ndcg@10={metrics['ndcg_at_10']:.3f} backend_used={summary.get('backend_used')} "
            f"fallback={summary.get('fallback')}",
            file=file,
        )
    hits = sum(1 for outcome in per_question if outcome["hit"])
    print(
        f"overall hit@5: {summary['overall_hit_at_5']:.2f} ({hits}/{len(per_question)})",
        file=file,
    )


if __name__ == "__main__":
    sys.exit(main())

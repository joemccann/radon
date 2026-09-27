"""Hybrid retrieval over the `knowledge` table.

Two ranked legs — FTS5 BM25 (exact tokens) and vector similarity
(paraphrase) — fused with weighted reciprocal rank fusion (k=60). Recency is
applied as a multiplicative exponential-decay factor on the fused score
rather than a third rank list: rank positions cannot express per-source
half-lives ("newsfeed answers expire, a structure definition doesn't"), a
decay factor can. The factor is floored so an old incident stays findable by
its exact error string.

Post-fusion: dedup to the best chunk per (source, doc_key), cap results per
source so a flooded source can't crowd out the rest, then attach adjacent
chunks (neighbor expansion) to each winner.

Scope/source filters are pushed into both candidate legs (not applied after
the fixed candidate pool) so a flooded unmatching source cannot crowd a
scoped query out of the pool entirely.

The 384-d vector leg uses server-side vector_top_k over the ANN index from
migration 0028. The 2048-d leg is an exact cosine scan of embedding_v2
(migration 0089 drops that DiskANN index). Callers may inject any
`vector_search(db, embedding, pool, scopes, sources)` callable (tests use a
numpy cosine scorer). Fusion/decay/list helpers are pure Python — importable
and unit-testable without a DB.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import re
from typing import Callable, Iterable, Mapping, Sequence

from knowledge.embed import EMBEDDING_DIM_V2

RRF_K = 60
LEG_WEIGHTS = {"fts": 1.0, "vector": 1.0}
RECENCY_HALF_LIFE_DAYS: dict[str, float] = {"newsfeed": 7.0, "incidents": 14.0}
MIN_RECENCY_FACTOR = 0.05
CANDIDATE_POOL = 50
VECTOR_FILTER_OVERFETCH = 4  # ANN top_k widening when filters discard candidates
MAX_PER_SOURCE = 3
NEIGHBOR_SPAN = 1

_ROW_COLUMNS = (
    "id", "source", "scope", "doc_key", "chunk_ix", "title", "summary",
    "content", "metadata", "created_at", "last_activity_at",
)

_FTS_SQL_TEMPLATE = (
    "SELECT knowledge_fts.rowid FROM knowledge_fts "
    "JOIN knowledge ON knowledge.id = knowledge_fts.rowid "
    "WHERE {where} ORDER BY bm25(knowledge_fts), knowledge_fts.rowid LIMIT ?"
)

_VECTOR_INDEXES = ("idx_knowledge_embedding",)

_V2_EXACT_SQL = (
    "SELECT id FROM knowledge WHERE {where} "
    "ORDER BY vector_distance_cos(embedding_v2, vector32(?)) LIMIT ?"
)

_NEIGHBOR_SQL = (
    "SELECT chunk_ix, content FROM knowledge "
    "WHERE source = ? AND doc_key = ? AND chunk_ix BETWEEN ? AND ? AND chunk_ix != ? "
    "ORDER BY chunk_ix"
)


# ── pure fusion / decay helpers (no DB) ──────────────────────────────


def rrf_fuse(
    ranked_lists: Mapping[str, Sequence],
    *,
    k: int = RRF_K,
    weights: Mapping[str, float] | None = None,
) -> dict:
    """Weighted reciprocal rank fusion: each leg contributes
    weight / (k + rank + 1) per key, best-first lists in, {key: score} out."""
    weights = LEG_WEIGHTS if weights is None else weights
    scores: dict = defaultdict(float)
    for leg, keys in ranked_lists.items():
        weight = weights.get(leg, 1.0)
        for rank, key in enumerate(keys):
            scores[key] += weight / (k + rank + 1)
    return dict(scores)


def recency_factor(source: str, last_activity_at: str, now: datetime) -> float:
    half_life_days = RECENCY_HALF_LIFE_DAYS.get(source)
    if half_life_days is None:
        return 1.0
    age_days = max((now - _parse_iso(last_activity_at)).total_seconds() / 86400.0, 0.0)
    return max(0.5 ** (age_days / half_life_days), MIN_RECENCY_FACTOR)


def dedup_best_chunk(scored_rows: Iterable[tuple[float, dict]]) -> list[tuple[float, dict]]:
    """Keep the first (= best, input is best-first) chunk per (source, doc_key)."""
    seen: set[tuple[str, str]] = set()
    kept = []
    for score, row in scored_rows:
        key = (row["source"], row["doc_key"])
        if key in seen:
            continue
        seen.add(key)
        kept.append((score, row))
    return kept


def cap_per_source(
    scored_rows: Iterable[tuple[float, dict]],
    max_per_source: int = MAX_PER_SOURCE,
) -> list[tuple[float, dict]]:
    counts: dict[str, int] = defaultdict(int)
    kept = []
    for score, row in scored_rows:
        if counts[row["source"]] >= max_per_source:
            continue
        counts[row["source"]] += 1
        kept.append((score, row))
    return kept


def fts_match_expression(query: str) -> str:
    """Neutralize FTS5 query syntax: alphanumeric tokens only, quoted, OR'd
    so BM25 favors docs matching more of the query."""
    tokens = re.findall(r"[A-Za-z0-9_]+", query)
    return " OR ".join(f'"{token}"' for token in tokens)


# ── hybrid search ────────────────────────────────────────────────────


_VALID_LEGS = frozenset({"fts", "vector"})


def hybrid_search(
    db,
    query: str,
    *,
    query_embedding: Sequence[float] | None = None,
    scopes: Sequence[str] | None = None,
    sources: Sequence[str] | None = None,
    limit: int = 10,
    now: datetime | None = None,
    vector_search: Callable | None = None,
    with_neighbors: bool = True,
    rerank: Callable[[list[tuple[float, dict]]], Sequence[tuple[float, dict]]] | None = None,
    legs: Sequence[str] | None = None,
    apply_recency: bool = True,
    apply_source_cap: bool = True,
) -> list[dict]:
    """Returns result rows best-first, each a dict of `knowledge` columns plus
    `score` and — unless `with_neighbors=False` — `neighbors` (adjacent chunks
    of the same document; one extra SELECT per winner, so callers that discard
    neighbors opt out).

    `rerank` reorders the FULL deduped candidate pool — (score, row) pairs
    best-first — before the per-source cap and the limit cut, so a caller can
    promote a doc class the fused ranking buries (prior-evals: thesis docs
    behind dozens of fill rows) without overfetching or extra statements.
    Neighbor SELECTs still run only for the final winners.

    Opt-in scoring hooks (defaults keep production ranking identical):
    `legs=None` runs both FTS and vector; `legs=("fts",)` / `("vector",)`
    isolate a single leg. `apply_recency=False` and `apply_source_cap=False`
    skip post-fusion decay and the per-source cap so a golden eval can score
    each raw leg the way the per-leg diagnostic does.
    """
    now = now or datetime.now(timezone.utc)
    active = _normalize_legs(legs)
    fts_ids = _fts_leg(db, query, CANDIDATE_POOL, scopes, sources) if "fts" in active else []
    vector_ids: list = []
    if "vector" in active and query_embedding is not None:
        search = vector_search or _vector_top_k_search
        vector_ids = list(search(db, query_embedding, CANDIDATE_POOL, scopes, sources))

    rows_by_id = _fetch_candidate_rows(db, fts_ids + vector_ids, scopes, sources)
    if len(active) == 1:
        # Single-leg scoring: preserve that leg's order (diag.py `ordered`),
        # then optional decay/cap. Fusion of one list would re-score by RRF.
        only = next(iter(active))
        ordered_ids = fts_ids if only == "fts" else vector_ids
        scored = []
        for rank, row_id in enumerate(ordered_ids):
            row = rows_by_id.get(row_id)
            if row is None:
                continue
            score = 1.0 / (RRF_K + rank + 1)
            if apply_recency:
                score *= recency_factor(row["source"], row["last_activity_at"], now)
            scored.append((score, row))
        if apply_recency:
            scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    else:
        fused = rrf_fuse(
            {
                "fts": [i for i in fts_ids if i in rows_by_id],
                "vector": [i for i in vector_ids if i in rows_by_id],
            }
        )
        scored = []
        for row_id, base_score in fused.items():
            row = rows_by_id[row_id]
            decayed = (
                base_score * recency_factor(row["source"], row["last_activity_at"], now)
                if apply_recency
                else base_score
            )
            scored.append((decayed, row))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))

    pool = dedup_best_chunk(scored)
    if rerank is not None:
        pool = list(rerank(pool))
    winners = (cap_per_source(pool) if apply_source_cap else list(pool))[:limit]
    if not with_neighbors:
        return [_scored_result(score, row) for score, row in winners]
    return [_result_with_neighbors(db, score, row) for score, row in winners]


def _normalize_legs(legs: Sequence[str] | None) -> frozenset[str]:
    if legs is None:
        return _VALID_LEGS
    unknown = set(legs) - _VALID_LEGS
    if unknown:
        raise ValueError(f"unknown retrieval legs: {sorted(unknown)}")
    if not legs:
        raise ValueError("legs must be non-empty")
    return frozenset(legs)


def _fts_leg(
    db,
    query: str,
    pool: int,
    scopes: Sequence[str] | None,
    sources: Sequence[str] | None,
) -> list[int]:
    match_expression = fts_match_expression(query)
    if not match_expression:
        return []
    filter_clauses, filter_args = _knowledge_filters(scopes, sources)
    sql = _FTS_SQL_TEMPLATE.format(
        where=" AND ".join(["knowledge_fts MATCH ?", *filter_clauses])
    )
    rows = db.execute(sql, (match_expression, *filter_args, pool)).fetchall()
    return [row[0] for row in rows]


def _vector_top_k_search(
    db,
    query_embedding: Sequence[float],
    pool: int,
    scopes: Sequence[str] | None = None,
    sources: Sequence[str] | None = None,
    index_name: str | None = None,
) -> list[int]:
    embedding_json = json.dumps(list(query_embedding))
    if len(query_embedding) == EMBEDDING_DIM_V2:
        # Legacy callers passed the dropped DiskANN name. Exact scan does not read it.
        if index_name not in (None, "idx_knowledge_embedding_v2"):
            raise ValueError("unknown knowledge vector index")
        return _exact_cosine_v2_ids(db, embedding_json, pool, scopes, sources)
    name = index_name or "idx_knowledge_embedding"
    if name not in _VECTOR_INDEXES:
        raise ValueError("unknown knowledge vector index")
    filter_clauses, filter_args = _knowledge_filters(scopes, sources)
    head = "SELECT t.id FROM vector_top_k('" + name + "', vector32(?), ?) t"
    if not filter_clauses:
        rows = db.execute(head, (embedding_json, pool)).fetchall()
        return [row[0] for row in rows]
    sql = head + " JOIN knowledge ON knowledge.id = t.id WHERE {where} LIMIT ?"
    sql = sql.format(where=" AND ".join(filter_clauses))
    top_k = pool * VECTOR_FILTER_OVERFETCH
    rows = db.execute(sql, (embedding_json, top_k, *filter_args, pool)).fetchall()
    return [row[0] for row in rows]


def _exact_cosine_v2_ids(
    db,
    embedding_json: str,
    pool: int,
    scopes: Sequence[str] | None,
    sources: Sequence[str] | None,
) -> list[int]:
    """Nearest-first ids for embedding_v2.

    vector_distance_cos is cosine distance (0 = identical). Ascending order
    is the same nearest-first rank vector_top_k fed into RRF. Similarity is
    1 - distance and ranks the same rows, so the hybrid score stays the RRF
    weight of that rank.
    """
    filter_clauses, filter_args = _knowledge_filters(scopes, sources)
    where = " AND ".join(["embedding_v2 IS NOT NULL", *filter_clauses])
    sql = _V2_EXACT_SQL.format(where=where)
    rows = db.execute(sql, (*filter_args, embedding_json, pool)).fetchall()
    return [row[0] for row in rows]


def _knowledge_filters(
    scopes: Sequence[str] | None,
    sources: Sequence[str] | None,
) -> tuple[list[str], list]:
    clauses: list[str] = []
    args: list = []
    for column, values in (("scope", scopes), ("source", sources)):
        if values:
            clauses.append(f"knowledge.{column} IN ({', '.join('?' for _ in values)})")
            args.extend(values)
    return clauses, args


def _fetch_candidate_rows(
    db,
    candidate_ids: Sequence[int],
    scopes: Sequence[str] | None,
    sources: Sequence[str] | None,
) -> dict[int, dict]:
    ids = list(dict.fromkeys(candidate_ids))
    if not ids:
        return {}
    filter_clauses, filter_args = _knowledge_filters(scopes, sources)
    clauses = [f"id IN ({', '.join('?' for _ in ids)})", *filter_clauses]
    sql = f"SELECT {', '.join(_ROW_COLUMNS)} FROM knowledge WHERE {' AND '.join(clauses)}"
    rows = db.execute(sql, (*ids, *filter_args)).fetchall()
    return {row[0]: dict(zip(_ROW_COLUMNS, row)) for row in rows}


def _scored_result(score: float, row: dict) -> dict:
    result = dict(row)
    result["metadata"] = json.loads(row["metadata"]) if row["metadata"] else None
    result["score"] = score
    return result


def _result_with_neighbors(db, score: float, row: dict) -> dict:
    neighbor_rows = db.execute(
        _NEIGHBOR_SQL,
        (
            row["source"], row["doc_key"],
            row["chunk_ix"] - NEIGHBOR_SPAN, row["chunk_ix"] + NEIGHBOR_SPAN,
            row["chunk_ix"],
        ),
    ).fetchall()
    result = _scored_result(score, row)
    result["neighbors"] = [
        {"chunk_ix": chunk_ix, "content": content} for chunk_ix, content in neighbor_rows
    ]
    return result


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed

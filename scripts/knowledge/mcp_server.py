"""Read-only stdio MCP server over the Radon knowledge corpus (Phase 3).

Exposes the ~5k-doc corpus (sources journal / evals / docs / newsfeed /
incidents; scopes trading / research / ops) to Claude Code as LLM-free
retrieval primitives: the agent orchestrates, these tools just retrieve.
Narrow structured I/O with bounded strings; invalid input comes back as
{"error": ...} instead of raising through the transport.

Tool logic lives in `_kb_*_impl(db, ...)` functions so tests can run them
against a local :memory: database. The Turso connection and the local query
embedder are acquired lazily on first tool call — importing this module has
no side effects. This module contains SELECTs only; a pin test greps the
source for SQL write keywords.

Registered project-scoped in .mcp.json (env RADON_DB_NO_REPLICA=1):
    .venv/bin/python scripts/knowledge/mcp_server.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Annotated, Callable, Sequence

from mcp.server.fastmcp import FastMCP
from pydantic import Field

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from knowledge.retrieve import hybrid_search  # noqa: E402

KNOWN_SOURCES = ("journal", "evals", "docs", "newsfeed", "incidents")
KNOWN_SCOPES = ("trading", "research", "ops")
PRIOR_EVAL_SOURCES = ("journal", "evals")

QUERY_MAX_CHARS = 500
CONTENT_MAX_CHARS = 1200
SUMMARY_MAX_CHARS = 500
TITLE_MAX_CHARS = 200
DOC_KEY_MAX_CHARS = 160
SEARCH_LIMIT_DEFAULT = 6
SEARCH_LIMIT_MAX = 10
RECENT_LIMIT_DEFAULT = 10
RECENT_LIMIT_MAX = 20

TICKER_RE = re.compile(r"^[A-Z0-9]{1,6}$")

QueryEmbedder = Callable[[str], Sequence[float]]

_RECENT_COLUMNS = (
    "source", "scope", "doc_key", "chunk_ix", "title", "summary",
    "content", "last_activity_at",
)

_RECENT_SQL_TEMPLATE = (
    f"SELECT {', '.join(_RECENT_COLUMNS)} FROM knowledge{{where}} "
    "ORDER BY last_activity_at DESC, id DESC LIMIT ?"
)


# ── pure tool logic (tests call these with a local DB) ───────────────


def _kb_search_impl(
    db,
    query_embedder: QueryEmbedder | None,
    query: str,
    *,
    scopes: Sequence[str] | None = None,
    sources: Sequence[str] | None = None,
    limit: int = SEARCH_LIMIT_DEFAULT,
) -> dict:
    if not isinstance(query, str) or not query.strip() or len(query) > QUERY_MAX_CHARS:
        return {"error": f"query must be a non-blank string of at most {QUERY_MAX_CHARS} characters"}
    for field, values, known in (
        ("scope", scopes, KNOWN_SCOPES),
        ("source", sources, KNOWN_SOURCES),
    ):
        error = _unknown_values_error(field, values, known)
        if error:
            return {"error": error}
    embedding = _embed_query(query_embedder, query)
    rows = hybrid_search(
        db,
        query.strip(),
        query_embedding=embedding,
        scopes=list(scopes) if scopes else None,
        sources=list(sources) if sources else None,
        limit=_clamped_limit(limit, SEARCH_LIMIT_MAX),
        with_neighbors=False,  # _bounded_row never returns neighbors
    )
    return {
        "retrieval": "hybrid" if embedding is not None else "fts-only",
        "results": [{**_bounded_row(row), "score": row["score"]} for row in rows],
    }


def _kb_recent_impl(
    db,
    source: str | None = None,
    limit: int = RECENT_LIMIT_DEFAULT,
) -> dict:
    error = _unknown_values_error("source", [source] if source else None, KNOWN_SOURCES)
    if error:
        return {"error": error}
    return _latest_rows(db, source_equals=source, limit=limit)


def _kb_prior_evals_impl(db, query_embedder: QueryEmbedder | None, ticker: str) -> dict:
    upper = ticker.strip().upper() if isinstance(ticker, str) else ""
    if not TICKER_RE.fullmatch(upper):
        return {"error": "ticker must be 1-6 characters of A-Z / 0-9 (e.g. EWY, SPY)"}
    result = _kb_search_impl(db, query_embedder, upper, sources=PRIOR_EVAL_SOURCES)
    if "error" in result:
        return result
    return {"ticker": upper, **result}


def _kb_incidents_impl(
    db,
    service: str | None = None,
    limit: int = RECENT_LIMIT_DEFAULT,
) -> dict:
    return _latest_rows(
        db, source_equals="incidents", doc_key_contains=service, limit=limit
    )


def _latest_rows(
    db,
    *,
    source_equals: str | None = None,
    doc_key_contains: str | None = None,
    limit: int,
) -> dict:
    clauses: list[str] = []
    args: list = []
    if source_equals:
        clauses.append("source = ?")
        args.append(source_equals)
    if doc_key_contains:
        clauses.append("instr(doc_key, ?) > 0")
        args.append(doc_key_contains)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = _RECENT_SQL_TEMPLATE.format(where=where)
    rows = db.execute(sql, (*args, _clamped_limit(limit, RECENT_LIMIT_MAX))).fetchall()
    return {"results": [_bounded_row(dict(zip(_RECENT_COLUMNS, row))) for row in rows]}


def _bounded_row(row: dict) -> dict:
    title = row.get("title")
    summary = row.get("summary")
    return {
        "source": row["source"],
        "scope": row["scope"],
        "doc_key": (row["doc_key"] or "")[:DOC_KEY_MAX_CHARS],
        "chunk_ix": row["chunk_ix"],
        "title": title[:TITLE_MAX_CHARS] if title else None,
        "summary": summary[:SUMMARY_MAX_CHARS] if summary else None,
        "content": (row.get("content") or "")[:CONTENT_MAX_CHARS],
        "last_activity_at": row["last_activity_at"],
    }


def _unknown_values_error(
    field: str, values: Sequence[str] | None, known: Sequence[str]
) -> str | None:
    if not values:
        return None
    unknown = [value for value in values if value not in known]
    if not unknown:
        return None
    return (
        f"unknown {field}(s): {', '.join(unknown)}. "
        f"Valid {field}s: {', '.join(known)}"
    )


def _clamped_limit(value, maximum: int) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return 1


def _embed_query(query_embedder: QueryEmbedder | None, query: str) -> list[float] | None:
    if query_embedder is None:
        return None
    try:
        return [float(component) for component in query_embedder(query)]
    except Exception as exc:  # noqa: BLE001 — FTS-only is a valid mode
        print(f"[kb-mcp] query embedding failed ({exc}); FTS-only", file=sys.stderr)
        return None


# ── lazy production DB + query embedder (first tool call only) ───────

_UNSET = object()
_db = None
_query_embedder_cache: object = _UNSET


def _get_db():
    global _db
    if _db is None:
        _db = _connect_production_db()
    return _db


def _connect_production_db():
    os.environ.setdefault("RADON_DB_NO_REPLICA", "1")
    from dotenv import load_dotenv

    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env")
    from db.client import get_db

    return get_db()


def _get_query_embedder() -> QueryEmbedder | None:
    global _query_embedder_cache
    if _query_embedder_cache is _UNSET:
        _query_embedder_cache = _build_query_embedder()
    return _query_embedder_cache  # type: ignore[return-value]


def _build_query_embedder() -> QueryEmbedder | None:
    try:
        from knowledge.embed import get_embedder

        embedder = get_embedder()
    except Exception as exc:  # missing deps/model — FTS-only is a valid mode
        print(f"[kb-mcp] embedder unavailable ({exc}); FTS-only", file=sys.stderr)
        return None
    embed_call = embedder if callable(embedder) else getattr(embedder, "embed", None)
    if embed_call is None:
        return None
    return lambda text: _as_vector(embed_call([text]))


def _as_vector(value) -> list[float]:
    """Accept a plain vector or a batch-of-one (list/generator of vectors)."""
    values = list(value)
    if len(values) == 1 and not isinstance(values[0], (int, float)):
        values = list(values[0])
    return [float(component) for component in values]


# ── MCP tool registrations ───────────────────────────────────────────

mcp = FastMCP(
    "radon-kb",
    instructions=(
        "Read-only retrieval over the Radon knowledge corpus: trade journal, "
        "ticker evals, ops docs, market newsfeed, incident writeups. doc_key "
        "values are stable and citable."
    ),
)


@mcp.tool()
def kb_search(
    query: Annotated[str, Field(description="Free-text question or keywords, 1-500 chars")],
    scopes: Annotated[
        list[str] | None,
        Field(description="Optional scope filter: trading, research, ops"),
    ] = None,
    sources: Annotated[
        list[str] | None,
        Field(description="Optional source filter: journal, evals, docs, newsfeed, incidents"),
    ] = None,
    limit: Annotated[
        int, Field(description=f"Max results, clamped to 1-{SEARCH_LIMIT_MAX}")
    ] = SEARCH_LIMIT_DEFAULT,
) -> dict:
    """Search the Radon knowledge corpus (~5k docs: trade journal entries,
    7-milestone ticker evals, ops docs/runbooks, market newsfeed, incident
    writeups) with hybrid BM25 + vector + recency retrieval. First stop for
    any question about prior trades, methodology, infrastructure, or market
    coverage. Sources: journal, evals, docs, newsfeed, incidents. Scopes:
    trading, research, ops. Result doc_keys are stable and citable; the
    response's `retrieval` field notes hybrid vs fts-only mode."""
    return _kb_search_impl(
        _get_db(), _get_query_embedder(), query,
        scopes=scopes, sources=sources, limit=limit,
    )


@mcp.tool()
def kb_recent(
    source: Annotated[
        str | None,
        Field(description="Optional source: journal, evals, docs, newsfeed, incidents"),
    ] = None,
    limit: Annotated[
        int, Field(description=f"Max rows, clamped to 1-{RECENT_LIMIT_MAX}")
    ] = RECENT_LIMIT_DEFAULT,
) -> dict:
    """The most recent knowledge-corpus rows by last_activity_at, newest
    first, no query needed. Use to see what the corpus ingested lately —
    e.g. the latest journal entries, newsfeed posts, or incident writeups —
    optionally narrowed to one source."""
    return _kb_recent_impl(_get_db(), source=source, limit=limit)


@mcp.tool()
def kb_prior_evals(
    ticker: Annotated[str, Field(description="Ticker symbol, 1-6 chars A-Z/0-9, e.g. EWY")],
) -> dict:
    """Prior trade-journal entries and 7-milestone evaluations for one ticker
    (sources journal + evals only). Use before evaluating or trading a ticker
    to surface past theses, structures, sizing, and outcomes. doc_keys are
    stable and citable."""
    return _kb_prior_evals_impl(_get_db(), _get_query_embedder(), ticker)


@mcp.tool()
def kb_incidents(
    service: Annotated[
        str | None,
        Field(description="Optional service-name filter (doc_key substring), e.g. relay, radon-api"),
    ] = None,
    limit: Annotated[
        int, Field(description=f"Max rows, clamped to 1-{RECENT_LIMIT_MAX}")
    ] = RECENT_LIMIT_DEFAULT,
) -> dict:
    """Incident writeups (outages, degradations, postmortems) from the
    incidents source, newest first. Use when debugging a failure or checking
    whether a signature has been seen before. Optionally filter by service
    name, matched as a substring of the incident doc_key."""
    return _kb_incidents_impl(_get_db(), service=service, limit=limit)


if __name__ == "__main__":
    mcp.run(transport="stdio")

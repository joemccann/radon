"""Connector registry (Phase 1). Each module defines SOURCE, SCOPE and
fetch(db) -> Iterator[KnowledgeDoc] yielding the source's full current corpus
RAW (summary=None, embedding=None); distillation/embedding happen downstream
in ingest.py. File-based connectors ignore db."""
from . import docs, evals, incidents, journal, newsfeed

ALL_SOURCES = {
    module.SOURCE: module
    for module in (journal, evals, docs, newsfeed, incidents)
}

__all__ = ["ALL_SOURCES", "docs", "evals", "incidents", "journal", "newsfeed"]

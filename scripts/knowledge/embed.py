"""Local embeddings for the knowledge base (fastembed / bge-small-en-v1.5).

384d to match migration 0028's F32_BLOB(384). Runs on CPU via onnxruntime so
journal/eval text never transits a third-party embeddings API (plan §Key
decisions). The model (~67 MB ONNX) downloads on first use into
FASTEMBED_CACHE_PATH — set it on hosts where /tmp is ephemeral.

get_embedder() is a lazy singleton and degrades gracefully: it returns None
when RADON_KB_EMBED_DISABLED=1 or fastembed is missing/broken, and ingest
then writes FTS-only rows (embedding NULL).
"""
from __future__ import annotations

import os
import sys
from typing import Callable, Sequence

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384
DISABLE_ENV = "RADON_KB_EMBED_DISABLED"
CONTENT_HEAD_CHARS = 2000

Embedder = Callable[[Sequence[str]], list[list[float]]]

_UNSET = object()
_embedder: object = _UNSET  # Embedder, None (unavailable), or _UNSET


def get_embedder() -> Embedder | None:
    """Return the batch-embedding callable, or None when embeddings are
    disabled or unavailable. Build failures are cached — one loud stderr
    line, not one per batch."""
    global _embedder
    if os.environ.get(DISABLE_ENV) == "1":
        return None
    if _embedder is _UNSET:
        _embedder = _build_embedder()
    return _embedder  # type: ignore[return-value]


def embedding_text(title: str | None, summary: str | None, content: str | None) -> str:
    """The text a knowledge row is embedded from: title + distilled summary,
    falling back to the content head when no summary exists yet."""
    body = summary if summary else (content or "")[:CONTENT_HEAD_CHARS]
    return f"{title}\n{body}".strip() if title else body.strip()


def _import_text_embedding():
    from fastembed import TextEmbedding  # noqa: PLC0415 — deliberate lazy import

    return TextEmbedding


def _build_embedder() -> Embedder | None:
    try:
        text_embedding_class = _import_text_embedding()
        model = text_embedding_class(EMBEDDING_MODEL)
    except Exception as exc:  # noqa: BLE001 — degrade to FTS-only ingest
        print(
            f"[knowledge-embed] embeddings unavailable ({exc}) — "
            "ingest degrades to FTS-only rows",
            file=sys.stderr,
        )
        return None

    def embed_texts(texts: Sequence[str]) -> list[list[float]]:
        return [[float(value) for value in vector] for vector in model.embed(list(texts))]

    return embed_texts

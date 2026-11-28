"""Knowledge embeddings.

The live default is nvidia/nemotron-3-embed-1b (2048d, column embedding_v2).
On 2026-09-25 Joe McCann reversed the local-only decision in
tasks/archive/knowledge-base-plan.md: every knowledge source, including journal,
positions, and P&L, is sent to NVIDIA's hosted embeddings API.
RADON_KB_EMBED_BACKEND=local forces BAAI/bge-small-en-v1.5 (384d).

Local fastembed is the automatic fallback on NVIDIA 429s, 5xx, timeouts,
and any other embed error. It is also the query path while any
embedding_v2 value is still NULL, so a partial backfill cannot answer from
a subset of the 2048-d column and miss the rest of the corpus. That NULL
check is cached for 60 seconds.

Ingest dual-writes both vectors by default so the 384 index stays current
for that fallback. The 2048-d path has no ANN index (exact cosine scan;
migration 0089 drops idx_knowledge_embedding_v2). RADON_KB_EMBED_DUAL_WRITE=0
turns the 2048 write off.

nvidia/llama-3.2-nv-embedqa-1b-v1 is not used: this account gets 404
Function not found, and the model caps input at 512 tokens.

get_embedder() returns the private gateway callable; no model is loaded in consumers. It returns None when
RADON_KB_EMBED_DISABLED=1 or fastembed is missing/broken, and ingest then
writes FTS-only rows (embedding NULL). The ONNX model (~67 MB) downloads
once into FASTEMBED_CACHE_PATH, defaulted here to ~/.cache/fastembed.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Sequence

from model_gateway.client import request as gateway_request
from model_gateway.embed_rules import (  # noqa: F401
    BACKEND_ENV,
    CONTENT_HEAD_CHARS,
    DISABLE_ENV,
    DUAL_WRITE_ENV,
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_DIM,
    EMBEDDING_DIM_V2,
    EMBEDDING_MODEL,
    EMBEDDING_MODEL_V2,
    NVIDIA_EMBED_BATCH,
    VECTOR_INDEX_V1,
    Embedder,
    dual_write_enabled,
    embed_backend,
    embedding_text,
    v2_coverage_ready,
)
from model_gateway.embed_rules import (
    resolve_query_vector as _decide_query_vector,
)

logger = logging.getLogger(__name__)

_UNSET = object()
_embedder: object = _UNSET  # Embedder, None (unavailable), or _UNSET
_embedder_build_lock = threading.Lock()


def get_embedder() -> Embedder | None:
    """Return the batch-embedding callable, or None when embeddings are
    disabled or unavailable. Build failures are cached — one loud stderr
    line, not one per batch. Double-checked lock so concurrent cold starts
    (asyncio.to_thread workers) build the ~67 MB model exactly once."""
    global _embedder
    if os.environ.get(DISABLE_ENV) == "1":
        return None
    if _embedder is _UNSET:
        with _embedder_build_lock:
            if _embedder is _UNSET:
                _embedder = _build_embedder()
    return _embedder  # type: ignore[return-value]


def _build_embedder() -> Embedder | None:
    # Native model loading occurs once inside the gateway, not in each consumer.
    return _gateway_local_embed


def embed_passages(texts: Sequence[str], *, post=None) -> list[list[float]]:
    return _nvidia_embed(texts, input_type="passage", post=post)


def embed_query(texts: Sequence[str], *, post=None) -> list[list[float]]:
    return _nvidia_embed(texts, input_type="query", post=post)


def _resolve_with_transport(
    text: str,
    *,
    local_embedder: Embedder | None,
    post=None,
    on_nvidia_error=None,
    on_local_error=None,
    coverage_ready: Callable[[], bool] | None = None,
) -> list[float] | None:
    """NVIDIA 2048 when the backend says so and v2 coverage is complete.

    Incomplete coverage skips NVIDIA and uses local 384. NVIDIA errors
    (429, 5xx, timeouts) also fall through to local 384, else None (FTS).
    """
    return _decide_query_vector(
        text,
        local_embedder=local_embedder,
        query=lambda texts: embed_query(texts, post=post),
        on_nvidia_error=on_nvidia_error,
        on_local_error=on_local_error,
        coverage_ready=coverage_ready,
    )


resolve_query_vector = _resolve_with_transport


def _nvidia_embed(texts: Sequence[str], *, input_type: str, post=None) -> list[list[float]]:
    if not texts:
        return []
    vectors = []
    items = list(texts)
    for offset in range(0, len(items), NVIDIA_EMBED_BATCH):
        batch = items[offset:offset + NVIDIA_EMBED_BATCH]
        response = gateway_request("embed", {"texts": batch, "dimensions": EMBEDDING_DIM_V2,
            "input_type": input_type}, timeout_seconds=120)
        vectors.extend(_validate_gateway_vectors(response.get("vectors"), len(batch), EMBEDDING_DIM_V2))
    return vectors


def _validate_gateway_vectors(vectors, count, dimensions):
    import math
    if not isinstance(vectors, list) or len(vectors) != count:
        raise RuntimeError("gateway embedding batch length mismatch")
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != dimensions:
            raise RuntimeError("gateway embedding dimension mismatch")
        if any(type(value) not in (float, int) or not math.isfinite(value) for value in vector):
            raise RuntimeError("gateway embedding contains non-finite values")
    return vectors


def _gateway_local_embed(texts: Sequence[str]) -> list[list[float]]:
    if not texts:
        return []
    vectors = []
    items = list(texts)
    for offset in range(0, len(items), EMBEDDING_BATCH_SIZE):
        batch = items[offset:offset + EMBEDDING_BATCH_SIZE]
        response = gateway_request("embed", {"texts": batch, "dimensions": EMBEDDING_DIM,
            "input_type": "passage"}, timeout_seconds=120)
        vectors.extend(_validate_gateway_vectors(response.get("vectors"), len(batch), EMBEDDING_DIM))
    return vectors

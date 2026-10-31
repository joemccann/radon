"""Knowledge embeddings.

The live default is nvidia/nemotron-3-embed-1b (2048d, column embedding_v2).
On 2026-09-25 Joe McCann reversed the local-only decision in
tasks/knowledge-base-plan.md: every knowledge source, including journal,
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
import sys
import threading
import time
from typing import Callable, Sequence

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384
EMBEDDING_MODEL_V2 = "nvidia/nemotron-3-embed-1b"
EMBEDDING_DIM_V2 = 2048
VECTOR_INDEX_V1 = "idx_knowledge_embedding"
DISABLE_ENV = "RADON_KB_EMBED_DISABLED"
BACKEND_ENV = "RADON_KB_EMBED_BACKEND"
DUAL_WRITE_ENV = "RADON_KB_EMBED_DUAL_WRITE"
from model_gateway.client import request as gateway_request
NVIDIA_EMBED_BATCH = 16
CONTENT_HEAD_CHARS = 2000
# fastembed defaults to 256: a 200-chunk catch-up peaked at 4.3 GiB RSS.
# Keep the model inference working set bounded independently of write batches.
EMBEDDING_BATCH_SIZE = 16

Embedder = Callable[[Sequence[str]], list[list[float]]]

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


def embedding_text(title: str | None, summary: str | None, content: str | None) -> str:
    """The text a knowledge row is embedded from: title + distilled summary,
    falling back to the content head when no summary exists yet."""
    body = summary if summary else (content or "")[:CONTENT_HEAD_CHARS]
    return f"{title}\n{body}".strip() if title else body.strip()




def _build_embedder() -> Embedder | None:
    # Native model loading occurs once inside the gateway, not in each consumer.
    return _gateway_local_embed


def embed_backend() -> str:
    """nvidia unless RADON_KB_EMBED_BACKEND=local. Unknown values stay local."""
    value = os.environ.get(BACKEND_ENV, "nvidia").strip().lower()
    if value == "nvidia":
        return "nvidia"
    return "local"


def dual_write_enabled() -> bool:
    """Write both vectors. Default on. RADON_KB_EMBED_DUAL_WRITE=0 disables v2."""
    raw = os.environ.get(DUAL_WRITE_ENV)
    if raw is None or not raw.strip():
        return True
    return raw.strip().lower() not in {"0", "false", "off", "no"}


def embed_passages(texts: Sequence[str], *, post=None) -> list[list[float]]:
    return _nvidia_embed(texts, input_type="passage", post=post)


def embed_query(texts: Sequence[str], *, post=None) -> list[list[float]]:
    return _nvidia_embed(texts, input_type="query", post=post)


_COVERAGE_TTL_S = 60.0
_coverage_lock = threading.Lock()
_coverage_cache: dict = {"at": None, "ready": None}


def reset_v2_coverage_cache() -> None:
    with _coverage_lock:
        _coverage_cache["at"] = None
        _coverage_cache["ready"] = None


def v2_coverage_ready(db, *, now=None) -> bool:
    """False while any knowledge.embedding_v2 is NULL.

    SELECT 1 ... LIMIT 1 is the cheap form of that count. The result is
    cached for 60 seconds. A missing column or a failed read is not ready,
    so queries stay on the 384 index instead of a partial 2048-d column.
    """
    current = now() if callable(now) else (time.monotonic() if now is None else float(now))
    with _coverage_lock:
        stamped = _coverage_cache["at"]
        if (
            stamped is not None
            and _coverage_cache["ready"] is not None
            and current - stamped < _COVERAGE_TTL_S
        ):
            return bool(_coverage_cache["ready"])
    ready = _probe_v2_coverage(db)
    with _coverage_lock:
        _coverage_cache["at"] = current
        _coverage_cache["ready"] = ready
    if not ready:
        print(
            "[knowledge-embed] embedding_v2 backfill incomplete; queries use bge-384",
            file=sys.stderr,
        )
    return ready


def _probe_v2_coverage(db) -> bool:
    try:
        row = db.execute(
            "SELECT 1 FROM knowledge WHERE embedding_v2 IS NULL LIMIT 1"
        ).fetchone()
    except Exception:
        return False
    return row is None


def resolve_query_vector(
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
    use_nvidia = embed_backend() == "nvidia"
    if use_nvidia and coverage_ready is not None and not coverage_ready():
        use_nvidia = False
    if use_nvidia:
        try:
            vector = embed_query([text], post=post)[0]
            if len(vector) != EMBEDDING_DIM_V2:
                raise RuntimeError(f"nvidia embedding dim {len(vector)}")
            return [float(value) for value in vector]
        except Exception as exc:  # noqa: BLE001 — fall through to local bge
            if on_nvidia_error is not None:
                on_nvidia_error(exc)
    if local_embedder is None:
        return None
    try:
        vector = local_embedder([text])[0]
    except Exception as exc:  # noqa: BLE001 — FTS-only is a valid mode
        if on_local_error is not None:
            on_local_error(exc)
        return None
    return [float(value) for value in vector]


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

"""Knowledge embeddings.

The live default is local fastembed / bge-small-en-v1.5 (384d, migration
0028). Journal and P&L text stays off third-party APIs unless
RADON_KB_EMBED_BACKEND=nvidia. That backend calls nvidia/nemotron-3-embed-1b
(2048d only; other `dimensions` values are rejected) and writes
embedding_v2. nvidia/llama-3.2-nv-embedqa-1b-v1 is not used: this account
gets 404 Function not found, and the model caps input at 512 tokens.

get_embedder() is the local singleton. It returns None when
RADON_KB_EMBED_DISABLED=1 or fastembed is missing/broken, and ingest then
writes FTS-only rows (embedding NULL). The ONNX model (~67 MB) downloads
once into FASTEMBED_CACHE_PATH, defaulted here to ~/.cache/fastembed.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Callable, Sequence

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384
EMBEDDING_MODEL_V2 = "nvidia/nemotron-3-embed-1b"
EMBEDDING_DIM_V2 = 2048
VECTOR_INDEX_V1 = "idx_knowledge_embedding"
VECTOR_INDEX_V2 = "idx_knowledge_embedding_v2"
DISABLE_ENV = "RADON_KB_EMBED_DISABLED"
BACKEND_ENV = "RADON_KB_EMBED_BACKEND"
DUAL_WRITE_ENV = "RADON_KB_EMBED_DUAL_WRITE"
NVIDIA_EMBED_URL = "https://integrate.api.nvidia.com/v1/embeddings"
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


def _import_text_embedding():
    from fastembed import TextEmbedding  # noqa: PLC0415 — deliberate lazy import

    return TextEmbedding


def _build_embedder() -> Embedder | None:
    # fastembed's default cache lives under tempfile.gettempdir() — ephemeral
    # on the VPS, so a process without an explicit FASTEMBED_CACHE_PATH (the
    # FastAPI server) would re-download the model instead of finding the copy
    # the ingest unit already has. setdefault keeps explicit unit env winning.
    os.environ.setdefault(
        "FASTEMBED_CACHE_PATH", os.path.expanduser("~/.cache/fastembed")
    )
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
        return [
            [float(value) for value in vector]
            for vector in model.embed(list(texts), batch_size=EMBEDDING_BATCH_SIZE)
        ]

    return embed_texts


def embed_backend() -> str:
    """local unless RADON_KB_EMBED_BACKEND=nvidia. Unknown values stay local."""
    value = os.environ.get(BACKEND_ENV, "local").strip().lower()
    return value if value in {"local", "nvidia"} else "local"


def dual_write_enabled() -> bool:
    """Write embedding_v2 alongside the 384-d vector. nvidia backend implies it."""
    return os.environ.get(DUAL_WRITE_ENV) == "1" or embed_backend() == "nvidia"


def embed_passages(texts: Sequence[str], *, post=None) -> list[list[float]]:
    return _nvidia_embed(texts, input_type="passage", post=post)


def embed_query(texts: Sequence[str], *, post=None) -> list[list[float]]:
    return _nvidia_embed(texts, input_type="query", post=post)


def resolve_query_vector(
    text: str,
    *,
    local_embedder: Embedder | None,
    post=None,
    on_nvidia_error=None,
    on_local_error=None,
) -> list[float] | None:
    """NVIDIA 2048 when the backend says so, else local 384, else None (FTS)."""
    if embed_backend() == "nvidia":
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


def _nvidia_embed(texts: Sequence[str], *, input_type: str, post) -> list[list[float]]:
    from clients.model_ladder import _classify_http_failure, _default_post, _request, safe_error_message

    if input_type not in {"query", "passage"}:
        raise ValueError("input_type must be query or passage")
    pending = list(texts)
    if not pending:
        return []
    key = (os.environ.get("NVIDIA_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("missing NVIDIA_API_KEY")
    headers = {"authorization": f"Bearer {key}", "content-type": "application/json"}
    sender = post or _default_post
    vectors: list[list[float]] = []
    for start in range(0, len(pending), NVIDIA_EMBED_BATCH):
        chunk = pending[start:start + NVIDIA_EMBED_BATCH]
        body = {
            "model": EMBEDDING_MODEL_V2,
            "input": chunk,
            "input_type": input_type,
            "dimensions": EMBEDDING_DIM_V2,
            "encoding_format": "float",
        }
        payload = _embed_with_retries(sender, headers, body, _request, _classify_http_failure, safe_error_message)
        vectors.extend(_vectors_from_payload(payload, len(chunk)))
    return vectors


def _embed_with_retries(post, headers, body, request, classify, safe_message):
    retries = 2
    delay = 0.5
    last = "empty"
    for attempt in range(retries + 1):
        try:
            status, text, payload = request(post, NVIDIA_EMBED_URL, headers, body, timeout=30.0)
        except RuntimeError as exc:
            last = safe_message(exc)
            if attempt >= retries:
                raise RuntimeError(last) from exc
            time.sleep(min(delay, 8.0))
            delay = min(delay * 2, 8.0)
            continue
        if status == 429:
            last = "http_429"
        elif status == 200 and isinstance(payload, dict):
            return payload
        else:
            last = classify(status, text or "")
        retryable = last == "http_429" or last.startswith("http_5")
        if attempt >= retries or not retryable:
            raise RuntimeError(last)
        time.sleep(min(delay, 8.0))
        delay = min(delay * 2, 8.0)
    raise RuntimeError(last)


def _vectors_from_payload(payload: dict, expected: int) -> list[list[float]]:
    rows = list(payload.get("data") or [])
    rows.sort(key=lambda row: row.get("index", 0))
    vectors = []
    for row in rows:
        vector = row.get("embedding")
        if not isinstance(vector, list) or len(vector) != EMBEDDING_DIM_V2:
            raise RuntimeError("nvidia embedding payload is not 2048-d")
        vectors.append([float(value) for value in vector])
    if len(vectors) != expected:
        raise RuntimeError("nvidia embedding batch length mismatch")
    return vectors

"""Which knowledge vector a query uses.

The app transport asks the model gateway. The native transport calls NVIDIA.
This file owns the coverage cache and the fallback order. It does not open
a socket.
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
DISABLE_ENV = "RADON_KB_EMBED_DISABLED"
BACKEND_ENV = "RADON_KB_EMBED_BACKEND"
DUAL_WRITE_ENV = "RADON_KB_EMBED_DUAL_WRITE"
NVIDIA_EMBED_BATCH = 16
CONTENT_HEAD_CHARS = 2000
# fastembed defaults to 256: a 200-chunk catch-up peaked at 4.3 GiB RSS.
# Keep the model inference working set bounded independently of write batches.
EMBEDDING_BATCH_SIZE = 16

Embedder = Callable[[Sequence[str]], list[list[float]]]

_COVERAGE_TTL_S = 60.0
_coverage_lock = threading.Lock()
_coverage_cache: dict = {"at": None, "ready": None}


def embedding_text(title: str | None, summary: str | None, content: str | None) -> str:
    """The text a knowledge row is embedded from: title + distilled summary,
    falling back to the content head when no summary exists yet."""
    body = summary if summary else (content or "")[:CONTENT_HEAD_CHARS]
    return f"{title}\n{body}".strip() if title else body.strip()


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
    query: Callable[[Sequence[str]], Sequence[Sequence[float]]],
    on_nvidia_error=None,
    on_local_error=None,
    coverage_ready: Callable[[], bool] | None = None,
) -> list[float] | None:
    """NVIDIA 2048 when the backend says so and v2 coverage is complete.

    Incomplete coverage skips NVIDIA and uses local 384. NVIDIA errors
    (429, 5xx, timeouts) also fall through to local 384, else None (FTS).
    ``query`` is the caller's transport.
    """
    use_nvidia = embed_backend() == "nvidia"
    if use_nvidia and coverage_ready is not None and not coverage_ready():
        use_nvidia = False
    if use_nvidia:
        try:
            vector = query([text])[0]
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

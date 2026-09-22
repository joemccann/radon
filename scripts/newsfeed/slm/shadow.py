"""Shadow-table writer for the SLM tagger CLI (Hrana rule 3: fresh connection)."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

INSERT_SQL = """
INSERT INTO slm_tagger_shadow (
  post_id, observed_at, model_version, mode, tags_slm, tags_slm_raw,
  tags_ladder, ladder_provider, ladder_sampled, slm_status,
  slm_latency_ms, ladder_latency_ms, exact3, jaccard
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def should_sample_ladder(post_id: str | None, instruction: str = "") -> bool:
    """Deterministic 1-in-20 sample. Hash of post_id (or instruction) % 20 == 0."""
    seed = (post_id or instruction or "").encode("utf-8")
    if not seed:
        return False
    digest = int(hashlib.sha256(seed).hexdigest(), 16)
    return digest % 20 == 0


def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"))


def shadow_args(row: dict[str, Any]) -> tuple[Any, ...]:
    observed = row.get("observed_at") or datetime.now(timezone.utc).isoformat()
    return (
        str(row.get("post_id") or ""),
        observed,
        str(row.get("model_version") or "1.0.0"),
        str(row.get("mode") or "shadow"),
        _json_or_none(row.get("tags_slm")),
        _json_or_none(row.get("tags_slm_raw")),
        _json_or_none(row.get("tags_ladder")),
        row.get("ladder_provider"),
        1 if row.get("ladder_sampled") else 0,
        str(row.get("slm_status") or "unavailable"),
        row.get("slm_latency_ms"),
        row.get("ladder_latency_ms"),
        row.get("exact3"),
        row.get("jaccard"),
    )


def write_shadow_row(row: dict[str, Any], *, execute=None) -> bool:
    """One row per post. Write failure is logged and dropped."""
    try:
        runner = execute
        if runner is None:
            from db.hrana_http import hrana_execute

            runner = hrana_execute
        runner(INSERT_SQL, shadow_args(row))
        return True
    except Exception as exc:  # noqa: BLE001 — never block the CLI result
        logger.warning("slm-tagger shadow write dropped: %s", type(exc).__name__)
        return False

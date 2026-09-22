"""Strict observation boundary and publication-time availability."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit


def utc(value: str | datetime | None = None) -> str:
    dt = (
        datetime.now(timezone.utc)
        if value is None
        else (datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value)
    )
    if dt.tzinfo is None:
        raise ValueError("Timestamp must carry an explicit UTC offset")
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def period(value: str) -> str:
    if len(value) == 10:
        return utc(value + "T00:00:00Z")
    return utc(value)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def validate_observation(row: dict) -> dict:
    from .registry import INDICATORS, SOURCES

    row = dict(row)
    for field in (
        "indicator_id",
        "series_id",
        "source_id",
        "unit",
        "period_start",
        "period_end",
        "fetched_at",
        "source_url",
        "raw_hash",
        "methodology_version",
        "cohort_version",
        "lineage_group",
        "measurement",
    ):
        if not isinstance(row.get(field), str) or not row[field].strip():
            raise ValueError(f"Missing observation field: {field}")
    if row["indicator_id"] not in INDICATORS or row["source_id"] not in SOURCES:
        raise ValueError("Unregistered indicator or source")
    if row["source_id"] not in INDICATORS[row["indicator_id"]]["source_ids"]:
        raise ValueError("Source is not registered for this indicator")
    if row["lineage_group"] != SOURCES[row["source_id"]]["lineage_group"]:
        raise ValueError("Source lineage mismatch")
    value = row.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Observation value must be finite numeric evidence")
    if row["measurement"] not in ("observed", "derived", "estimated"):
        raise ValueError("Invalid measurement class")
    if not re.fullmatch(r"[0-9a-f]{64}", row["raw_hash"]):
        raise ValueError("raw_hash must be a SHA256")
    url = urlsplit(row["source_url"])
    if (
        url.scheme not in ("https", "http")
        or not url.netloc
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("Source URL must be canonical and credential-free")
    row["period_start"], row["period_end"] = period(row["period_start"]), period(row["period_end"])
    row["fetched_at"] = utc(row["fetched_at"])
    row["published_at"] = utc(row["published_at"]) if row.get("published_at") else None
    if row["period_end"] < row["period_start"]:
        raise ValueError("Reversed observation period")
    if not isinstance(row.get("metadata", {}), dict):
        raise ValueError("metadata must be an object")
    row.setdefault("metadata", {})
    if row["period_end"] > row["fetched_at"] and not (
        row["measurement"] == "estimated" and row["metadata"].get("forecast") is True
    ):
        raise ValueError("Future measurement period requires an explicit estimated forecast")
    canonical(row)
    return row


def available_at(row: dict) -> str:
    """A backfilled publication timestamp can never predate actual first sight."""
    return max(utc(row["fetched_at"]), utc(row.get("published_at") or row["fetched_at"]))


COHORT_INDICATORS = frozenset({"D1", "D2", "D3", "C1", "C2", "C3", "C4"})


def response_group(row):
    """Publisher response membership scope, independent of individual members."""
    if row["indicator_id"] not in COHORT_INDICATORS:
        return None
    parts = [row["indicator_id"], row["source_id"], row["period_end"][:10], row["methodology_version"]]
    metadata = row["metadata"]
    if row["indicator_id"] == "D3":
        parts.extend([metadata.get("dataset"), metadata.get("metric"), metadata.get("modality")])
    elif row["indicator_id"] in ("C2", "C3", "C4"):
        parts.append(row["cohort_version"])
    return canonical(parts)

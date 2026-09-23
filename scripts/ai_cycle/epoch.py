"""Epoch AI public CC BY CSVs. Structural compute context, never a trade signal."""

from __future__ import annotations

import csv
import io
import re

from .collectors import SourceError, _http_published, number, observation

SOURCE_ID = "epoch"
INDICATOR_ID = "S1"
MODELS_URL = "https://epoch.ai/data/notable_ai_models.csv"
CENTERS_URL = "https://epoch.ai/data/data_centers/data_centers.csv"
USER_AGENT = "RadonAICycle/1.0 (epoch collector; +https://github.com/joemccann/radon)"
METHODOLOGY_VERSION = "epoch-csv-v1"
COHORT_VERSION = "epoch-public-csv-v1"
LICENSE = "CC BY 4.0"
ATTRIBUTION = "Source: Epoch AI"
ESTIMATION_METHOD = "Epoch published estimate"
_OWNER_SUFFIX = re.compile(r"\s+#([A-Za-z0-9_-]+)\s*$")
_MODEL_COLUMNS = (
    "Model",
    "Organization",
    "Publication date",
    "Training compute (FLOP)",
    "Frontier model",
)
_CENTER_COLUMNS = ("Name", "Current H100 equivalents", "Current power (MW)", "Owner")


def _rows(text, required):
    if not isinstance(text, str) or not text.strip():
        raise SourceError("Epoch CSV was empty")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    missing = [column for column in required if column not in (reader.fieldnames or [])]
    if missing:
        raise SourceError("Epoch CSV header is missing required columns")
    return reader


def _truthy(value):
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def _optional_number(value):
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    try:
        return number(text)
    except (TypeError, ValueError, SourceError) as exc:
        raise SourceError("Epoch measurement is not numeric") from exc


def _owner(value):
    text = "" if value is None else str(value).strip()
    match = _OWNER_SUFFIX.search(text)
    if not match:
        return text, None
    return text[: match.start()].strip(), match.group(1).lower()


def _obs(series, value, unit, start, digest, fetched, *, url, metadata, published=None):
    return observation(
        SOURCE_ID,
        INDICATOR_ID,
        series,
        value,
        unit,
        start,
        start,
        digest,
        fetched,
        published=published,
        url=url,
        cohort=COHORT_VERSION,
        measurement="estimated",
        methodology_version=METHODOLOGY_VERSION,
        metadata={
            "attribution": ATTRIBUTION,
            "license": LICENSE,
            "estimation_method": ESTIMATION_METHOD,
            **metadata,
        },
    )


def parse_models(text, digest, fetched, *, published=None, etag=None):
    best = None
    for row in _rows(text, _MODEL_COLUMNS):
        if not _truthy(row.get("Frontier model")):
            continue
        flop = _optional_number(row.get("Training compute (FLOP)"))
        if flop is None:
            continue
        if best is None or flop > best[0]:
            best = (flop, row)
    if best is None:
        raise SourceError("Epoch CSV has no numeric frontier training compute")
    flop, row = best
    day = fetched[:10]
    return [
        _obs(
            "frontier.max_training_flop",
            flop,
            "FLOP",
            day,
            digest,
            fetched,
            url=MODELS_URL,
            published=published,
            metadata={
                "entity": (row.get("Model") or "").strip(),
                "org": (row.get("Organization") or "").strip(),
                "label": "Frontier max training compute",
                "publication_date": (row.get("Publication date") or "").strip(),
                "confidence": (row.get("Confidence") or "").strip() or None,
                "source_file": "notable_ai_models.csv",
                "csv_etag": etag,
                "definition": "Maximum published Training compute (FLOP) among Frontier model rows. Source: Epoch AI",
            },
        )
    ]


def parse_centers(text, digest, fetched, *, published=None, etag=None):
    best = None
    for row in _rows(text, _CENTER_COLUMNS):
        h100e = _optional_number(row.get("Current H100 equivalents"))
        if h100e is None:
            continue
        if best is None or h100e > best[0]:
            best = (h100e, row)
    if best is None:
        raise SourceError("Epoch CSV has no numeric current H100 equivalents")
    h100e, row = best
    power = _optional_number(row.get("Current power (MW)"))
    if power is None:
        raise SourceError("Epoch max H100e row is missing current power")
    owner, confidence = _owner(row.get("Owner"))
    day = fetched[:10]
    common = {
        "entity": (row.get("Name") or "").strip(),
        "owner": owner,
        "publication_date": None,
        "confidence": confidence,
        "source_file": "data_centers.csv",
        "csv_etag": etag,
    }
    return [
        _obs(
            "data_center.max_current_h100e",
            h100e,
            "H100e",
            day,
            digest,
            fetched,
            url=CENTERS_URL,
            published=published,
            metadata={
                **common,
                "label": "Max current H100 equivalents",
                "definition": "Maximum published Current H100 equivalents. Source: Epoch AI",
            },
        ),
        _obs(
            "data_center.max_current_h100e.power_mw",
            power,
            "MW",
            day,
            digest,
            fetched,
            url=CENTERS_URL,
            published=published,
            metadata={
                **common,
                "label": "Max current H100e site power",
                "definition": "Current power (MW) on the max Current H100 equivalents row. No MW to H100e conversion. Source: Epoch AI",
            },
        ),
    ]


def collect_epoch(transport):
    headers = {"User-Agent": USER_AGENT}
    models_text, models_digest, models_fetched, models_meta = transport.fetch_html(MODELS_URL, headers=headers)
    centers_text, centers_digest, centers_fetched, centers_meta = transport.fetch_html(CENTERS_URL, headers=headers)
    return [
        *parse_models(
            models_text,
            models_digest,
            models_fetched,
            published=_http_published(models_meta.get("last_modified")),
            etag=models_meta.get("etag"),
        ),
        *parse_centers(
            centers_text,
            centers_digest,
            centers_fetched,
            published=_http_published(centers_meta.get("last_modified")),
            etag=centers_meta.get("etag"),
        ),
    ]

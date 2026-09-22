"""Liquid Compute public GPU index ticker. Third venue, never the rental book.

Homepage-public market ticker at GET /api/market/ticker. robots.txt Disallows
/api/ but the public homepage loads this payload; treat it as homepage data.
Identify Radon. Back off on non-200. Do not scrape /auth/ or /ingest/.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date
from pathlib import Path

from .collectors import SourceError, Transport, now_iso, number, observation

TICKER_URL = "https://liquidcompute.com/api/market/ticker"
USER_AGENT = "RadonAICycle/1.0 (liquidcompute collector; +https://github.com/joemccann/radon)"
SOURCE_ID = "liquidcompute"
UNIT = "usd_per_gpu_per_hr"
BACKOFF_SECONDS = (1.0, 2.0, 4.0)
HEALTH_SERVICE = "liquidcompute"
METHODOLOGY_VERSION = "liquidcompute-ticker-v1"
COHORT_VERSION = "liquidcompute-us-index-v1"
DEFINITION = (
    "Third-venue public GPU index from the Liquid Compute homepage ticker. "
    "Index methodology is opaque until licensed. Not a rental-book asking price, "
    "not Silicon Data, and not gpurentalprices."
)


def _as_of_date(value):
    if not isinstance(value, str):
        raise SourceError("Liquid Compute asOf is missing")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise SourceError("Liquid Compute asOf is not an ISO date") from exc


def parse_ticker(payload, digest, fetched):
    """Map one homepage ticker payload to host-tagged rows plus C5 observations."""
    if not isinstance(payload, dict):
        raise SourceError("Liquid Compute ticker schema changed")
    as_of = _as_of_date(payload.get("asOf"))
    if payload.get("units") != UNIT:
        raise SourceError("Liquid Compute units schema changed")
    if as_of > fetched[:10]:
        raise SourceError("Liquid Compute asOf is in the future")
    indices = payload.get("indices")
    if not isinstance(indices, list) or not indices:
        raise SourceError("Liquid Compute indices are missing")
    seen, store_rows, observations = set(), [], []
    for item in indices:
        if not isinstance(item, dict):
            raise SourceError("Liquid Compute index row schema changed")
        series_id = item.get("id")
        if not isinstance(series_id, str) or not series_id.strip():
            raise SourceError("Liquid Compute index id is missing")
        if series_id in seen:
            raise SourceError("Duplicate Liquid Compute index id")
        seen.add(series_id)
        label = item.get("label") if isinstance(item.get("label"), str) and item["label"].strip() else series_id
        value = number(item.get("value"))
        change = item.get("changePct")
        if change is not None:
            if isinstance(change, bool) or not isinstance(change, (int, float)):
                raise SourceError("Liquid Compute changePct is not numeric")
            change = float(change)
        metadata = {
            "label": label,
            "entity": series_id,
            "venue": "liquidcompute-index",
            "date": as_of,
            "vintage": as_of,
            "definition": DEFINITION,
            "lane": "gpu-index-third-venue",
            "not_rental_book": True,
            "not_silicon_data": True,
            "license": "Homepage-public ticker; methodology opaque until licensed",
        }
        if change is not None:
            metadata["change_pct"] = change
        store_rows.append(
            {
                "date": as_of,
                "source": SOURCE_ID,
                "series_id": series_id,
                "value": value,
                "unit": UNIT,
                "vintage": as_of,
                "label": label,
                "fetched_at": fetched,
                "raw_hash": digest,
            }
        )
        observations.append(
            observation(
                SOURCE_ID,
                "C5",
                series_id,
                value,
                UNIT,
                as_of,
                as_of,
                digest,
                fetched,
                published=f"{as_of}T00:00:00Z",
                metadata=metadata,
                url=TICKER_URL,
                cohort=COHORT_VERSION,
                methodology_version=METHODOLOGY_VERSION,
            )
        )
    return store_rows, observations


def fetch_ticker(transport, *, sleep=time.sleep):
    """GET the public ticker with identifying UA and exponential backoff on non-200."""
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    last_error = None
    for delay in (*BACKOFF_SECONDS, None):
        try:
            return transport.fetch(TICKER_URL, headers=headers)
        except SourceError as exc:
            reason = str(exc)
            last_error = exc
            retryable = "HTTP " in reason or "transport failed" in reason
            if not retryable or delay is None:
                raise
            sleep(delay)
    raise last_error


def store_row_from_observation(row):
    return {
        "date": row["metadata"]["date"],
        "source": row["source_id"],
        "series_id": row["series_id"],
        "value": row["value"],
        "unit": row["unit"],
        "vintage": row["metadata"]["vintage"],
        "label": row["metadata"].get("label"),
        "fetched_at": row["fetched_at"],
        "raw_hash": row["raw_hash"],
    }


def persist_ticker(store, observations):
    store.upsert_liquidcompute([store_row_from_observation(row) for row in observations])
    store.upsert_observations_by_identity(observations)
    return len(observations)


def _write_health(state, started, error=None):
    from scripts.db.hrana_http import write_service_health_http

    write_service_health_http(
        HEALTH_SERVICE,
        state,
        started_at=started,
        finished_at=now_iso(),
        error=error,
        timeout=8,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true", help="Upsert host-tagged rows")
    parser.add_argument("--database", help="Isolated SQLite path")
    parser.add_argument("--archive", default=str(Path.home() / ".radon/ai-cycle/raw"))
    args = parser.parse_args(argv)
    started = now_iso()
    transport = Transport(Path(args.archive), max_requests=8)
    production = args.record and args.database is None and not os.environ.get("RADON_AI_CYCLE_DB_PATH")
    try:
        payload, digest, fetched = fetch_ticker(transport)
        store_rows, observations = parse_ticker(payload, digest, fetched)
        if args.record:
            from .snapshot import persist_api_snapshot
            from .store import ObservationStore

            store = ObservationStore(args.database)
            persist_ticker(store, observations)
            store.import_raw_archive(Path(args.archive))
            persist_api_snapshot(store)
        print(
            json.dumps(
                {
                    "mode": "record" if args.record else "verify",
                    "source": SOURCE_ID,
                    "as_of": store_rows[0]["date"],
                    "rows": len(store_rows),
                    "series": [row["series_id"] for row in store_rows],
                },
                indent=2,
            )
        )
        if production:
            _write_health("ok", started)
        return 0
    except (SourceError, KeyError, TypeError, ValueError, OSError) as exc:
        reason = str(exc) if isinstance(exc, SourceError) else "Source schema or local archive validation failed"
        print(json.dumps({"mode": "record" if args.record else "verify", "source": SOURCE_ID, "error": reason}, indent=2))
        if production:
            _write_health("error", started, {"message": "Liquid Compute ticker collection failed"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Retired research health. Stdlib only: the health daemon imports this.

The ingestion review stage (`research.ingestion.stage_health(..., 'review')`)
runs only inside `radon-research.service`, which writes the Turso row
`dropbox-research`. That unit is disabled. `marketdesk_job` and
`marketdesk_pdf_job` do not call it. They write `marketdesk-research` and
`marketdesk-pdf`. A leftover row can still carry the 2026-10-15 `DbHttpError`.
Readers mark that row retired and point current research health at the live
services. They do not treat those services as hosts of the old stage.
"""
from __future__ import annotations

RETIRED_RESEARCH_SERVICE = "dropbox-research"
RETIRED_RESEARCH_UNIT = "radon-research.service"
LIVE_RESEARCH_SERVICES = ("marketdesk-research", "marketdesk-pdf")
_RETIRED_NOTE = (
    "Ingestion review ran only in radon-research.service. "
    "That stage does not run on the live services. "
    "Current research health is marketdesk-research and marketdesk-pdf."
)


def present_service_health_rows(rows):
    """Copy ``rows``, replacing a leftover dropbox-research error with a retired marker."""
    if not isinstance(rows, list):
        return rows
    indexed = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("service"), str):
            indexed[row["service"]] = row
    live = []
    for name in LIVE_RESEARCH_SERVICES:
        row = indexed.get(name)
        if not row:
            continue
        live.append({
            "service": name,
            "state": row.get("state"),
            "updated_at": row.get("updated_at"),
        })
    presented = []
    for row in rows:
        if not isinstance(row, dict) or row.get("service") != RETIRED_RESEARCH_SERVICE:
            presented.append(row)
            continue
        marked = dict(row)
        marked["state"] = "retired"
        marked["retired"] = True
        marked["unit"] = RETIRED_RESEARCH_UNIT
        marked["last_error"] = None
        marked["succeeded_by"] = list(LIVE_RESEARCH_SERVICES)
        marked["note"] = _RETIRED_NOTE
        marked["review_stage"] = {
            "retired": True,
            "retired_service": RETIRED_RESEARCH_SERVICE,
            "retired_unit": RETIRED_RESEARCH_UNIT,
            "runs_on": [],
            "live_services": live,
        }
        presented.append(marked)
    return presented


def present_service_health_section(section):
    """Return a copy of a `/status` service_health section with retired rows marked."""
    if not isinstance(section, dict):
        return section
    rows = section.get("rows")
    if not isinstance(rows, list):
        return section
    presented = present_service_health_rows(rows)
    out = dict(section)
    out["rows"] = presented
    return out

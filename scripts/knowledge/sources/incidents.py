"""Incidents connector — service_health is current-state-per-service (PK is
service, upserted each writer cycle; no history retained), so this emits one
live state digest per service. Historical incident knowledge lives in
tasks/lessons.md via the docs connector.

Content carries only what changes when something HAPPENS: the state, and the
error text for a non-ok state. Attempt/updated timestamps and the per-run
detail payload of a healthy service are rewritten every writer cycle; putting
them in content made every service re-distill every hour. Timestamps ride
last_activity_at, which the store only bumps when content changes — so it
reads as "when this service's state last changed", not "last run"."""
from __future__ import annotations

import json
from typing import Iterator

from ..schema import KnowledgeDoc

SOURCE = "incidents"
SCOPE = "ops"

_ROWS_SQL = (
    "SELECT service, state, last_attempt_started_at, last_attempt_finished_at, "
    "last_error, updated_at FROM service_health ORDER BY service"
)


def fetch(db) -> Iterator[KnowledgeDoc]:
    for service, state, started, finished, last_error, updated in db.execute(_ROWS_SQL).fetchall():
        yield KnowledgeDoc(
            source=SOURCE,
            scope=SCOPE,
            doc_key=service,
            content=_digest(service, state, last_error),
            title=f"{service} service health: {state}",
            metadata={"service": service, "state": state},
            created_at=updated,
            last_activity_at=updated or finished,
        )


def _digest(service, state, last_error) -> str:
    lines = [f"Service {service} is {state or 'unknown'}."]
    detail = _detail(last_error) if state != "ok" else None
    if detail:
        lines.append(f"Last error: {detail}")
    return "\n".join(lines)


def _detail(last_error) -> str | None:
    if not last_error:
        return None
    try:
        parsed = json.loads(last_error)
    except (ValueError, TypeError):
        return str(last_error)
    if isinstance(parsed, dict):
        return "; ".join(f"{key}={value}" for key, value in parsed.items())
    return str(parsed)

"""service_health.state is an enum the shared writer enforces.

The column has no CHECK constraint; the vocabulary lived only in comments.
``service_health_upsert_args`` is the args builder every bounded Python
writer goes through (db.writer, hrana_http, FastAPI heal paths), so it
refuses anything outside the values writers actually produce.
"""
from __future__ import annotations

import pytest

from db.service_health_sql import SERVICE_HEALTH_STATES, service_health_upsert_args


def test_vocabulary_matches_what_writers_write():
    # warn: FastAPI orders-sync shed (scripts/api/server.py) and
    # web/lib/serviceHealth.ts ServiceHealthState.
    assert SERVICE_HEALTH_STATES == frozenset({"ok", "syncing", "warn", "error", "paused"})


@pytest.mark.parametrize("state", sorted({"ok", "syncing", "warn", "error", "paused"}))
def test_known_states_are_written(state):
    args = service_health_upsert_args("svc", state)
    assert args[:2] == ("svc", state)


@pytest.mark.parametrize("state", ["OK", "healthy", "degraded", "dormant", "", None])
def test_unknown_state_is_refused(state):
    with pytest.raises(ValueError):
        service_health_upsert_args("svc", state)

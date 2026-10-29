"""Retired dropbox-research health must not replay as a live review failure."""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from health_service.probes import build_status
from health_service.retired import (
    LIVE_RESEARCH_SERVICES,
    RETIRED_RESEARCH_SERVICE,
    RETIRED_RESEARCH_UNIT,
    present_service_health_rows,
)
from knowledge.sources import incidents as incidents_source
from watchdog.check import check_service
from watchdog.units import evaluate


def _row(service, state, last_error, updated_at="2026-10-15T20:02:02+00:00"):
    return {
        "service": service,
        "state": state,
        "last_error": last_error,
        "updated_at": updated_at,
    }


def test_present_rows_retire_the_stale_review_stage_and_point_at_live_services():
    stale = json.dumps({
        "discovery": None,
        "stages": [{
            "stage": "review",
            "state": "error",
            "error": "DbHttpError",
            "updated_at": "2026-10-15T20:02:02+00:00",
        }],
    })
    rows = present_service_health_rows([
        _row(RETIRED_RESEARCH_SERVICE, "error", stale),
        _row("marketdesk-research", "ok", None, "2026-10-29T12:00:00+00:00"),
        _row("marketdesk-pdf", "error", "preview_quota", "2026-10-29T12:00:00+00:00"),
        _row("cri-scan", "ok", None),
    ])
    retired = next(row for row in rows if row["service"] == RETIRED_RESEARCH_SERVICE)
    assert retired["state"] == "retired"
    assert retired["retired"] is True
    assert retired["unit"] == RETIRED_RESEARCH_UNIT
    assert retired["last_error"] is None
    assert "DbHttpError" not in json.dumps(retired)
    assert retired["succeeded_by"] == list(LIVE_RESEARCH_SERVICES)
    review = retired["review_stage"]
    assert review["retired"] is True
    assert review["runs_on"] == []
    assert [item["service"] for item in review["live_services"]] == list(LIVE_RESEARCH_SERVICES)
    assert review["live_services"][0]["state"] == "ok"
    assert review["live_services"][1]["state"] == "error"
    assert next(row for row in rows if row["service"] == "cri-scan")["state"] == "ok"


def test_status_body_does_not_replay_the_retired_review_error():
    stale = json.dumps({"stages": [{"stage": "review", "error": "DbHttpError"}]})
    body = build_status(
        {},
        {},
        "2026-10-29T00:00:00+00:00",
        service_health={
            "state": "ok",
            "rows": [_row(RETIRED_RESEARCH_SERVICE, "error", stale)],
            "row_count": 1,
        },
    )
    row = body["service_health"]["rows"][0]
    assert row["state"] == "retired"
    assert row["last_error"] is None
    assert "DbHttpError" not in json.dumps(row)


def test_watchdog_does_not_fire_on_the_retired_service():
    now = datetime(2026, 10, 29, tzinfo=timezone.utc)
    outcome = check_service(
        service=RETIRED_RESEARCH_SERVICE,
        kind="error",
        now=now,
        market_state="closed",
    )
    assert outcome.status == "disabled"
    assert outcome.fired is False
    assert outcome.message == "retired"


def test_watchdog_ignores_a_failed_retired_unit():
    now = datetime(2026, 10, 29, tzinfo=timezone.utc)
    retired = {
        "Id": RETIRED_RESEARCH_UNIT,
        "ActiveState": "failed",
        "Result": "exit-code",
        "NRestarts": 3,
        "Type": "simple",
        "SubState": "failed",
    }
    live = {
        "Id": "radon-marketdesk.service",
        "ActiveState": "failed",
        "Result": "exit-code",
        "NRestarts": 1,
        "Type": "simple",
        "SubState": "failed",
    }
    outcomes = evaluate(current=[retired, live], previous={}, now=now)
    assert [item.service for item in outcomes] == ["radon-marketdesk.service"]


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE service_health (service TEXT PRIMARY KEY, state TEXT, "
        "last_attempt_started_at TEXT, last_attempt_finished_at TEXT, "
        "last_error TEXT, updated_at TEXT)"
    )
    return conn


def test_incidents_digest_does_not_replay_the_retired_review_error(db):
    stale = json.dumps({
        "stages": [{"stage": "review", "state": "error", "error": "DbHttpError"}],
    })
    db.execute(
        "INSERT INTO service_health VALUES (?, ?, ?, ?, ?, ?)",
        (RETIRED_RESEARCH_SERVICE, "error", "2026-10-15T20:02:02+00:00",
         "2026-10-15T20:02:02+00:00", stale, "2026-10-15T20:02:02+00:00"),
    )
    db.commit()
    docs = list(incidents_source.fetch(db))
    assert len(docs) == 1
    text = docs[0].content
    assert "DbHttpError" not in text
    assert "retired" in text
    assert "marketdesk-research" in text
    assert "marketdesk-pdf" in text
    assert "does not run" in text

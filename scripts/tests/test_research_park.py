"""Provider outages park a document without consuming its attempts; only evidence failures hold."""
import time

import pytest

from research import ingestion, model
from research.state import State


def entry():
    return {".tag": "file", "id": "id:one", "rev": "rev1", "content_hash": "a" * 64, "name": "report.pdf",
            "path_lower": "/joe mccann/current/2026/september/sep 07/report.pdf",
            "path_display": "/Joe McCann/Current/2026/September/Sep 07/report.pdf"}


@pytest.fixture
def state(tmp_path):
    root = tmp_path / "private"; root.mkdir(mode=0o700)
    s = State(root / "state.sqlite")
    s.ingest_page("2026/September/Sep 07", {"cursor": "c", "entries": [entry()]}, "2026-09-07")
    yield s
    s.close()


@pytest.mark.parametrize("message,outage", [
    ("Model ladder exhausted after trying every keyed provider (anthropic: quota; gemini: http_429)", True),
    ("http_429 rate limit", True),
    ("provider http_503", True),
    ("Research reviewer unavailable or malformed response", True),
    ("Source image exceeds review byte limit", False),
])
def test_provider_outage_classification(message, outage):
    assert model.is_provider_outage(model.ModelError(message)) is outage
    assert model.is_provider_outage(ValueError(message)) is False


def test_park_returns_work_to_pending_without_consuming_an_attempt(state):
    key = state.pending()[0]["key"]
    assert state.claim(key)
    state.park(key, retry_at=time.time() + 600)
    row = state.db.execute("SELECT status, attempts, available_at FROM work WHERE key=?", (key,)).fetchone()
    assert row["status"] == "pending" and row["attempts"] == 0 and row["available_at"] > time.time() + 500
    assert state.pending() == [], "parked work is not offered before retry_at"


def test_review_one_parks_on_outage_even_after_many_attempts(state, tmp_path):
    key = state.pending()[0]["key"]
    state.db.execute("UPDATE work SET attempts=5 WHERE key=?", (key,)); state.db.commit()
    pdf = tmp_path / "r.pdf"; pdf.write_bytes(b"%PDF-")
    state.claim_parse(key); state.parsed(key, str(pdf))

    class Pipeline:
        def process(self, *a, **k):
            raise model.ModelError("Model ladder exhausted after trying every keyed provider")

    class Publisher:
        def recent_posts(self, days=90): return []
        def publish(self, payload): raise AssertionError("no publish")

    from research.dropbox import content_hash
    state.db.execute("UPDATE work SET metadata=json_set(metadata,'$.content_hash',?) WHERE key=?", (content_hash(b"%PDF-"), key)); state.db.commit()
    assert ingestion.review_one(tmp_path, state, Pipeline(), Publisher(), publish=False)
    row = state.db.execute("SELECT status, attempts, result FROM work WHERE key=?", (key,)).fetchone()
    assert row["status"] == "pending" and row["attempts"] == 5 and row["result"] is None

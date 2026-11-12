"""Citadel Rubner GMI always-publish: held-row TTL exemption and outcome mirror."""
import json
from datetime import datetime, timezone

from research import publish
from tests.test_research_force_include import RUBNER_PAGE


def work(name, folder):
    return {"key": "k" * 64, "folder_date": "2026-10-01",
            "metadata": {"name": name, "id": "id:one", "rev": "r1", "content_hash": "a" * 64,
                         "path_lower": f"/joe mccann/current/2026/october/oct 01/{folder}/{name}",
                         "client_modified": "2026-10-01T08:00:00Z"}}


def test_expire_skips_always_publish_held_rows(monkeypatch):
    calls = []

    def fake(sql, args=(), **kw):
        calls.append((sql, args))
        if "SELECT" in sql:
            return [(1, "rubner-key", "Citadel", "the q4 reload",
                     "Citadel - The Q4 Reload October 1 Oct 2026.pdf",
                     json.dumps({"alwaysPublish": True, "excerpt": RUBNER_PAGE}))]
        return []

    monkeypatch.setattr(publish, "hrana_execute", fake)
    published = []
    monkeypatch.setattr(publish, "publish", lambda post: published.append(post))
    count = publish.expire_stale_held(now=datetime(2026, 10, 3, 15, 0, tzinfo=timezone.utc))
    assert count == 0 and published == []
    assert any("SELECT" in sql for sql, _ in calls)
    assert not any("UPDATE" in sql and "HELD_EXPIRED" in sql for sql, _ in calls)



def test_expire_still_drops_ordinary_held_rows(monkeypatch):
    calls = []

    def fake(sql, args=(), **kw):
        calls.append((sql, args))
        if "SELECT" in sql:
            return [(1, "old-key", "Goldman Sachs", "tic data", "tic data.pdf", "{}")]
        return [("old-key",)]

    monkeypatch.setattr(publish, "hrana_execute", fake)
    count = publish.expire_stale_held(now=datetime(2026, 10, 3, 15, 0, tzinfo=timezone.utc))
    assert count == 1
    assert any("HELD_EXPIRED" in sql for sql, _ in calls)



def test_outcome_row_records_always_publish_flag_and_fallback_code():
    row = publish.outcome_row(
        work("Citadel - The Q4 Reload October 1 Oct 2026.pdf", folder="citadel"),
        {"pipeline": "v2", "outcome": "reviewed", "always_publish": True,
         "always_publish_fallback": True,
         "identity": {"publisher": "Citadel", "series": "the q4 reload", "doc_type": "research",
                      "date": "2026-10-01", "date_source": "text"},
         "audit": [{"held": "VERIFY_FAILED", "claim_key": "cta-z",
                    "verification": {"reason": "ungrounded z"}},
                   {"always_publish_fallback": True, "reason_code": "ALWAYS_PUBLISH_FALLBACK"}],
         "selection": {"candidates": [], "reason": "empty"},
         "posts": [{"id": "research-summary"}],
         "document": {"page_count": 17, "excerpt": RUBNER_PAGE}},
    )
    assert json.loads(row["context_json"])["alwaysPublish"] is True
    assert "ALWAYS_PUBLISH_FALLBACK" in json.loads(row["reason_codes"])
    assert row["outcome"] == "published"


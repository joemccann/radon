"""T-539: Unicode display safety is observed at the actual reviewer output."""
from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest

from newsfeed.slm.review_cli import review_rows


@pytest.mark.parametrize("field", ["id", "title", "body"])
def test_unicode_controls_cannot_reorder_any_reviewed_field(field):
    hostile = "Fed\u202ecuts\u2066SPX\u2069 → \u200b5000\u061c\u2028\ufeff"
    values = {"id": "post-17", "title": "Fed cuts", "body": "SPX → 5000"}
    values[field] = hostile
    row = {
        "id": values["id"],
        "messages": [
            {"role": "user", "content": f"Title: {values['title']}\nBody: {values['body']}"},
            {"role": "assistant", "content": '{"tags":["MACRO","FED","SPX"]}'},
        ],
    }
    stdout = io.StringIO()
    now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
    reviewed = review_rows([row], stdin=io.StringIO("y\n"), stdout=stdout, now=now)
    expected = {**values, field: "Fed cuts SPX  →  5000   "}
    assert stdout.getvalue() == (
        f"id={expected['id']}\nTitle: {expected['title']}\nBody: {expected['body']}\n"
        "Tags: ['MACRO', 'FED', 'SPX']\n[y/n/c TAG1,TAG2,TAG3]\n"
    )
    # Display cleaning must preserve the original evidence in the reviewed row.
    assert reviewed == [{**row, "human": "y", "reviewed_at": now.isoformat(), "gold": ["MACRO", "FED", "SPX"]}]

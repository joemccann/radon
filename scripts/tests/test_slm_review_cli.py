"""C.4 review CLI writes gold_human.jsonl with reviewed_at."""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

from newsfeed.slm.review_cli import review_rows


def test_three_row_fixture(tmp_path: Path):
    rows = [
        {
            "id": "a",
            "messages": [
                {"role": "user", "content": "Title: One\nBody: " + ("x" * 50)},
                {"role": "assistant", "content": '{"tags":["GAMMA","SPX","VOL"]}'},
            ],
        },
        {
            "id": "b",
            "messages": [
                {"role": "user", "content": "Title: Two\nBody: " + ("y" * 50)},
                {"role": "assistant", "content": '{"tags":["VIX","SPX","PUTS"]}'},
            ],
        },
        {
            "id": "c",
            "messages": [
                {"role": "user", "content": "Title: Three\nBody: " + ("z" * 50)},
                {"role": "assistant", "content": '{"tags":["OPTIONS","SPX","VOL"]}'},
            ],
        },
    ]
    stdin = io.StringIO("y\nn\nc GAMMA,SPX,VIX\n")
    stdout = io.StringIO()
    now = datetime(2026, 9, 19, tzinfo=timezone.utc)
    reviewed = review_rows(rows, stdin=stdin, stdout=stdout, now=now)
    assert len(reviewed) == 3
    assert all(r["reviewed_at"] == now.isoformat() for r in reviewed)
    assert reviewed[0]["human"] == "y"
    assert reviewed[1]["human"] == "n"
    assert reviewed[2]["corrected_tags"] == ["GAMMA", "SPX", "VIX"]
    dest = tmp_path / "gold_human.jsonl"
    dest.write_text("".join(json.dumps(r) + "\n" for r in reviewed), encoding="utf-8")
    assert "reviewed_at" in dest.read_text(encoding="utf-8")

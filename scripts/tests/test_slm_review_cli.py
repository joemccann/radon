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


def test_scraped_text_reaches_the_tty_without_control_bytes():
    import io

    from newsfeed.slm.review_cli import review_rows

    row = {
        "id": "p1",
        "messages": [
            {"role": "user", "content": "Title: \x1b[2JFed cuts\rTags: SPOOF\nBody: line1\x07\nline2"},
            {"role": "assistant", "content": '{"tags": ["MACRO", "FED", "RATES"]}'},
        ],
    }
    stdout = io.StringIO()
    review_rows([row], stdin=io.StringIO("y\n"), stdout=stdout)
    shown = stdout.getvalue()
    assert "\x1b" not in shown and "\r" not in shown and "\x07" not in shown
    # The title renders on ONE line; embedded newlines cannot spoof fields.
    title_lines = [line for line in shown.splitlines() if line.startswith("Title: ")]
    assert title_lines == ["Title:  [2JFed cuts Tags: SPOOF"]

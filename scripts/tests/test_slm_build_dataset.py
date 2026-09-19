"""HR-4 / HR-8 dataset builder: time splits, one input, row rules."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from newsfeed.slm import build_dataset as bd
from newsfeed.slm.contract import BODY_CHAR_LIMIT, SOURCES, build_user_prompt

REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / "scripts" / "newsfeed" / "slm" / "build_dataset.py"


def _ts(day: str) -> str:
    return f"{day}T12:00:00+00:00"


TAXONOMY = ["GAMMA", "SPX", "VOL", "VIX", "PUTS"]


def _row(**kwargs):
    base = {
        "id": kwargs.pop("id", "p1"),
        "title": "Dealer gamma flip",
        "content": "A long enough body about dealer gamma and SPX positioning that clears forty chars.",
        "timestamp": _ts("2026-06-01"),
        "tags": '["UNION","VISION","PARENT"]',
        "tags_text": '["GAMMA","SPX","VOL"]',
        "tags_vision": None,
        "images": None,
        "is_research": 0,
    }
    base.update(kwargs)
    return base


class TestSourcesAndHygiene:
    def test_manifest_sources_are_turso_posts_only(self):
        result = bd.build_dataset([_row()], taxonomy=TAXONOMY, extract_at=datetime(2026, 9, 19, tzinfo=timezone.utc))
        assert result["manifest"]["sources"] == SOURCES == ["turso.posts"]

    def test_module_has_one_sql_input_and_no_shuffle(self):
        src = MODULE.read_text(encoding="utf-8")
        assert "FROM posts p" in src
        assert "WHERE p.id > ?" in src
        assert "shuffle" not in src
        assert "random" not in src.lower().replace("body_char_limit", "")
        assert "http://" not in src
        assert "https://" not in src
        assert "urllib" not in src
        assert "requests" not in src


class TestRowRules:
    def test_research_rows_excluded(self):
        rows = [_row(id="r1", is_research=1), _row(id="p1")]
        result = bd.build_dataset(rows, taxonomy=TAXONOMY)
        assert [r["id"] for r in result["rows"]] == ["p1"]

    def test_tags_union_never_used_as_label(self):
        rows = [
            _row(id="bad", tags_text=None, tags='["GAMMA","SPX","VOL"]'),
            _row(id="ok"),
        ]
        result = bd.build_dataset(rows, taxonomy=TAXONOMY)
        assert [r["id"] for r in result["rows"]] == ["ok"]

    def test_dedupe_keeps_earliest(self):
        rows = [
            _row(id="later", timestamp=_ts("2026-06-10")),
            _row(id="earlier", timestamp=_ts("2026-06-01")),
        ]
        result = bd.build_dataset(rows, taxonomy=TAXONOMY)
        assert [r["id"] for r in result["rows"]] == ["earlier"]

    def test_user_prompt_parity_and_body_limit_import(self):
        assert BODY_CHAR_LIMIT == 1500
        title = "T"
        content = "x" * 2000
        assert build_user_prompt(title, content) == f"Title: {title}\nBody: {content[:BODY_CHAR_LIMIT]}"
        assert "BODY_CHAR_LIMIT" in MODULE.read_text(encoding="utf-8")
        assert ".slice(0, 1500)" not in MODULE.read_text(encoding="utf-8")

    def test_pii_counts_in_manifest(self):
        rows = [
            _row(
                content=(
                    "Contact joe@example.com and see https://example.com/a for the "
                    "dealer gamma note that is long enough to keep."
                )
            )
        ]
        result = bd.build_dataset(rows, taxonomy=TAXONOMY)
        assert result["manifest"]["pii_email_count"] == 1
        assert result["manifest"]["pii_url_count"] == 1

    def test_rare_tags_and_label_distribution(self):
        rows = []
        for i in range(12):
            rows.append(
                _row(
                    id=f"c{i}",
                    timestamp=_ts("2026-01-01"),
                    tags_text='["GAMMA","SPX","VOL"]',
                    content=f"Gamma note number {i} with enough characters in the body text.",
                )
            )
        for i in range(3):
            rows.append(
                _row(
                    id=f"r{i}",
                    timestamp=_ts("2026-01-02"),
                    tags_text='["VIX","SPX","PUTS"]',
                    content=f"Rare VIX note number {i} with enough characters in the body text.",
                )
            )
        result = bd.build_dataset(
            rows, taxonomy=TAXONOMY, extract_at=datetime(2026, 9, 19, tzinfo=timezone.utc)
        )
        manifest = result["manifest"]
        assert "label_distribution" in manifest
        assert "rare_tags" in manifest
        assert manifest["label_distribution"]["GAMMA"] == 12


class TestTimeSplit:
    def test_strict_time_order_no_overlap(self):
        extract_at = datetime(2026, 9, 19, tzinfo=timezone.utc)
        rows = [
            _row(
                id="train",
                timestamp=_ts("2026-05-01"),
                content="Train-window dealer gamma note with enough characters in the body.",
            ),
            _row(
                id="valid",
                timestamp=_ts("2026-07-01"),
                content="Valid-window dealer gamma note with enough characters in the body.",
            ),
            _row(
                id="test",
                timestamp=_ts("2026-08-20"),
                content="Test-window dealer gamma note with enough characters in the body.",
            ),
        ]
        result = bd.build_dataset(rows, taxonomy=TAXONOMY, extract_at=extract_at)
        splits = result["splits"]
        train_max = max(r["ts"] for r in splits["train"])
        valid_min = min(r["ts"] for r in splits["valid"])
        valid_max = max(r["ts"] for r in splits["valid"])
        test_min = min(r["ts"] for r in splits["test"])
        assert train_max < valid_min <= valid_max < test_min

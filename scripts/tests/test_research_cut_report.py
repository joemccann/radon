"""Daily Dropbox PDF cut: honest HELD_EXPIRED labels and day assignment."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from research import cut_report

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "scripts/tests/fixtures/pdf_cut_2026-09-28.json"
WORKFLOW = REPO / ".github/workflows/research-pdf-cut.yml"
REASON_TS = REPO / "web/lib/researchReasonCodes.ts"
MIGRATE = REPO / "scripts/db/migrate.py"

CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON_SHA = "5fda3b95a4ea91299a34e894583c3862153e4b97"
UPLOAD_SHA = "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"


def _fixture_rows():
    return json.loads(FIXTURE.read_text())["rows"]


def _report(date="2026-09-28", rows=None):
    return cut_report.build_report(rows if rows is not None else _fixture_rows(), date)


def test_reason_code_labels_match_the_web_map():
    text = REASON_TS.read_text()
    block = re.search(r"const LABELS: Record<string, string> = \{([\s\S]*?)\};", text)
    assert block, "researchReasonCodes.ts LABELS missing"
    expected = dict(re.findall(r'([A-Z0-9_]+):\s*"([^"]+)"', block.group(1)))
    assert expected, "no TS labels parsed"
    assert cut_report.REASON_CODE_LABELS == expected


def test_hold_expired_bucket_label_is_ttl_not_retries():
    assert cut_report.HOLD_EXPIRED_LABEL == "hold expired (24h TTL)"
    assert cut_report.HELD_LABEL == "held (still held)"
    report = _report()
    rendered = cut_report.render_markdown(report) + json.dumps(report)
    assert "retries exhausted" not in rendered.lower()
    assert report["buckets"]["hold_expired"]["label"] == "hold expired (24h TTL)"
    assert "retries exhausted" not in json.dumps(cut_report.REASON_CODE_LABELS).lower()


def test_sep_28_fixture_buckets_and_folder_counts():
    report = _report()
    buckets = report["buckets"]
    assert buckets["published"]["docs"] == 76 and buckets["published"]["posts"] == 157
    assert buckets["held"]["docs"] == 15 and buckets["held"]["posts"] == 0
    assert buckets["hold_expired"]["docs"] == 128 and buckets["hold_expired"]["posts"] == 0
    assert buckets["dropped"]["docs"] == 15 and buckets["dropped"]["posts"] == 0
    assert buckets["hold_expired"]["verdicts"] == {
        "NO_CANDIDATES": 71,
        "VERIFY_FAILED": 53,
        "INVALID_CANDIDATE": 3,
        "TEXT_ONLY_WITH_FIGURES": 2,
    }
    assert buckets["dropped"]["reasons"] == {
        "DUPLICATE_OF_PUBLISHED": 14,
        "SINGLE_STOCK_NOT_IN_BOOK": 1,
    }
    folders = report["by_folder"]
    assert folders["2026-09-24"]["published"] == {"docs": 4, "posts": 8}
    assert folders["2026-09-24"]["hold_expired"]["docs"] == 59
    assert folders["2026-09-25"]["published"] == {"docs": 2, "posts": 3}
    assert folders["2026-09-25"]["hold_expired"]["docs"] == 51
    assert folders["2026-09-26"]["hold_expired"]["docs"] == 16
    assert folders["2026-09-27"]["published"] == {"docs": 31, "posts": 78}
    assert folders["2026-09-27"]["hold_expired"]["docs"] == 2
    assert folders["2026-09-27"]["dropped"]["docs"] == 13
    assert folders["2026-09-28"]["published"] == {"docs": 39, "posts": 68}
    assert folders["2026-09-28"]["held"]["docs"] == 15
    assert folders["2026-09-28"]["dropped"]["docs"] == 2
    assert folders["2026-09-28"]["hold_expired"]["docs"] == 0


def test_dropped_reason_breakdown_uses_human_labels():
    md = cut_report.render_markdown(_report())
    assert "Duplicate of a published document" in md
    assert "Single-name equity research outside the watchlist and portfolio" in md
    assert "Nothing selected" in md
    assert "Verification failed" in md
    assert "Draft failed validation" in md


def test_day_assignment_hold_expired_uses_expired_at_then_updated_at():
    rows = [
        {"outcome": "dropped", "folder_date": "2026-09-24", "posts": 0,
         "reason_codes": ["NO_CANDIDATES", "HELD_EXPIRED"],
         "updated_at": "2026-09-27T18:00:00+00:00",
         "expired_at": "2026-09-28T18:00:00+00:00", "held_at": "2026-09-27T18:00:00+00:00"},
        {"outcome": "dropped", "folder_date": "2026-09-24", "posts": 0,
         "reason_codes": ["VERIFY_FAILED", "HELD_EXPIRED"],
         "updated_at": "2026-09-28T12:00:00+00:00"},
    ]
    sep28 = _report("2026-09-28", rows)
    assert sep28["buckets"]["hold_expired"]["docs"] == 2
    sep27 = _report("2026-09-27", rows)
    assert sep27["buckets"]["hold_expired"]["docs"] == 0
    assert sep27["buckets"]["published"]["docs"] == 0
    assert sep27["buckets"]["dropped"]["docs"] == 0
    assert sep27["buckets"]["held"]["docs"] == 0
    assert len(sep27["hold_expired_reviewed_this_day"]) == 1
    assert sep28["hold_expired_reviewed_this_day"] == []


def test_published_held_dropped_use_updated_at():
    rows = [
        {"outcome": "published", "folder_date": "2026-09-28", "posts": 2,
         "reason_codes": [], "updated_at": "2026-09-28T18:00:00+00:00"},
        {"outcome": "held", "folder_date": "2026-09-28", "posts": 0,
         "reason_codes": ["NO_CANDIDATES"], "updated_at": "2026-09-28T19:00:00+00:00",
         "held_at": "2026-09-28T19:00:00+00:00"},
        {"outcome": "dropped", "folder_date": "2026-09-28", "posts": 0,
         "reason_codes": ["DUPLICATE_OF_PUBLISHED"], "updated_at": "2026-09-28T20:00:00+00:00"},
        {"outcome": "published", "folder_date": "2026-09-27", "posts": 1,
         "reason_codes": [], "updated_at": "2026-09-27T18:00:00+00:00"},
    ]
    report = _report("2026-09-28", rows)
    assert report["buckets"]["published"]["docs"] == 1
    assert report["buckets"]["published"]["posts"] == 2
    assert report["buckets"]["held"]["docs"] == 1
    assert report["buckets"]["dropped"]["docs"] == 1


def test_report_header_documents_the_day_assignment_rule():
    md = cut_report.render_markdown(_report())
    assert "updated_at" in md and "expired_at" in md and "held_at" in md
    assert cut_report.DAY_ASSIGNMENT_RULE in md
    assert md.lower().count("retries exhausted") == 0


def test_default_date_is_yesterday_pt():
    now = datetime(2026, 9, 29, 7, 30, tzinfo=timezone.utc)  # 00:30 PT
    assert cut_report.default_cut_date(now) == "2026-09-28"


def test_cli_writes_json_and_markdown(tmp_path):
    json_out = tmp_path / "cut.json"
    md_out = tmp_path / "cut.md"
    cut_report.main(["--date", "2026-09-28", "--from-json", str(FIXTURE),
                     "--json-out", str(json_out), "--md-out", str(md_out)])
    payload = json.loads(json_out.read_text())
    assert payload["buckets"]["hold_expired"]["docs"] == 128
    text = md_out.read_text()
    assert "hold expired (24h TTL)" in text
    assert "retries exhausted" not in text.lower()


def test_fetch_sql_is_select_only():
    source = Path(cut_report.__file__).read_text()
    for stmt in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER "):
        assert stmt not in source
    assert "SELECT" in cut_report.CUT_SQL
    assert "held_at" in cut_report.CUT_SQL and "expired_at" in cut_report.CUT_SQL


def test_workflow_is_dispatch_only_and_sha_pinned():
    text = WORKFLOW.read_text()
    wf = yaml.load(text, Loader=yaml.BaseLoader)
    on = wf.get("on", wf.get(True, {}))
    assert set(on) == {"workflow_dispatch"}
    assert "date" in on["workflow_dispatch"]["inputs"]
    uses = [step["uses"] for job in wf["jobs"].values() for step in job["steps"] if "uses" in step]
    assert f"actions/checkout@{CHECKOUT_SHA}" in uses
    assert f"actions/setup-python@{SETUP_PYTHON_SHA}" in uses
    assert f"actions/upload-artifact@{UPLOAD_SHA}" in uses
    assert all(re.search(r"@[0-9a-f]{40}$", u) for u in uses)
    assert "TURSO_DB_URL" in text and "TURSO_AUTH_TOKEN" in text
    assert "research.cut_report" in text
    assert "research-pdf-cut.json" in text and "research-pdf-cut.md" in text
    assert "INSERT " not in text and "UPDATE " not in text

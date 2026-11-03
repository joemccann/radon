"""Clairic report dates stay off unless CLAIRIC_DATES_ENABLED=1.

Synthetic page text only. The bad-date shapes follow the 2026-10-19 eval
(sidebar year, quoted speech, legal citation, stale cover) without real PDFs.
"""
import io
import json
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError

import pytest

from research import clairic, identify, publish, triage

ROOT = Path(__file__).resolve().parents[2]
FOLDER = "2026-10-17"
TODAY = date(2026, 10, 20)
ACCOUNT = "a" * 32
TOKEN = "cf-test-token-not-a-secret"
ENDPOINT = (
    "https://api.cloudflare.com/client/v4/accounts/"
    + ACCOUNT
    + "/ai/run/@cf/cloudflare/clairic"
)
BASE_KEYS = {
    "publisher", "publisher_source", "publisher_folder", "series", "date",
    "date_source", "date_page", "date_quote", "doc_type", "doc_type_reason",
    "tickers", "publisher_page", "publisher_quote",
}


def meta(name="note.pdf", publisher_folder="berenberg"):
    path = f"/inbox/research/2026/oct/oct 17/{publisher_folder}/{name}"
    return {"name": name, "path_lower": path, "client_modified": "2026-10-17T08:15:00Z"}


def work():
    return {"key": "k" * 64, "folder_date": FOLDER, "metadata": {"id": "id:one", "name": "note.pdf"}}


class _Capture:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def __call__(self, request, timeout=None):
        self.calls.append((request, timeout))
        if self.error is not None:
            raise self.error
        raw = json.dumps(self.payload).encode()

        class _Resp:
            def read(self, n=-1):
                return raw if n < 0 else raw[:n]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return _Resp()


def _answer(value, confidence, passes=1):
    return {
        "success": True,
        "result": {
            "answers": {
                "report_date": {"value": value, "confidence": confidence, "p_null": 0.01},
                "publisher": {"value": "Not The Folder", "confidence": 0.99},
            },
            "usage": {"passes": passes, "input_tokens": 12},
        },
    }


def _arm(monkeypatch, capture, *, dates="1", sanity=None):
    monkeypatch.setenv("CLAIRIC_DATES_ENABLED", dates)
    if sanity is None:
        monkeypatch.delenv("CLAIRIC_DATE_SANITY_ENABLED", raising=False)
    else:
        monkeypatch.setenv("CLAIRIC_DATE_SANITY_ENABLED", sanity)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", TOKEN)
    monkeypatch.setattr(clairic, "today_et", lambda: TODAY)
    monkeypatch.setattr(clairic.urllib.request, "urlopen", capture)


def _off(monkeypatch, capture):
    monkeypatch.delenv("CLAIRIC_DATES_ENABLED", raising=False)
    monkeypatch.delenv("CLAIRIC_DATE_SANITY_ENABLED", raising=False)
    monkeypatch.setattr(clairic, "today_et", lambda: TODAY)
    monkeypatch.setattr(clairic.urllib.request, "urlopen", capture)


# --- eval shapes (synthetic) ----------------------------------------------

SIDEBAR = "Previous publications\n13 October 2010 Company initiation\n16 October 2026\nSector strategy"
SPEECH = "In the speech of 8 September 2025 the minister said growth had peaked.\n6 October 2026\nRussell Napier"
CITATION = "Directive (EU) 2016/43 of 7 April 2016 on markets. September 2026 index levels."
STALE_COVER = "21 June 2024\nAmundi weekly\n9 October 2026"


def test_flag_off_keeps_the_sidebar_year_and_does_not_call_clairic(monkeypatch):
    capture = _Capture(_answer("2026-10-16", 0.936))
    _off(monkeypatch, capture)
    ident = identify.identify({1: SIDEBAR}, meta(), FOLDER)
    assert ident.date == "2010-10-13"
    assert ident.date_source == "text"
    assert capture.calls == []
    assert "date_flag" not in ident.as_dict()
    assert set(ident.as_dict()) == BASE_KEYS


def test_true_is_not_on(monkeypatch):
    capture = _Capture(_answer("2026-10-16", 0.936))
    _arm(monkeypatch, capture, dates="true")
    ident = identify.identify({1: SIDEBAR}, meta(), FOLDER)
    assert ident.date == "2010-10-13"
    assert capture.calls == []


def test_sidebar_date_is_replaced_when_clairic_is_confident(monkeypatch, capsys):
    capture = _Capture(_answer("2026-10-16", 0.936))
    _arm(monkeypatch, capture)
    ident = identify.identify({1: SIDEBAR}, meta(), FOLDER)
    assert ident.date == "2026-10-16"
    assert ident.date_source == "clairic"
    assert ident.date_page is None and ident.date_quote is None
    assert ident.publisher == "Berenberg"
    assert "date_flag" not in ident.as_dict()
    request, timeout = capture.calls[0]
    assert request.full_url == ENDPOINT
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == f"Bearer {TOKEN}"
    assert timeout == clairic.TIMEOUT_S == 10
    body = json.loads(request.data.decode())
    assert body["model"] == "clairic"
    assert body["reference_date"] == FOLDER
    assert body["fields"]["report_date"]["type"] == "date"
    assert "publisher" in body["fields"]
    assert "[page 1]" in body["state"] and "13 October 2010" in body["state"]
    logged = capsys.readouterr().err
    assert TOKEN not in logged and ACCOUNT not in logged
    assert json.loads(logged.strip())["reason"] == "accepted"


def test_quoted_speech_date_is_replaced_at_the_confidence_floor(monkeypatch):
    capture = _Capture(_answer("2026-10-06", 0.9))
    _arm(monkeypatch, capture)
    ident = identify.identify({1: SPEECH}, meta("napier.pdf"), FOLDER)
    assert ident.date == "2026-10-06"
    assert ident.date_source == "clairic"


def test_confidence_just_below_the_floor_keeps_a_sane_ladder_date(monkeypatch):
    text = "16 October 2026 Sector strategy"
    capture = _Capture(_answer("2026-10-01", 0.899))
    _arm(monkeypatch, capture)
    ident = identify.identify({1: text}, meta(), FOLDER)
    assert ident.date == "2026-10-16"
    assert ident.date_source == "text"
    assert ident.date_flag == "clairic_low_confidence"
    assert ident.as_dict()["date_flag"] == "clairic_low_confidence"


def test_legal_citation_falls_back_to_the_folder_date(monkeypatch):
    """D4: low-confidence Clairic date, regex keeps the 2016 citation, sanity uses the folder."""
    capture = _Capture(_answer("2026-09-18", 0.467))
    _arm(monkeypatch, capture)
    ident = identify.identify({1: CITATION}, meta("pulse.pdf", "lseg"), FOLDER)
    assert ident.date == FOLDER
    assert ident.date_source == "folder"
    assert ident.date_flag == "stale_vs_folder"


def test_stale_cover_template_falls_back_to_the_folder_date(monkeypatch):
    capture = _Capture(_answer("2024-10-21", 0.461))
    _arm(monkeypatch, capture)
    ident = identify.identify({1: STALE_COVER}, meta("amundi.pdf"), FOLDER)
    assert ident.date == FOLDER
    assert ident.date_flag == "stale_vs_folder"
    decision, code = triage.decide(ident)
    assert (decision, code) == ("review", None)


def test_null_clairic_date_is_no_printed_date_and_keeps_the_ladder(monkeypatch):
    capture = _Capture(_answer(None, 0.99))
    _arm(monkeypatch, capture)
    ident = identify.identify({1: "16 October 2026 Sector strategy"}, meta(), FOLDER)
    assert ident.date == "2026-10-16"
    assert ident.date_source == "text"
    assert ident.date_flag == "no_printed_date"


def test_future_clairic_date_is_rejected(monkeypatch):
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    capture = _Capture(_answer(tomorrow, 0.99))
    _arm(monkeypatch, capture)
    ident = identify.identify({1: "16 October 2026 Sector strategy"}, meta(), FOLDER)
    assert ident.date == "2026-10-16"
    assert ident.date_flag == "future_date"


def test_forty_five_days_is_accepted_and_forty_six_is_stale(monkeypatch):
    folder = date.fromisoformat(FOLDER)
    fresh = (folder - timedelta(days=45)).isoformat()
    stale = (folder - timedelta(days=46)).isoformat()
    _arm(monkeypatch, _Capture(_answer(fresh, 0.95)))
    assert identify.identify({1: SIDEBAR}, meta(), FOLDER).date == fresh
    _arm(monkeypatch, _Capture(_answer(stale, 0.95)))
    ident = identify.identify({1: "16 October 2026 Sector strategy"}, meta(), FOLDER)
    assert ident.date == "2026-10-16"
    assert ident.date_flag == "stale_vs_folder"


def test_http_error_keeps_todays_ladder_and_logs_without_the_body(monkeypatch, capsys):
    error = HTTPError(ENDPOINT, 500, "err", hdrs=None, fp=io.BytesIO(TOKEN.encode()))
    capture = _Capture(error=error)
    _arm(monkeypatch, capture)
    ident = identify.identify({1: SIDEBAR}, meta(), FOLDER)
    assert ident.date == "2010-10-13"
    assert ident.date_source == "text"
    assert "date_flag" not in ident.as_dict()
    logged = capsys.readouterr().err
    assert json.loads(logged.strip())["reason"] == "http_500"
    assert TOKEN not in logged


def test_timeout_falls_back(monkeypatch, capsys):
    capture = _Capture(error=TimeoutError("slow"))
    _arm(monkeypatch, capture)
    ident = identify.identify({1: SIDEBAR}, meta(), FOLDER)
    assert ident.date == "2010-10-13"
    assert json.loads(capsys.readouterr().err.strip())["reason"] == "timeout"


def test_missing_credentials_do_not_call_and_do_not_flag(monkeypatch, capsys):
    capture = _Capture(_answer("2026-10-16", 0.99))
    _arm(monkeypatch, capture)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN")
    ident = identify.identify({1: SIDEBAR}, meta(), FOLDER)
    assert ident.date == "2010-10-13"
    assert capture.calls == []
    assert "date_flag" not in ident.as_dict()
    assert json.loads(capsys.readouterr().err.strip())["reason"] == "missing_credentials"


def test_sanity_flag_alone_rewrites_a_stale_ladder_date_without_clairic(monkeypatch):
    capture = _Capture(_answer("2026-10-16", 0.99))
    _off(monkeypatch, capture)
    monkeypatch.setenv("CLAIRIC_DATE_SANITY_ENABLED", "1")
    ident = identify.identify({1: SIDEBAR}, meta(), FOLDER)
    assert capture.calls == []
    assert ident.date == FOLDER
    assert ident.date_source == "folder"
    assert ident.date_flag == "stale_vs_folder"


def test_transport_error_does_not_apply_sanity_unless_that_flag_is_on(monkeypatch):
    capture = _Capture(error=TimeoutError("slow"))
    _arm(monkeypatch, capture, sanity="0")
    ident = identify.identify({1: SIDEBAR}, meta(), FOLDER)
    assert ident.date == "2010-10-13"
    assert "date_flag" not in ident.as_dict()
    _arm(monkeypatch, _Capture(error=TimeoutError("slow")), sanity="1")
    flagged = identify.identify({1: SIDEBAR}, meta(), FOLDER)
    assert flagged.date == FOLDER
    assert flagged.date_flag == "stale_vs_folder"


def test_state_is_pages_1_to_3_plus_the_last_page(monkeypatch):
    pages = {i: f"marker-{i}" for i in range(1, 6)}
    capture = _Capture(_answer("2026-10-16", 0.95))
    _arm(monkeypatch, capture)
    identify.identify(pages, meta(), FOLDER)
    state = json.loads(capture.calls[0][0].data.decode())["state"]
    assert "marker-1" in state and "marker-2" in state and "marker-3" in state
    assert "marker-4" not in state and "marker-5" in state


def test_publisher_is_omitted_when_it_would_exceed_one_pass():
    huge = "x" * 40_000
    body = clairic.build_request({1: huge, 5: "LASTPAGE"}, FOLDER)
    assert set(body["fields"]) == {"report_date"}
    assert "LASTPAGE" in body["state"]
    assert "[page 4]" not in body["state"]
    short = clairic.build_request({1: "16 October 2026"}, FOLDER)
    assert set(short["fields"]) == {"report_date", "publisher"}


def test_date_flag_is_on_the_outcome_row_and_does_not_hold(monkeypatch):
    capture = _Capture(_answer("2024-10-21", 0.461))
    _arm(monkeypatch, capture)
    ident = identify.identify({1: STALE_COVER}, meta(), FOLDER)
    row = publish.outcome_row(work(), {
        "pipeline": "v2", "identity": ident.as_dict(), "posts": [{"id": "research-1"}],
        "audit": [], "selection": {"candidates": []},
    })
    assert row["outcome"] == "published"
    assert json.loads(row["reason_codes"]) == []
    assert row["document_date"] == FOLDER
    assert json.loads(row["context_json"])["date_flag"] == "stale_vs_folder"
    plain = publish.outcome_row(work(), {
        "pipeline": "v2",
        "identity": {"publisher": "Goldman Sachs", "series": "tic", "doc_type": "research",
                     "date": "2026-10-16", "date_source": "text"},
        "posts": [{"id": "research-1"}], "audit": [], "selection": {"candidates": []},
    })
    assert "date_flag" not in json.loads(plain["context_json"])


def test_cloudflare_keys_stay_out_of_required_env_mcp_and_newsfeed():
    required = [
        line.strip()
        for line in (ROOT / "cloud/config/required-env.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert "CLOUDFLARE_API_TOKEN" not in required
    assert "CLOUDFLARE_ACCOUNT_ID" not in required
    deploy = (ROOT / "cloud/scripts/deploy.sh").read_text()
    assert "CLOUDFLARE_" not in deploy.split("readonly MCP_ENV_KEYS=", 1)[1].split("\n", 1)[0]
    runtime = (ROOT / "cloud/scripts/radon-app-runtime.sh").read_text()
    newsfeed = runtime.split('unit" == "radon-newsfeed.service"', 1)[1].split("fi", 1)[0]
    assert "CLOUDFLARE_" not in newsfeed
    example = (ROOT / ".env.example").read_text()
    cloud_example = (ROOT / "cloud/.env.example").read_text()
    for name in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "CLAIRIC_DATES_ENABLED", "CLAIRIC_DATE_SANITY_ENABLED"):
        assert name in example
        assert name in cloud_example


@pytest.fixture(autouse=True)
def _native_clairic_domain_boundary(monkeypatch):
    """Existing date-grounding suites inject the native provider operation."""
    from model_gateway import _clairic_backend as backend
    monkeypatch.setattr(clairic, "fetch_report_date", backend.fetch_report_date)
    # Preserve bounded native HTTP fixture capture without any consumer transport.
    monkeypatch.setattr(clairic, "urllib", backend.urllib, raising=False)

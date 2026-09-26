"""Nemotron Parse ladder: hosted adapters, whole-document local fallback, no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from research.nemotron_parse import latex_table_to_markdown, parser_mode  # noqa: E402
from tests.test_research_pdf import PARSER_AVAILABLE, make_pdf  # noqa: E402

pytestmark = pytest.mark.skipif(not PARSER_AVAILABLE, reason="Firecrawl/PDFium runtime dependencies unavailable")

LOCAL = "firecrawl/pdf-inspector"
V2 = "nvidia/nemotron-parse-2.0"
V1 = "nvidia/nemotron-parse"
SENTENCE = "Research evidence page {n}, September 7 2026"
TABLE = (
    r"\begin{tabular}{ll} Strike & Price \\ 100 & 1.25 \\ \end{tabular}"
)


class _Response:
    def __init__(self, status, payload, headers=None, text=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = text if text is not None else (
            json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload)
        )

    def json(self):
        if not isinstance(self._payload, (dict, list)):
            raise ValueError("not json")
        return self._payload


def _v2_payload(page: int, *, extra: str = "", finish: str = "stop"):
    text = (
        f"<bbox>0.1 0.2 0.8 0.3</bbox> {SENTENCE.format(n=page)} <class_Text> "
        f"<bbox>0.1 0.4 0.5 0.6</bbox> <class_Chart> {extra}"
    )
    return {"choices": [{"message": {"content": text}, "finish_reason": finish}]}


def _v1_payload(page: int, *, table: bool = False):
    blocks = [[{
        "bbox": {"xmin": 0.1, "ymin": 0.2, "xmax": 0.9, "ymax": 0.4},
        "text": SENTENCE.format(n=page),
        "type": "Text",
    }]]
    if table:
        blocks[0].append({
            "bbox": {"xmin": 0.1, "ymin": 0.5, "xmax": 0.9, "ymax": 0.8},
            "text": TABLE,
            "type": "Table",
        })
    return {"choices": [{"message": {"tool_calls": [{
        "function": {"name": "markdown_bbox", "arguments": json.dumps(blocks)},
    }]}}]}


def _scripted(script):
    calls = []

    def post(url, **kwargs):
        body = kwargs["json"]
        calls.append(body)
        model = body["model"]
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        status, payload = step(model)
        headers = {}
        if status == 429:
            headers = {"retry-after": "0"}
        text = None
        if status == 400:
            text = "DEGRADED function cannot be invoked"
            payload = {"error": text}
        return _Response(status, payload, headers=headers, text=text)

    return post, calls


def _parse(tmp_path, post, monkeypatch, pages=1):
    monkeypatch.setenv("RADON_RESEARCH_PARSER", "nemotron")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("RADON_RESEARCH_PARSE_MAX_RETRIES", "0")
    monkeypatch.delenv("RADON_RESEARCH_PARSE_MODELS", raising=False)
    from research.pdf import parse
    pdf = make_pdf(tmp_path / "source.pdf", pages=pages)
    evidence = parse(
        pdf, tmp_path / "parsed", post=post, sleep=lambda _s: None,
        jitter=lambda: 0.0, monotonic=lambda: 0.0,
    )
    return evidence


def test_parser_mode_defaults(monkeypatch):
    monkeypatch.delenv("RADON_RESEARCH_PARSER", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    assert parser_mode() == "local"
    monkeypatch.setenv("NVIDIA_API_KEY", "present")
    assert parser_mode() == "nemotron"
    monkeypatch.setenv("RADON_RESEARCH_PARSER", "local")
    assert parser_mode() == "local"


def test_v2_success_keeps_evidence_shape(tmp_path, monkeypatch):
    def ok(model):
        assert model == V2
        return 200, _v2_payload(1)
    post, calls = _scripted([ok])
    evidence = _parse(tmp_path, post, monkeypatch)
    assert evidence["parser"] == V2
    assert evidence["parser_version"] == V2
    page = evidence["pages"][0]
    for key in ("page_number", "markdown_file", "needs_ocr", "ocr_reason",
                "position_frame_rotation", "items"):
        assert key in page
    assert page["items"]
    assert any(block["type"] == "Chart" for block in page["blocks"])
    text = (tmp_path / "parsed" / "page-0001.md").read_text()
    assert "Research evidence page 1" in text
    assert "Chart" not in text
    body = calls[0]
    assert "tools" not in body
    assert body["messages"][0]["content"][0]["text"].startswith("</s><s><predict_bbox>")
    assert evidence["grounding"]["failed"] is False
    saved = json.loads((tmp_path / "parsed" / "evidence.json").read_text())
    assert saved["parser"] == V2 and "markdowns" not in saved


def _by_model(handler):
    calls = []

    def post(url, **kwargs):
        body = kwargs["json"]
        calls.append(body)
        status, payload, headers, text = handler(body["model"])
        return _Response(status, payload, headers=headers, text=text)

    return post, calls


def test_v2_degraded_and_length_fall_through_to_v1(tmp_path, monkeypatch):
    def handler(model):
        if model == V2:
            return 200, {"choices": [{"message": {"content": "<s><s>"}, "finish_reason": "length"}]}, {}, None
        return 200, _v1_payload(1, table=True), {}, None

    post, calls = _by_model(handler)
    evidence = _parse(tmp_path, post, monkeypatch)
    assert evidence["parser"] == V1
    assert calls[0]["model"] == V2 and calls[-1]["model"] == V1
    markdown = (tmp_path / "parsed" / "page-0001.md").read_text()
    assert "| Strike | Price |" in markdown
    assert "\\begin{tabular}" not in markdown


def test_v2_degraded_is_a_model_failure(tmp_path, monkeypatch):
    def handler(model):
        if model == V2:
            return 400, {"error": "DEGRADED"}, {}, "DEGRADED function cannot be invoked"
        return 200, _v1_payload(1), {}, None

    post, calls = _by_model(handler)
    evidence = _parse(tmp_path, post, monkeypatch)
    assert evidence["parser"] == V1
    assert calls[0]["model"] == V2 and "tools" not in calls[0]


def test_v1_retries_429_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setenv("RADON_RESEARCH_PARSE_MODELS", V1)
    monkeypatch.setenv("RADON_RESEARCH_PARSE_MAX_RETRIES", "2")
    sleeps = []
    remaining = {"n": 2}

    def handler(model):
        assert model == V1
        if remaining["n"]:
            remaining["n"] -= 1
            return 429, {}, {"retry-after": "0.1"}, "rate limit"
        return 200, _v1_payload(1), {}, None

    post, calls = _by_model(handler)
    from research.pdf import parse
    monkeypatch.setenv("RADON_RESEARCH_PARSER", "nemotron")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    pdf = make_pdf(tmp_path / "source.pdf", pages=1)
    evidence = parse(
        pdf, tmp_path / "parsed", post=post,
        sleep=lambda seconds: sleeps.append(seconds),
        jitter=lambda: 0.0, monotonic=lambda: 0.0,
    )
    assert evidence["parser"] == V1
    assert len(calls) == 3 and sleeps
    assert calls[-1]["tools"][0]["function"]["name"] == "markdown_bbox"


def test_hosted_failures_fall_back_for_the_whole_document(tmp_path, monkeypatch):
    def handler(model):
        if "nemotron-parse-2" in model:
            return 503, {"error": "EngineCore"}, {}, "EngineCore"
        raise TimeoutError("timed out")

    post, _calls = _by_model(handler)
    evidence = _parse(tmp_path, post, monkeypatch, pages=2)
    assert evidence["parser"] == LOCAL
    assert {row["model"] for row in evidence["fallback_attempts"]} == {V2, V1}
    for number in (1, 2):
        text = (tmp_path / "parsed" / f"page-{number:04d}.md").read_text()
        assert f"Research evidence page {number}" in text
    assert all(page.get("blocks") is None for page in evidence["pages"])


def test_malformed_v1_does_not_mix_parsers(tmp_path, monkeypatch):
    def handler(model):
        if "nemotron-parse-2" in model:
            return 502, {}, {}, "bad gateway"
        return 200, {"choices": [{"message": {"content": "not a tool call"}}]}, {}, None

    post, _calls = _by_model(handler)
    evidence = _parse(tmp_path, post, monkeypatch, pages=2)
    assert evidence["parser"] == LOCAL
    assert evidence["page_count"] == 2
    assert "malformed" in [row["reason"] for row in evidence["fallback_attempts"]]


def test_numeric_divergence_rejects_hosted_text(tmp_path, monkeypatch):
    def handler(model):
        if "nemotron-parse-2" in model:
            payload = {"choices": [{"message": {"content": "no figures here at all <class_Text>"}, "finish_reason": "stop"}]}
            return 200, payload, {}, None
        payload = {"choices": [{"message": {"tool_calls": [{"function": {
            "name": "markdown_bbox",
            "arguments": json.dumps([[{"bbox": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1},
                                      "text": "no figures here at all", "type": "Text"}]]),
        }}]}}]}
        return 200, payload, {}, None

    post, _calls = _by_model(handler)
    evidence = _parse(tmp_path, post, monkeypatch)
    assert evidence["parser"] == LOCAL
    assert evidence["fallback_reason"] == "numeric_grounding"
    assert "Research evidence page 1" in (tmp_path / "parsed" / "page-0001.md").read_text()


def test_latex_table_helper_builds_a_markdown_table():
    rendered = latex_table_to_markdown(TABLE)
    assert rendered.splitlines()[0] == "| Strike | Price |"
    assert "100" in rendered and "1.25" in rendered


def test_compare_parsers_report(tmp_path, monkeypatch):
    monkeypatch.setenv("RADON_RESEARCH_PARSER", "nemotron")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("RADON_RESEARCH_PARSE_MAX_RETRIES", "0")
    make_pdf(tmp_path / "one.pdf", pages=1)
    from research.compare_parsers import compare_folder

    def ok(model):
        return 200, _v1_payload(1, table=True) if model == V1 else (400, {})
    post, _calls = _scripted([ok, ok])
    report = compare_folder(tmp_path, tmp_path / "report.md", post=post)
    assert "numeric recall" in report
    assert V1 in report or "nemotron-parse" in report
    assert (tmp_path / "report.md").is_file()

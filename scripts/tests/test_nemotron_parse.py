"""Nemotron Parse v1 per page, local grounding, no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from research.nemotron_parse import (  # noqa: E402
    chart_candidates,
    grounding_text,
    image_derived_numbers,
    latex_table_to_markdown,
    parse_models,
    parser_mode,
    prepare_blocks,
    reset_parse_circuit,
    table_agreement,
)
from research.pipeline import date_evidence_passed, numeric_evidence_passed  # noqa: E402
from tests.test_research_pdf import PARSER_AVAILABLE, make_pdf  # noqa: E402

pytestmark = pytest.mark.skipif(not PARSER_AVAILABLE, reason="Firecrawl/PDFium runtime dependencies unavailable")

LOCAL = "firecrawl/pdf-inspector"
V2 = "nvidia/nemotron-parse-2.0"
V1 = "nvidia/nemotron-parse"
SENTENCE = "Research evidence page {n}, September 7 2026"
TABLE = r"\begin{tabular}{ll} Strike & Price \\ 100 & 1.25 \\ \end{tabular}"
LONG = "Cross asset fund flow monitor row stays duplicated across the page footer block"


@pytest.fixture(autouse=True)
def _reset_circuit():
    reset_parse_circuit()
    yield
    reset_parse_circuit()


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


def _v1_blocks(blocks):
    return {"choices": [{"finish_reason": "stop", "message": {"tool_calls": [{
        "function": {"name": "markdown_bbox", "arguments": json.dumps([blocks])},
    }]}}]}


def _v1_payload(page: int, *, table: bool = False, extra=None):
    blocks = [{
        "bbox": {"xmin": 0.1, "ymin": 0.2, "xmax": 0.9, "ymax": 0.4},
        "text": SENTENCE.format(n=page),
        "type": "Text",
    }]
    if table:
        blocks.append({
            "bbox": {"xmin": 0.1, "ymin": 0.5, "xmax": 0.9, "ymax": 0.8},
            "text": TABLE,
            "type": "Table",
        })
    if extra:
        blocks.extend(extra)
    return _v1_blocks(blocks)


def _by_model(handler):
    calls = []

    def post(url, **kwargs):
        body = kwargs["json"]
        calls.append(body)
        status, payload, headers, text = handler(body["model"])
        return _Response(status, payload, headers=headers, text=text)

    return post, calls


def _parse(tmp_path, post, monkeypatch, pages=1, *, selective="0"):
    monkeypatch.setenv("RADON_RESEARCH_PARSER", "nemotron")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("RADON_RESEARCH_PARSE_MAX_RETRIES", "0")
    monkeypatch.setenv("RADON_RESEARCH_PARSE_CONCURRENCY", "1")
    monkeypatch.setenv("RADON_RESEARCH_PARSE_SELECTIVE", selective)
    from research.pdf import parse
    pdf = make_pdf(tmp_path / "source.pdf", pages=pages)
    evidence = parse(
        pdf, tmp_path / "parsed", post=post, sleep=lambda _s: None,
        jitter=lambda: 0.0, monotonic=lambda: 0.0,
    )
    return evidence


def _ok_v1(model):
    assert model == V1
    return 200, _v1_payload(1), {}, None


def test_parser_mode_defaults(monkeypatch):
    monkeypatch.delenv("RADON_RESEARCH_PARSER", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    assert parser_mode() == "local"
    monkeypatch.setenv("NVIDIA_API_KEY", "present")
    assert parser_mode() == "nemotron"
    monkeypatch.setenv("RADON_RESEARCH_PARSER", "local")
    assert parser_mode() == "local"


def test_default_ladder_is_v1_only(monkeypatch):
    monkeypatch.delenv("RADON_RESEARCH_PARSE_MODELS", raising=False)
    assert parse_models() == (V1,)
    monkeypatch.setenv("RADON_RESEARCH_PARSE_MODELS", f"{V2},{V1}")
    assert parse_models() == (V2, V1)


def test_v1_keeps_local_grounding_and_evidence_shape(tmp_path, monkeypatch):
    post, calls = _by_model(_ok_v1)
    evidence = _parse(tmp_path, post, monkeypatch)
    assert evidence["parser"] == f"{V1}+{LOCAL}"
    assert evidence["parser_version"] == V1
    page = evidence["pages"][0]
    for key in ("page_number", "markdown_file", "needs_ocr", "ocr_reason",
                "position_frame_rotation", "items", "sources"):
        assert key in page
    assert page["sources"] == ["local", "nemotron"]
    text = (tmp_path / "parsed" / "page-0001.md").read_text()
    assert text.index("Research evidence page 1") < text.index("<!-- image-derived -->")
    assert grounding_text(text).strip().startswith("Research evidence")
    assert "<!-- image-derived -->" not in grounding_text(text)
    body = calls[0]
    assert body["model"] == V1
    assert body["tools"][0]["function"]["name"] == "markdown_bbox"
    assert "tools" in body and not body["messages"][0]["content"][0].get("text", "").startswith("</s>")
    saved = json.loads((tmp_path / "parsed" / "evidence.json").read_text())
    assert saved["pages"][0]["sources"] == ["local", "nemotron"]
    assert "markdowns" not in saved


def test_text_layer_pages_skip_nemotron(tmp_path, monkeypatch):
    def boom(model):
        raise AssertionError(model)

    post, calls = _by_model(boom)
    evidence = _parse(tmp_path, post, monkeypatch, pages=2, selective="1")
    assert calls == []
    assert evidence["parser"] == LOCAL
    assert [page["sources"] for page in evidence["pages"]] == [["local"], ["local"]]


def test_ocr_page_calls_v1_and_marks_image_numbers(tmp_path, monkeypatch):
    from research import pdf as pdfmod

    original = pdfmod.parse_local

    def scanned(path):
        result = original(path)
        result["pages"][0]["needs_ocr"] = True
        result["pages"][0]["ocr_reason"] = "scanned"
        result["markdowns"][1] = ""
        return result

    monkeypatch.setattr(pdfmod, "parse_local", scanned)

    def handler(model):
        assert model == V1
        blocks = [{
            "bbox": {"xmin": 0.1, "ymin": 0.2, "xmax": 0.8, "ymax": 0.5},
            "text": "Gross output 12.3",
            "type": "Text",
        }, {
            "bbox": {"xmin": 0.1, "ymin": 0.55, "xmax": 0.8, "ymax": 0.9},
            "text": "Hours worked 8.1",
            "type": "Table",
        }]
        return 200, _v1_blocks(blocks), {}, None

    post, calls = _by_model(handler)
    evidence = _parse(tmp_path, post, monkeypatch, selective="1")
    assert len(calls) == 1 and calls[0]["model"] == V1
    page = evidence["pages"][0]
    assert page["sources"] == ["local", "nemotron"]
    assert page["needs_ocr"] is True
    assert "12.3" in page["image_derived_numbers"]
    text = (tmp_path / "parsed" / "page-0001.md").read_text()
    assert "12.3" in text
    assert "12.3" not in grounding_text(text)


def test_v2_is_opt_in_and_empty_200_falls_through_per_page(tmp_path, monkeypatch):
    monkeypatch.setenv("RADON_RESEARCH_PARSE_MODELS", f"{V2},{V1}")

    def handler(model):
        if model == V2:
            return 200, {"choices": [{"message": {"content": "<s><s>"}, "finish_reason": "length"}]}, {}, None
        return 200, _v1_payload(1), {}, None

    post, calls = _by_model(handler)
    evidence = _parse(tmp_path, post, monkeypatch)
    assert [body["model"] for body in calls] == [V2, V1]
    assert evidence["pages"][0]["sources"] == ["local", "nemotron"]
    assert evidence["pages"][0]["model"] == V1
    assert "tools" not in calls[0]


def test_page_failures_stay_on_that_page(tmp_path, monkeypatch):
    def handler(model):
        page = len([1 for _ in handler.seen])
        handler.seen.append(model)
        if page == 0:
            return 503, {"error": "EngineCore"}, {}, "EngineCore"
        return 200, _v1_payload(2), {}, None

    handler.seen = []
    post, calls = _by_model(handler)
    evidence = _parse(tmp_path, post, monkeypatch, pages=2)
    assert evidence["pages"][0]["sources"] == ["local"]
    assert evidence["pages"][0]["fallback_reason"].startswith("http_5")
    assert evidence["pages"][1]["sources"] == ["local", "nemotron"]
    assert evidence["parser"] == f"{V1}+{LOCAL}"
    assert "Research evidence page 1" in (tmp_path / "parsed" / "page-0001.md").read_text()
    assert "<!-- image-derived -->" not in (tmp_path / "parsed" / "page-0001.md").read_text()
    assert len(calls) == 2


def test_length_empty_degraded_and_timeout_are_local_only(tmp_path, monkeypatch):
    cases = [
        (200, {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}, {}, None, "length"),
        (400, {"error": "DEGRADED"}, {}, "DEGRADED function cannot be invoked", "degraded"),
    ]
    for status, payload, headers, text, reason in cases:
        reset_parse_circuit()

        def handler(model, status=status, payload=payload, headers=headers, text=text):
            return status, payload, headers, text

        post, _calls = _by_model(handler)
        evidence = _parse(tmp_path, post, monkeypatch)
        assert evidence["pages"][0]["sources"] == ["local"]
        assert evidence["pages"][0]["fallback_reason"] == reason
        assert evidence["parser"] == LOCAL

    def timed_out(model):
        raise TimeoutError("timed out")

    post, _calls = _by_model(timed_out)
    evidence = _parse(tmp_path, post, monkeypatch)
    assert evidence["pages"][0]["fallback_reason"] == "timeout"
    assert evidence["pages"][0]["sources"] == ["local"]


def test_v1_retries_429_then_succeeds(tmp_path, monkeypatch):
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
    monkeypatch.setenv("RADON_RESEARCH_PARSE_SELECTIVE", "0")
    monkeypatch.setenv("RADON_RESEARCH_PARSE_CONCURRENCY", "1")
    pdf = make_pdf(tmp_path / "source.pdf", pages=1)
    evidence = parse(
        pdf, tmp_path / "parsed", post=post,
        sleep=lambda seconds: sleeps.append(seconds),
        jitter=lambda: 0.0, monotonic=lambda: 0.0,
    )
    assert evidence["pages"][0]["sources"] == ["local", "nemotron"]
    assert len(calls) == 3 and sleeps
    assert calls[-1]["tools"][0]["function"]["name"] == "markdown_bbox"


def test_table_agreement_below_90_percent_is_local_only(tmp_path, monkeypatch):
    def handler(model):
        return 200, _v1_payload(1, table=True), {}, None

    post, _calls = _by_model(handler)
    evidence = _parse(tmp_path, post, monkeypatch)
    assert evidence["pages"][0]["sources"] == ["local"]
    assert evidence["pages"][0]["fallback_reason"] == "table_agreement"
    text = (tmp_path / "parsed" / "page-0001.md").read_text()
    assert "100" not in grounding_text(text)
    assert "<!-- image-derived -->" not in text


def test_table_agreement_keeps_structured_table_out_of_the_gate(tmp_path, monkeypatch):
    def handler(model):
        blocks = [{
            "bbox": {"xmin": 0.1, "ymin": 0.2, "xmax": 0.9, "ymax": 0.6},
            "text": r"\begin{tabular}{ll} Month & Year \\ September & 7 \\ Note & 2026 \\ \end{tabular}",
            "type": "Table",
        }]
        return 200, _v1_blocks(blocks), {}, None

    post, _calls = _by_model(handler)
    evidence = _parse(tmp_path, post, monkeypatch)
    assert evidence["pages"][0]["sources"] == ["local", "nemotron"]
    assert evidence["pages"][0]["table_agreement"] >= 0.9
    text = (tmp_path / "parsed" / "page-0001.md").read_text()
    assert "| September | 7 |" in text
    assert "Research evidence page 1" in grounding_text(text)


def test_duplicated_blocks_fall_back(tmp_path, monkeypatch):
    def handler(model):
        blocks = [
            {"bbox": {"xmin": 0.1, "ymin": 0.2, "xmax": 0.9, "ymax": 0.4}, "text": LONG, "type": "Text"},
            {"bbox": {"xmin": 0.1, "ymin": 0.5, "xmax": 0.9, "ymax": 0.7}, "text": LONG, "type": "Text"},
        ]
        return 200, _v1_blocks(blocks), {}, None

    post, _calls = _by_model(handler)
    evidence = _parse(tmp_path, post, monkeypatch)
    assert evidence["pages"][0]["fallback_reason"] == "duplicated"
    assert evidence["pages"][0]["sources"] == ["local"]
    assert LONG not in (tmp_path / "parsed" / "page-0001.md").read_text()


def test_blocks_are_sorted_and_zero_area_pictures_dropped():
    blocks, problem = prepare_blocks([
        {"type": "Page-footer", "text": "Footer after the table", "bbox": {"xmin": 0.1, "ymin": 0.92, "xmax": 0.9, "ymax": 0.98}},
        {"type": "Picture", "text": "logo", "bbox": {"xmin": 0.1, "ymin": 0.1, "xmax": 0.1, "ymax": 0.1}},
        {"type": "Table", "text": "Strike 7", "bbox": {"xmin": 0.1, "ymin": 0.2, "xmax": 0.8, "ymax": 0.5}},
        {"type": "Text", "text": "Source", "bbox": {"xmin": 0.1, "ymin": 0.6, "xmax": 0.4, "ymax": 0.7}},
        {"type": "Text", "text": "Source", "bbox": {"xmin": 0.1, "ymin": 0.6, "xmax": 0.4, "ymax": 0.7}},
    ])
    assert problem is None
    assert [block["type"] for block in blocks] == ["Table", "Text", "Page-footer"]
    assert chart_candidates(blocks, 3) == []


def test_picture_boxes_are_chart_candidates():
    blocks = [{"type": "Picture", "text": "axis 1.2", "bbox": {"xmin": 0.1, "ymin": 0.2, "xmax": 0.8, "ymax": 0.7}}]
    found = chart_candidates(blocks, 4)
    assert found[0]["page"] == 4
    assert found[0]["origin"] == "nemotron"
    assert found[0]["bbox"] == [0.1, 0.2, 0.8, 0.7]
    assert found[0]["kind"] == "raster"


def test_image_only_numbers_do_not_pass_the_verbatim_gate():
    local = "Research evidence page 1, September 7 2026"
    hosted = local + "\n\n<!-- image-derived -->\nGross output 12.3\n<!-- /image-derived -->\n"
    assert image_derived_numbers(local, "Gross output 12.3") == ["12.3"]
    assert "12.3" not in grounding_text(hosted)
    value = {"title": "Output", "content": "Gross output 12.3", "pages": [1], "figures": []}
    verdict = {"numeric_checks": [{
        "proposal_quote": "12.3", "source_quote": "Gross output 12.3", "page": 1, "supported": True,
    }]}
    assert numeric_evidence_passed(value, verdict, {1: hosted}) is False
    assert numeric_evidence_passed(value, verdict, {1: "Gross output 12.3"}) is True
    dated = {
        "document_date": "2026-09-07", "pages": [1],
    }
    evidence = {"date_evidence": {
        "page": 1, "date_text": "7 September 2026", "source_quote": "Printed 7 September 2026",
        "role": "report", "role_verified": True,
    }}
    fenced = "<!-- image-derived -->\nPrinted 7 September 2026\n<!-- /image-derived -->\n"
    assert date_evidence_passed(dated, evidence, {1: fenced}) is False
    assert date_evidence_passed(dated, evidence, {1: "Printed 7 September 2026"}) is True
    from research import ground
    assert ground.ground(["Gross output 12.3"], {1: hosted}, [1])["passed"] is False
    assert ground.ground(["September 7 2026"], {1: hosted}, [1])["passed"] is True


def test_circuit_breaker_parks_after_consecutive_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("RADON_RESEARCH_PARSE_FAIL_LIMIT", "2")

    def handler(model):
        return 503, {"error": "down"}, {}, "down"

    post, calls = _by_model(handler)
    evidence = _parse(tmp_path, post, monkeypatch, pages=3)
    assert len(calls) == 2
    assert evidence["pages"][2]["fallback_reason"] == "circuit"
    assert all(page["sources"] == ["local"] for page in evidence["pages"])


def test_success_resets_the_circuit(tmp_path, monkeypatch):
    monkeypatch.setenv("RADON_RESEARCH_PARSE_FAIL_LIMIT", "2")
    state = {"n": 0}

    def handler(model):
        state["n"] += 1
        if state["n"] == 2:
            return 200, _v1_payload(2), {}, None
        return 500, {"error": "down"}, {}, "down"

    post, calls = _by_model(handler)
    evidence = _parse(tmp_path, post, monkeypatch, pages=3)
    assert len(calls) == 3
    assert evidence["pages"][1]["sources"] == ["local", "nemotron"]
    assert evidence["pages"][0]["sources"] == ["local"]
    assert evidence["pages"][2]["fallback_reason"].startswith("http_5")


def test_latex_table_helper_builds_a_markdown_table():
    rendered = latex_table_to_markdown(TABLE)
    assert rendered.splitlines()[0] == "| Strike | Price |"
    assert "100" in rendered and "1.25" in rendered
    assert table_agreement("only 7 and 2026", [{"type": "Table", "text": TABLE}]) < 0.9


def test_compare_parsers_report(tmp_path, monkeypatch):
    monkeypatch.setenv("RADON_RESEARCH_PARSER", "nemotron")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("RADON_RESEARCH_PARSE_MAX_RETRIES", "0")
    monkeypatch.setenv("RADON_RESEARCH_PARSE_SELECTIVE", "1")
    make_pdf(tmp_path / "one.pdf", pages=1)
    from research.compare_parsers import compare_folder

    def ok(model):
        if model == V1:
            return 200, _v1_payload(1), {}, None
        return 400, {}, {}, "DEGRADED"

    post, calls = _by_model(ok)
    report = compare_folder(tmp_path, tmp_path / "report.md", post=post)
    assert "numeric recall" in report
    assert V1 in report or "nemotron-parse" in report
    assert calls and calls[0]["model"] == V1
    assert (tmp_path / "report.md").is_file()


def test_catalogue_renders_nemotron_chart_candidates(tmp_path):
    from research.figures import catalogue
    pdf = make_pdf(tmp_path / "source.pdf", pages=1)
    extras = chart_candidates([
        {"type": "Picture", "text": "Bloomberg chart", "bbox": {"xmin": 0.0, "ymin": 0.0, "xmax": 0.15, "ymax": 0.15}},
    ], 1)
    found = catalogue(pdf, [1], tmp_path / "out", dpi=72, extras=extras)
    assert any(row.get("origin") == "nemotron" for row in found)
    nemotron = next(row for row in found if row.get("origin") == "nemotron")
    png = tmp_path / "out" / nemotron["image_file"]
    assert png.is_file() and png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

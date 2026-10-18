"""PNG export contract and visual geometry regressions for grounded chart plans."""
import json
import struct
import signal
import subprocess

import pytest

from research.chart_render import ChartRenderError, publisher_label, render_chart, validate_plan
from research import chart_render


def inflation_plan():
    return {"kind": "bar", "title": "Japan consumer inflation expectations", "unit": "%",
            "temporal": True, "axis": [0, 5], "ticks": [0, 1, 2, 3, 4, 5],
            "bars": [{"id": "aug", "label": "August 2026", "value": 4.15},
                     {"id": "sep", "label": "September 2026", "value": 4.35}],
            "note": "Only the two reported months are shown."}


@pytest.mark.parametrize("value", [None, "", "unknown", " UNKNOWN ", "unverified", "n/a", "none", "unavailable"])
def test_unverified_publisher_is_named_as_source_document(value):
    assert publisher_label(value) == "Source PDF"


def test_verified_publisher_is_preserved():
    assert publisher_label(" LSEG ") == "LSEG"


# Shipped 2026-10-18: a three-line model note ran into the source footer.
VERBOSE_NOTE = ("Sorted by value descending. Returns are YTD as of the week ending 17 Oct 2026. Desk notes PnL "
                "'remains positive but has moderated' and tolerance for further losses has diminished. Source: Morgan "
                "Stanley PB Content Team via Nick Savone, 17 Oct 2026.")


@pytest.mark.parametrize("field", ["note", "sourceNote"])
def test_rejects_chart_note_longer_than_one_footer_line(field):
    plan = inflation_plan()
    plan.pop("note")
    plan[field] = VERBOSE_NOTE
    with pytest.raises(ChartRenderError, match="brief"):
        validate_plan(plan)


def test_note_and_source_note_share_one_footer_line():
    plan = inflation_plan()
    plan["note"] = "x" * 60
    plan["sourceNote"] = "y" * 60
    with pytest.raises(ChartRenderError, match="brief"):
        validate_plan(plan)
    plan["sourceNote"] = "y" * 39
    validate_plan(plan)


def test_rejects_misleading_nonzero_bar_axis():
    plan = inflation_plan()
    plan["axis"] = [4, 4.5]
    with pytest.raises(ChartRenderError, match="zero"):
        validate_plan(plan)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "4.35", True])
def test_rejects_nonnumeric_or_nonfinite_observations(value):
    plan = inflation_plan()
    plan["bars"][1]["value"] = value
    with pytest.raises(ChartRenderError):
        validate_plan(plan)


def test_rejects_out_of_domain_observations_and_duplicate_labels():
    plan = inflation_plan()
    plan["bars"][1]["value"] = 9
    with pytest.raises(ChartRenderError, match="domain"):
        validate_plan(plan)
    plan = inflation_plan()
    plan["bars"][1]["label"] = plan["bars"][0]["label"]
    with pytest.raises(ChartRenderError, match="unique"):
        validate_plan(plan)


def test_rejects_unbounded_or_empty_plans():
    plan = inflation_plan()
    plan["bars"] = []
    with pytest.raises(ChartRenderError):
        validate_plan(plan)
    plan["bars"] = [{"label": str(n), "value": 1} for n in range(41)]
    with pytest.raises(ChartRenderError):
        validate_plan(plan)


@pytest.mark.parametrize("change", [
    {"kind": "pie"}, {"title": ""}, {"axis": [6, 0]}, {"axis": [0]},
    {"unit": ""}, {"reference": {"value": 7, "label": "Target"}},
    {"reference": {"value": 3}}, {"metric": {"untrusted": "object"}},
])
def test_invalid_shape_never_starts_a_renderer(change, tmp_path, monkeypatch):
    plan = {**inflation_plan(), **change}
    monkeypatch.setattr(chart_render.subprocess, "Popen", lambda *a, **k: pytest.fail("Invalid chart started a subprocess"))
    with pytest.raises(ChartRenderError):
        render_chart(plan, tmp_path / "invalid.png")
    assert not (tmp_path / "invalid.png").exists()


def test_timeout_kills_entire_renderer_process_group(tmp_path, monkeypatch):
    class Hung:
        pid = 321
        returncode = None
        calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if timeout:
                raise subprocess.TimeoutExpired("renderer", timeout)
            return "", ""

    process = Hung()
    killed = []
    monkeypatch.setattr(chart_render.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(chart_render.shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(chart_render.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(ChartRenderError, match="timed out"):
        render_chart(inflation_plan(), tmp_path / "hung.png")
    assert killed == [(321, signal.SIGKILL)]
    assert process.calls == 2
    assert not (tmp_path / "hung.png").exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("timestamps", [(10, 10), (20, 10), (0, 8.65e15), (0, None)])
def test_refuses_invalid_chronology_before_browser_work(timestamps, tmp_path, monkeypatch):
    plan = inflation_plan()
    plan.update(kind="line", points=[{**row, "timestamp": timestamp}
                                     for row, timestamp in zip(plan.pop("bars"), timestamps)])
    monkeypatch.setattr(chart_render.subprocess, "Popen", lambda *a, **k: pytest.fail("Invalid chronology launched renderer"))
    with pytest.raises(ChartRenderError):
        render_chart(plan, tmp_path / "dates.png")


def test_reversed_range_is_refused_instead_of_drawing_a_negative_interval():
    plan = {"kind": "range", "title": "Coupon ranges", "unit": "%", "axis": [0, 6],
            "marks": [{"label": "Issuer", "low": 4, "high": 3}]}
    with pytest.raises(ChartRenderError, match="endpoints"):
        validate_plan(plan)


def test_missing_runtime_preserves_previous_good_export(tmp_path, monkeypatch):
    output = tmp_path / "good.png"
    output.write_bytes(b"previous approved export")
    monkeypatch.setattr(chart_render.shutil, "which", lambda _: None)
    with pytest.raises(ChartRenderError, match="Node runtime"):
        render_chart(inflation_plan(), output)
    assert output.read_bytes() == b"previous approved export"


def test_failed_renderer_preserves_previous_good_export_and_cleans_temporary_files(tmp_path, monkeypatch):
    class Failed:
        returncode = 1

        def communicate(self, timeout=None):
            return "", "Pinned font payload is missing"

    output = tmp_path / "good.png"
    output.write_bytes(b"previous approved export")
    monkeypatch.setattr(chart_render.shutil, "which", lambda _: "/usr/bin/node")
    monkeypatch.setattr(chart_render.subprocess, "Popen", lambda *a, **k: Failed())
    with pytest.raises(ChartRenderError, match="font payload is missing"):
        render_chart(inflation_plan(), output)
    assert output.read_bytes() == b"previous approved export"
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.integration
def test_temporal_bars_export_crisp_png_and_honest_geometry(tmp_path):
    result = render_chart(inflation_plan(), tmp_path / "inflation.png", publisher="Goldman Sachs",
                          document_date="2026-10-09", pages=[4, 5])
    raw = (tmp_path / "inflation.png").read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", raw[16:24]) == (2400, result["height"])
    assert result["renderer"] == "d3-svg-chromium"
    bars = result["geometry"]["marks"]
    assert len(bars) == 2
    assert bars[0]["x"] < bars[1]["x"]
    assert bars[1]["height"] / bars[0]["height"] == pytest.approx(4.35 / 4.15, rel=0.002)
    assert result["geometry"]["text_overflow"] == []
    assert "4.35%" in result["visible_text"]
    assert "September 2026" in result["visible_text"]
    assert "Goldman Sachs" in " ".join(result["visible_text"])
    assert "pp. 4, 5" in " ".join(result["visible_text"])
    assert "https://radon.run" in result["visible_text"]
    svg = (tmp_path / "inflation.svg").read_text()
    assert "data:font/woff2;base64," in svg


@pytest.mark.integration
def test_document_text_is_escaped_and_cannot_execute(tmp_path):
    plan = inflation_plan()
    plan["title"] = '<script>throw new Error("untrusted")</script>'
    result = render_chart(plan, tmp_path / "safe.png")
    assert plan["title"] in " ".join(result["visible_text"])
    assert "&lt;script&gt;" in (tmp_path / "safe.svg").read_text()


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["bar", "line", "range", "scatter"])
def test_each_chart_family_renders_readable_labels(kind, tmp_path):
    common = {"kind": kind, "title": "Source measurement", "unit": "%", "axis": [0, 6],
              "ticks": [0, 2, 4, 6]}
    if kind == "bar":
        common["bars"] = [{"label": "Entity A", "value": 3}, {"label": "Entity B", "value": -1}]
        common["axis"] = [-2, 6]
    elif kind == "line":
        common["points"] = [{"label": "January 2026", "value": 2}, {"label": "June 2026", "value": 4}]
    elif kind == "range":
        common["marks"] = [{"label": "Entity A", "low": 3, "high": 4}, {"label": "Entity B", "low": 2, "high": 2}]
    else:
        common.update(xUnit="years", yUnit="%", xAxis=[0, 10], yAxis=[0, 6], xTicks=[0, 5, 10], yTicks=[0, 2, 4, 6],
                      points=[{"label": "Entity A", "x": 2, "y": 3}, {"label": "Entity B", "x": 6, "y": 4}])
    result = render_chart(common, tmp_path / f"{kind}.png")
    assert result["geometry"]["text_overflow"] == []
    assert len(result["geometry"]["marks"]) == 2
    assert "Entity A" in " ".join(result["visible_text"]) if kind != "line" else "January 2026" in result["visible_text"]


@pytest.mark.integration
def test_actual_line_timestamps_preserve_unequal_spacing(tmp_path):
    plan = {"kind": "line", "title": "Reported quarterly observations", "metric": "Share", "unit": "%",
            "axis": [0, 10], "points": [
                {"label": "Q1 2026", "value": 2, "timestamp": 1767225600000},
                {"label": "Q2 2026", "value": 4, "timestamp": 1775001600000},
                {"label": "Q4 2026", "value": 6, "timestamp": 1790812800000}]}
    result = render_chart(plan, tmp_path / "irregular.png")
    marks = result["geometry"]["marks"]
    short_gap = marks[1]["x"] - marks[0]["x"]
    long_gap = marks[2]["x"] - marks[1]["x"]
    assert long_gap / short_gap == pytest.approx(183 / 90, rel=.002)
    assert "Share (%)" in result["visible_text"]


@pytest.mark.integration
def test_horizontal_axis_title_has_clear_gap_above_footnote(tmp_path):
    note = "ING forecast. September trade data due the following week."
    plan = {"kind": "bar", "title": "China September trade growth forecast", "metric": "Year-on-year trade growth",
            "unit": "%", "axis": [0, 30], "orientation": "horizontal", "note": note,
            "bars": [{"label": "Exports", "value": 24.8}, {"label": "Imports", "value": 21.7}]}
    result = render_chart(plan, tmp_path / "forecast.png")
    bounds = {row["text"]: row for row in result["geometry"]["text_bounds"]}
    axis = bounds["Year-on-year trade growth (%)"]
    assert bounds[note]["y"] - (axis["y"] + axis["height"]) >= 10


@pytest.mark.integration
def test_clipped_annotation_fails_before_overwriting_previous_export(tmp_path):
    plan = inflation_plan()
    plan["subtitle"] = "Unbroken-source-label-" * 20
    output = tmp_path / "approved.png"
    output.write_bytes(b"previous approved export")
    with pytest.raises(ChartRenderError, match="overflow"):
        render_chart(plan, output)
    assert output.read_bytes() == b"previous approved export"
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.integration
@pytest.mark.parametrize("publisher", ["unknown", "", "Verified research publisher with a long legal organization name and a lengthy department attribution"])
def test_footer_preserves_attribution_and_keeps_radon_url_clear(publisher, tmp_path):
    result = render_chart(inflation_plan(), tmp_path / "footer.png", publisher=publisher, document_date="2026-10-09", pages=[4, 5])
    visible = " ".join(result["visible_text"])
    assert "https://radon.run" in result["visible_text"]
    assert "Source: unknown" not in visible
    assert ("Source PDF" if publisher in ("unknown", "") else publisher) in visible
    assert "2026-10-09" in visible and "pp. 4, 5" in visible
    url = next(row for row in result["geometry"]["text_bounds"] if row["text"] == "https://radon.run")
    for row in result["geometry"]["text_bounds"]:
        if row["text"] == "https://radon.run":
            continue
        assert (row["x"]+row["width"] <= url["x"] or row["x"] >= url["x"]+url["width"]
                or row["y"]+row["height"] <= url["y"] or row["y"] >= url["y"]+url["height"])


@pytest.mark.integration
def test_direct_node_renderer_does_not_reintroduce_unknown_attribution(tmp_path):
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"plan": inflation_plan(), "publisher": "unknown", "documentDate": "2026-10-09", "pages": [4, 5]}))
    result = subprocess.run(["node", str(chart_render.Path(chart_render.__file__).with_suffix(".mjs")), str(request)],
                            capture_output=True, text=True, timeout=15, check=True)
    svg = json.loads(result.stdout)["svg"]
    assert "Source: Source PDF" in svg
    assert "Source: unknown" not in svg
    assert "https://radon.run" in svg

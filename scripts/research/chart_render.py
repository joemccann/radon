"""Bounded D3 SVG export and offline Chromium rasterization of grounded plans.

The planner supplies measurements; this module accepts only fixed chart shapes,
never source-authored JavaScript or model-authored drawing instructions.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile


# The SVG wraps notes at 100 characters; a second line collides with the source footer.
NOTE_CHARS = 100


class ChartRenderError(ValueError):
    """A chart cannot be exported without violating the rendering contract."""


def publisher_label(value) -> str:
    """Name verified attribution or identify the source document honestly."""
    label = str(value or "").strip()
    if label.lower() in {"", "unknown", "unknown publisher", "unverified", "n/a", "none", "null", "unavailable"}:
        return "Source PDF"
    return label[:180]


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ChartRenderError("Chart measurements must be finite numbers")
    return value


def _domain(plan, key):
    value = plan.get(key)
    if not isinstance(value, list) or len(value) != 2:
        raise ChartRenderError(f"Missing {key} domain")
    low, high = map(_number, value)
    if low >= high:
        raise ChartRenderError("Chart domain must increase")
    return low, high


def validate_plan(plan: dict) -> None:
    """Reject malformed values and geometries before starting a browser."""
    if not isinstance(plan, dict) or plan.get("kind") not in {"bar", "line", "range", "scatter"}:
        raise ChartRenderError("Unsupported chart kind")
    if not isinstance(plan.get("title"), str) or not plan["title"].strip() or len(plan["title"]) > 500:
        raise ChartRenderError("Chart requires a bounded descriptive title")
    kind = plan["kind"]
    key = "bars" if kind == "bar" else "marks" if kind == "range" else "points"
    rows = plan.get(key)
    minimum = 1 if kind == "range" else 2
    if not isinstance(rows, list) or not minimum <= len(rows) <= 40:
        raise ChartRenderError(f"Charts require {minimum} to 40 observations")
    labels = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("label"), str) or not row["label"].strip() or len(row["label"]) > 180:
            raise ChartRenderError("Each observation requires a bounded label")
        labels.append(row["label"])
    if len(set(labels)) != len(labels):
        raise ChartRenderError("Observation labels must be unique")
    if kind == "scatter":
        domains = {"x": _domain(plan, "xAxis"), "y": _domain(plan, "yAxis")}
        fields = ["x", "y"]
        units = ["xUnit", "yUnit"]
    else:
        axis = _domain(plan, "axis")
        if kind == "bar" and not axis[0] <= 0 <= axis[1]:
            raise ChartRenderError("Bar axes must include zero")
        fields = ["low", "high"] if kind == "range" else ["value"]
        domains = dict.fromkeys(fields, axis)
        units = ["unit"]
    if kind == "line" and any("timestamp" in row for row in rows):
        dates = [_number(row.get("timestamp")) for row in rows]
        if any(abs(value) > 8.64e15 for value in dates) or any(a >= b for a, b in zip(dates, dates[1:])):
            raise ChartRenderError("Line timestamps must increase within the UTC date domain")
    for field in units:
        if not isinstance(plan.get(field), str) or not plan[field].strip() or len(plan[field]) > 80:
            raise ChartRenderError("Axes require explicit bounded units")
    for row in rows:
        for field in fields:
            value = _number(row.get(field))
            low, high = domains[field]
            if not low <= value <= high:
                raise ChartRenderError("Observation lies outside the chart domain")
        if kind == "range" and row["low"] > row["high"]:
            raise ChartRenderError("Range endpoints must increase")
    reference = plan.get("reference")
    if reference:
        if not isinstance(reference, dict) or not isinstance(reference.get("label"), str) or len(reference["label"]) > 180:
            raise ChartRenderError("Reference requires a bounded text label")
        value = _number(reference.get("value"))
        low, high = _domain(plan, "axis")
        if not low <= value <= high:
            raise ChartRenderError("Reference lies outside the chart domain")
    for field in ("subtitle", "note", "sourceNote", "xLabel", "yLabel", "metric"):
        if field in plan and (not isinstance(plan[field], str) or len(plan[field]) > 1000):
            raise ChartRenderError("Chart annotations must be bounded text")
    if len(" ".join(plan[field] for field in ("note", "sourceNote") if plan.get(field))) > NOTE_CHARS:
        raise ChartRenderError("Chart notes must stay one brief footer line")


def _rasterize(svg: str, output: Path, width: int, height: int) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=2)
            page.set_default_timeout(15_000)
            page.route("**/*", lambda route: route.abort())
            page.set_content('<html><head><meta charset="utf-8"></head><body style="margin:0">' + svg + '</body></html>')
            page.evaluate("document.fonts.ready")
            geometry = page.evaluate("""() => {
                const svg = document.querySelector('svg');
                const overflow = [...svg.querySelectorAll('text')].flatMap(node => {
                    const b = node.getBoundingClientRect();
                    return b.left < -1 || b.right > svg.width.baseVal.value + 1 || b.top < -1 || b.bottom > svg.height.baseVal.value + 1 ? [node.textContent] : [];
                });
                return {text_overflow: overflow,
                    text_bounds: [...svg.querySelectorAll('text')].map(node => {
                        const b = node.getBoundingClientRect();
                        return {text: node.textContent, x:b.x, y:b.y, width:b.width, height:b.height};
                    }),
                    marks: [...svg.querySelectorAll('[data-mark]')].map(node => {
                        const b = node.getBBox();
                        return {label: node.getAttribute('data-label'), value: node.getAttribute('data-value'),
                                x:b.x, y:b.y, width:b.width, height:b.height};
                    }), visible_text:[...svg.querySelectorAll('text')].map(node => node.textContent)};
            }""")
            if geometry["text_overflow"]:
                raise ChartRenderError("Rendered labels overflow the export canvas")
            page.screenshot(path=str(output), type="png", animations="disabled", scale="device")
            return geometry
        finally:
            browser.close()


def render_chart(plan: dict, output_path, *, publisher="", document_date="", pages=(), source_text="") -> dict:
    """Export a 2400px PNG plus SVG sidecar; failures never produce approved media.

    Node and browser work run in one child process with a total 60-second deadline.
    The fresh Python child uses the current interpreter and production-installed
    Playwright, so its Chromium revision matches the Python runtime image.
    """
    validate_plan(plan)
    node = shutil.which("node")
    if not node:
        raise ChartRenderError("Node runtime is required for deterministic D3 export")
    output = Path(output_path).resolve()
    if output.suffix.lower() != ".png":
        raise ChartRenderError("Chart exports require a .png output path")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"plan": plan, "publisher": publisher_label(publisher), "documentDate": str(document_date)[:80],
               "pages": [int(value) for value in list(pages)[:40]], "sourceText": str(source_text)[:20000]}
    with tempfile.TemporaryDirectory(prefix="radon-chart-", dir=output.parent) as folder:
        temp = Path(folder)
        request = temp / "request.json"
        request.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
        command = [sys.executable, str(Path(__file__).resolve()), "--export", str(request), str(temp / "chart.png")]
        try:
            child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            try:
                stdout, stderr = child.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.communicate()
                raise
            if child.returncode:
                raise subprocess.CalledProcessError(child.returncode, command, stdout, stderr)
            metadata = json.loads(stdout)
        except (subprocess.SubprocessError, ValueError) as error:
            detail = getattr(error, "stderr", "") or str(error)
            raise ChartRenderError(f"Chart export failed: {detail[-2000:]}") from error
        os.replace(temp / "chart.png", output)
        os.replace(temp / "chart.svg", output.with_suffix(".svg"))
    metadata.update(path=str(output), svg_path=str(output.with_suffix(".svg")))
    return metadata


def _export(request_path: str, output_path: str):
    payload = json.loads(Path(request_path).read_text())
    validate_plan(payload["plan"])
    helper = Path(__file__).with_suffix(".mjs")
    result = subprocess.run([shutil.which("node"), str(helper), request_path], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise ChartRenderError(result.stderr[-2000:])
    rendered = json.loads(result.stdout)
    output = Path(output_path)
    geometry = _rasterize(rendered["svg"], output, rendered["width"], rendered["height"])
    output.with_suffix(".svg").write_text(rendered["svg"], encoding="utf-8")
    print(json.dumps({"width": rendered["width"] * 2, "height": rendered["height"] * 2,
                      "renderer": "d3-svg-chromium", "geometry": {k: v for k, v in geometry.items() if k != "visible_text"},
                      "visible_text": geometry["visible_text"]}))


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--export":
        raise SystemExit("Usage: chart_render.py --export request.json chart.png")
    _export(sys.argv[2], sys.argv[3])

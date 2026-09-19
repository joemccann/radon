"""Deterministic chart detection from PDF page objects: no model, no guessed coordinates."""
import zlib

import pytest

from research import figures

W, H = 612, 792


def _pdf(content, size=(W, H)):
    """A one-page PDF with Helvetica and the given content stream (PDF points, origin bottom-left)."""
    stream = zlib.compress(content.encode())
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {size[0]} {size[1]}] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>".encode(),
        b"<< /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def text(x, y, s, size=10):
    return f"BT /F1 {size} Tf {x} {y} Td ({s}) Tj ET\n"


def chart(x0, y0, x1, y1):
    """Axes, four gridlines, a line series and three bars, all as paths."""
    ops = [f"0.5 w {x0} {y0} m {x1} {y0} l S", f"{x0} {y0} m {x0} {y1} l S"]
    for k in range(1, 5):
        y = y0 + (y1 - y0) * k / 5
        ops.append(f"0.2 w {x0} {y:.1f} m {x1} {y:.1f} l S")
    ops.append(f"1 w {x0 + 10} {y0 + 30} m {x0 + 120} {y0 + 90} l {x0 + 260} {y0 + 60} l {x1 - 20} {y1 - 30} l S")
    for k in range(3):
        bx = x0 + 40 + k * 120
        ops.append(f"{bx} {y0} {60} {40 + k * 35} re f")
    return "\n".join(ops) + "\n"


PAGE = (
    "0.3 w 40 760 m 572 760 l S\n"                                      # header rule, full width
    + text(72, 700, "Exhibit 1: S&P 500 EPS revisions breadth", 12)
    + chart(72, 400, 540, 680)
    + text(50, 400, "0%") + text(50, 540, "10%") + text(50, 675, "20%")   # y ticks
    + text(100, 386, "2023") + text(300, 386, "2024") + text(500, 386, "2025")  # x ticks
    + text(72, 372, "Source: Goldman Sachs Global Investment Research")
    + text(72, 330, "Body paragraph one about earnings momentum persisting into next quarter.")
    + text(72, 316, "Body paragraph two with more prose that must stay outside the crop.")
)


@pytest.fixture
def chart_pdf(tmp_path):
    path = tmp_path / "chart.pdf"
    path.write_bytes(_pdf(PAGE))
    return path


def test_detects_the_chart_region_with_axis_labels_inside(chart_pdf):
    found = figures.detect(chart_pdf, 1)
    assert len(found) == 1
    fig = found[0]
    l, t, r, b = fig["bbox"]
    # Chart body spans x 72..540, y 400..680 (top-left normalized: y 0.141..0.495). Ticks sit just outside it.
    assert l <= 50 / W and r >= 540 / W
    assert t <= (H - 680) / H and b >= (H - 386) / H + 0.005, "x tick labels are inside the crop"
    assert b < (H - 330) / H, "body prose stays outside the crop"
    assert fig["page"] == 1 and fig["objects"] >= 8


def test_title_and_source_are_captured_as_optional_metadata(chart_pdf):
    fig = figures.detect(chart_pdf, 1)[0]
    assert fig["title"] == "Exhibit 1: S&P 500 EPS revisions breadth"
    assert fig["source_line"].startswith("Source: Goldman Sachs")
    l, t, r, b = fig["bbox"]
    assert t <= (H - 712) / H and b >= (H - 370) / H, "title and source lines are pulled into the crop when found"


def test_header_rules_and_text_only_pages_yield_nothing(tmp_path):
    path = tmp_path / "text.pdf"
    path.write_bytes(_pdf("0.3 w 40 760 m 572 760 l S\n" + text(72, 700, "Just a memo", 12) + text(72, 680, "with a rule.")))
    assert figures.detect(path, 1) == []


def test_missing_title_and_source_do_not_block_detection(tmp_path):
    path = tmp_path / "bare.pdf"
    path.write_bytes(_pdf(chart(72, 400, 540, 680)))
    fig = figures.detect(path, 1)[0]
    assert fig["title"] is None and fig["source_line"] is None


def test_two_charts_side_by_side_are_separate_figures(tmp_path):
    path = tmp_path / "two.pdf"
    path.write_bytes(_pdf(chart(60, 400, 290, 640) + chart(330, 400, 560, 640)))
    found = figures.detect(path, 1)
    assert len(found) == 2
    assert found[0]["bbox"][2] < found[1]["bbox"][0]


def test_catalogue_numbers_figures_across_pages_and_renders_crops(chart_pdf, tmp_path):
    catalogue = figures.catalogue(chart_pdf, [1], tmp_path / "out")
    assert [f["id"] for f in catalogue] == ["f1"]
    png = tmp_path / "out" / catalogue[0]["image_file"]
    assert png.is_file() and png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

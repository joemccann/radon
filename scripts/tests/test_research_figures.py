"""Deterministic chart detection from PDF page objects: no model, no guessed coordinates."""
import zlib
from pathlib import Path

import pytest

from research import figures
from research import pdf as research_pdf

SCRIPTS = Path(__file__).resolve().parents[1]
W, H = 612, 792
GRAY = bytes([0x20] * 64)


def _pdf(content, size=(W, H), origin=(0, 0), extras="", image=None):
    """A one-page PDF with Helvetica and the given content stream (PDF points, origin bottom-left)."""
    ox, oy = origin
    stream = zlib.compress(content.encode())
    resources = "/Font << /F1 5 0 R >>"
    if image is not None:
        resources += " /XObject << /Im1 6 0 R >>"
    extra = f" {extras.strip()}" if extras.strip() else ""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [{ox} {oy} {ox + size[0]} {oy + size[1]}] "
         f"/Contents 4 0 R /Resources << {resources} >>{extra} >>").encode(),
        b"<< /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    if image is not None:
        iw, ih, data = image
        objs.append(
            b"<< /Type /XObject /Subtype /Image /Width " + str(iw).encode()
            + b" /Height " + str(ih).encode()
            + b" /ColorSpace /DeviceGray /BitsPerComponent 8 /Length "
            + str(len(data)).encode() + b" >>\nstream\n" + data + b"\nendstream"
        )
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


def image_chart(x0, y0, x1, y1):
    """One DeviceGray IMAGE XObject stretched into the box (PDF points, origin bottom-left)."""
    return f"q {x1 - x0} 0 0 {y1 - y0} {x0} {y0} cm /Im1 Do Q\n"


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


def test_isolated_catalogue_matches_in_process_and_renders_in_the_child(chart_pdf, tmp_path, monkeypatch):
    from research.pipeline import figures_isolated
    monkeypatch.setenv("PYTHONPATH", str(SCRIPTS))
    extras = [{"page": 1, "bbox": [0.05, 0.86, 0.45, 0.99]}]
    expected = figures.catalogue(chart_pdf, [1], tmp_path / "direct", extras=extras)
    isolated = figures_isolated(chart_pdf, [1], tmp_path / "child", extras=extras)
    assert list(isolated) == list(expected)
    assert isolated.skipped_pages == expected.skipped_pages
    png = tmp_path / "child" / isolated[0]["image_file"]
    assert png.is_file() and png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_isolated_creation_date_reads_the_info_dictionary(tmp_path, monkeypatch):
    from research.pipeline import creation_date_isolated
    monkeypatch.setenv("PYTHONPATH", str(SCRIPTS))
    path = tmp_path / "dated.pdf"
    body = _pdf(chart(72, 400, 540, 680))
    info = b"7 0 obj\n<< /CreationDate (D:20260908124300Z) >>\nendobj\n"
    trailer = body.rindex(b"trailer")
    path.write_bytes(body[:trailer] + info + body[trailer:].replace(b"/Root 1 0 R", b"/Root 1 0 R /Info 7 0 R"))
    assert creation_date_isolated(path) == "D:20260908124300Z"
    assert creation_date_isolated(tmp_path / "missing.pdf") is None


RASTER = (
    text(72, 700, "Exhibit 1: S&P 500 EPS revisions breadth", 12)
    + image_chart(72, 400, 540, 680)
    + text(72, 372, "Source: Goldman Sachs Global Investment Research")
    + text(72, 330, "Body paragraph one about earnings momentum persisting into next quarter.")
)


def _dark_extent(png):
    from PIL import Image
    with Image.open(png) as image:
        gray = image.convert("L")
        w, h = gray.size
        pixels = gray.load()
        xs, ys = [], []
        for y in range(h):
            for x in range(w):
                if pixels[x, y] < 80:
                    xs.append(x)
                    ys.append(y)
    assert xs, "crop has no dark pixels"
    return min(xs) / w, min(ys) / h, max(xs) / w, max(ys) / h


def _assert_chart_fills_crop(pdf_path, bbox, tmp_path, name="crop"):
    record = research_pdf.render(pdf_path, tmp_path / name, [1], dpi=216, crop=bbox)[0]
    png = tmp_path / name / record["image_file"]
    _l, _t, r, b = _dark_extent(png)
    assert r >= 0.98 and b >= 0.98, (r, b)


def test_single_raster_image_chart_is_a_figure(tmp_path):
    path = tmp_path / "raster.pdf"
    path.write_bytes(_pdf(RASTER, image=(8, 8, GRAY)))
    found = figures.detect(path, 1)
    assert len(found) == 1
    fig = found[0]
    assert fig["kind"] == "raster"
    l, t, r, b = fig["bbox"]
    assert l <= 72 / W and r >= 540 / W
    assert t <= (H - 712) / H and b >= (H - 370) / H, "title and Source line are inside the crop"
    assert b < (H - 330) / H, "body prose stays outside the crop"


def test_two_lone_raster_charts_are_two_figures(tmp_path):
    path = tmp_path / "two-raster.pdf"
    path.write_bytes(_pdf(image_chart(60, 400, 290, 640) + image_chart(330, 400, 560, 640), image=(8, 8, GRAY)))
    found = figures.detect(path, 1)
    assert len(found) == 2
    assert found[0]["kind"] == found[1]["kind"] == "raster"
    assert found[0]["bbox"][2] < found[1]["bbox"][0]


def test_near_full_page_image_is_not_a_figure(tmp_path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(_pdf(image_chart(6, 6, 606, 786), image=(8, 8, GRAY)))
    assert figures.detect(path, 1) == []


def test_offset_mediabox_page_detects_in_displayed_frame(tmp_path):
    path = tmp_path / "offset.pdf"
    path.write_bytes(_pdf(PAGE, origin=(10, 10)))
    found = figures.detect(path, 1)
    assert len(found) == 1 and found[0]["kind"] == "vector"
    _assert_chart_fills_crop(path, found[0]["bbox"], tmp_path, "offset")


def test_cropbox_inset_page_detects_in_displayed_frame(tmp_path):
    path = tmp_path / "cropbox.pdf"
    path.write_bytes(_pdf(PAGE, extras="/CropBox [20 20 592 772]"))
    found = figures.detect(path, 1)
    assert len(found) == 1 and found[0]["kind"] == "vector"
    _assert_chart_fills_crop(path, found[0]["bbox"], tmp_path, "cropbox")


def test_rotated_page_crop_lands_on_displayed_chart(tmp_path):
    path = tmp_path / "rotated.pdf"
    path.write_bytes(_pdf(PAGE, extras="/Rotate 90"))
    found = figures.detect(path, 1)
    assert len(found) == 1 and found[0]["kind"] == "vector"
    _assert_chart_fills_crop(path, found[0]["bbox"], tmp_path, "rotated")


def _form_pdf(content, matrices, extras="", image=None):
    """Nest content in Form XObjects; matrices run outermost to innermost."""
    def stream(dictionary, value):
        packed = zlib.compress(value.encode())
        return (dictionary + f" /Length {len(packed)} /Filter /FlateDecode >>\nstream\n").encode() + packed + b"\nendstream"

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W} {H}] /Contents 4 0 R "
         f"/Resources << /XObject << /Fm 6 0 R >> >> {extras} >>").encode(),
        stream("<<", "/Fm Do"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for index, matrix in enumerate(matrices):
        final = index == len(matrices) - 1
        resources = "/Font << /F1 5 0 R >>" if final else f"/XObject << /Fm {7 + index} 0 R >>"
        if final and image is not None:
            resources += f" /XObject << /Im1 {6 + len(matrices)} 0 R >>"
        dictionary = ("<< /Type /XObject /Subtype /Form /BBox [0 0 612 792] "
                      + f"/Matrix [{' '.join(map(str, matrix))}] /Resources << {resources} >>")
        objects.append(stream(dictionary, content if final else "/Fm Do"))
    if image is not None:
        iw, ih, data = image
        objects.append(f"<< /Type /XObject /Subtype /Image /Width {iw} /Height {ih} "
                       f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length {len(data)} >>\nstream\n".encode()
                       + data + b"\nendstream")
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for index, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


@pytest.mark.parametrize("matrices", [
    [(1, 0, 0, 1, 50, 350)],
    [(0.8, 0, 0, 1.1, 100, 350)],
    [(0.8, 0, 0, 1.1, 100, 350), (0.7, 0, 0, 0.8, 30, 20)],
])
def test_form_chart_bounds_include_all_ancestor_transforms(tmp_path, matrices):
    import pypdfium2
    # Bank decks put plots near their Form's local origin, then place the Form
    # higher on the page. Untransformed bounds crop footer labels instead.
    content = text(20, 215, "Crowded Longs and Shorts", 12) + chart(20, 20, 420, 190)
    path = tmp_path / "forms.pdf"
    path.write_bytes(_form_pdf(content, matrices))
    found = figures.detect(path, 1)
    assert len(found) == 1
    transform = pypdfium2.PdfMatrix()
    for matrix in matrices:
        transform = pypdfium2.PdfMatrix(*matrix).multiply(transform)
    left, bottom = transform.on_point(20, 20)
    right, top = transform.on_point(420, 190)
    l, t, r, b = found[0]["bbox"]
    assert l <= left / W and r >= right / W
    assert t <= 1 - top / H and b >= 1 - bottom / H
    assert found[0]["title"] == "Crowded Longs and Shorts"
    assert b < 0.6, "the crop covers the placed plot, not the Form-local footer strip"


def test_form_chart_own_matrix_is_not_applied_twice(tmp_path):
    import pypdfium2
    content = "q 0.7 0 0 0.8 30 20 cm\n" + chart(20, 20, 420, 190) + "Q\n"
    path = tmp_path / "own-matrix.pdf"
    path.write_bytes(_form_pdf(content, [(0.8, 0, 0, 1.1, 100, 350)]))
    with pypdfium2.PdfDocument(str(path)) as document:
        actual = list(figures._drawn_bounds(document[0]))
    union = actual[0][0]
    for bounds, _image in actual[1:]:
        union = (min(union[0], bounds[0]), min(union[1], bounds[1]),
                 max(union[2], bounds[2]), max(union[3], bounds[3]))
    assert union == pytest.approx((135.2, 389.6, 359.2, 539.2), abs=2), union


def test_raster_chart_in_nested_forms_uses_page_bounds(tmp_path):
    path = tmp_path / "raster-forms.pdf"
    path.write_bytes(_form_pdf(image_chart(20, 20, 420, 190),
                               [(0.8, 0, 0, 1.1, 100, 350), (0.7, 0, 0, 0.8, 30, 20)],
                               image=(8, 8, GRAY)))
    fig = figures.detect(path, 1)[0]
    assert fig["kind"] == "raster"
    l, t, r, b = fig["bbox"]
    assert l <= 135.2 / W and r >= 359.2 / W
    assert t <= 1 - 539.2 / H and b >= 1 - 389.6 / H
    assert b < 0.6


def test_form_transform_precedes_displayed_page_rotation(tmp_path):
    path = tmp_path / "rotated-form.pdf"
    matrices = [(0.8, 0, 0, 1.1, 100, 350)]
    path.write_bytes(_form_pdf(chart(20, 20, 420, 190), matrices))
    expected = figures._to_displayed(figures.detect(path, 1)[0]["bbox"], 90)
    path.write_bytes(_form_pdf(chart(20, 20, 420, 190), matrices, extras="/Rotate 90"))
    assert figures.detect(path, 1)[0]["bbox"] == pytest.approx(expected, abs=0.0001)


def test_catalogue_reports_skipped_pages(tmp_path, monkeypatch):
    import pypdfium2
    path = tmp_path / "chart.pdf"
    path.write_bytes(_pdf(PAGE))
    monkeypatch.setattr(pypdfium2.PdfPage, "get_bbox", lambda self: (float("nan"), 0.0, 612.0, 792.0))
    catalogue = figures.catalogue(path, [1], tmp_path / "out")
    assert catalogue == []
    assert catalogue.skipped_pages == [{"page": 1, "reason": "invalid_bbox"}]


def _multipage(contents, image=None):
    """One PDF with one page per content stream, sharing Helvetica and the optional gray image."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", None, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    if image is not None:
        iw, ih, data = image
        objs.append(b"<< /Type /XObject /Subtype /Image /Width " + str(iw).encode() + b" /Height " + str(ih).encode()
                    + b" /ColorSpace /DeviceGray /BitsPerComponent 8 /Length " + str(len(data)).encode()
                    + b" >>\nstream\n" + data + b"\nendstream")
    resources = "/Font << /F1 3 0 R >>" + (" /XObject << /Im1 4 0 R >>" if image is not None else "")
    kids = []
    for content in contents:
        stream = zlib.compress(content.encode())
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n" + stream + b"\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W} {H}] /Contents {len(objs)} 0 R /Resources << {resources} >> >>".encode())
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def test_logo_sized_extra_box_is_not_a_figure(chart_pdf, tmp_path):
    # Nemotron tags letterhead logos as pictures (BTIG 2026-10-07: 0.195 x 0.037 of the page).
    logo = {"page": 1, "bbox": [0.6965, 0.0371, 0.8918, 0.0736], "origin": "nemotron"}
    found = figures.catalogue(chart_pdf, [1], tmp_path / "out", extras=[logo])
    assert [f.get("origin") for f in found] == [None], "only the detected chart survives"


def test_running_header_repeated_on_every_page_is_not_a_figure(tmp_path):
    # A letterhead raster in the same spot on every page renders identically and is never a chart.
    # Large enough to pass every size gate, so only the repeat rule can drop it.
    header = image_chart(420, 680, 580, 780)
    path = tmp_path / "running.pdf"
    path.write_bytes(_multipage([header + chart(72, 300, 540, 600), header + chart(72, 100, 540, 500)], image=(8, 8, GRAY)))
    extras = [{"page": n, "bbox": [420 / W, 12 / H, 580 / W, 112 / H]} for n in (1, 2)]
    found = figures.catalogue(path, [1, 2], tmp_path / "out", extras=extras)
    assert [(f["id"], f["page"], f["kind"]) for f in found] == [("f1", 1, "vector"), ("f2", 2, "vector")]
    for figure in found:
        assert (tmp_path / "out" / figure["image_file"]).is_file()
        assert figure["bbox"][1] > 112 / H, "the header is not part of the chart"


def test_identical_chart_on_two_pages_is_kept_once_not_dropped(tmp_path):
    # A deck that repeats one chart body-side is not letterhead: keep the first, drop the copy.
    path = tmp_path / "repeat.pdf"
    path.write_bytes(_multipage([chart(72, 300, 540, 600), chart(72, 300, 540, 600)]))
    found = figures.catalogue(path, [1, 2], tmp_path / "out")
    assert [(f["id"], f["page"]) for f in found] == [("f1", 1)]


def test_page_frame_artwork_does_not_swallow_the_chart(tmp_path):
    # BTIG page one: a full-height sidebar fused every drawn object into a whole-page "figure".
    sidebar = "0 0 66 792 re f\n0 760 66 32 re f\n"
    path = tmp_path / "sidebar.pdf"
    path.write_bytes(_pdf(sidebar + chart(72, 400, 540, 680)))
    found = figures.detect(path, 1)
    assert len(found) == 1
    l, t, r, b = found[0]["bbox"]
    assert l >= 66 / W - 0.01 and t > 0.05 and b < 0.95


def test_sidebar_only_page_yields_nothing(tmp_path):
    path = tmp_path / "frame.pdf"
    path.write_bytes(_pdf("0 0 66 792 re f\n0 760 66 32 re f\n0 0 612 4 re f\n" + text(100, 700, "Disclosures")))
    assert figures.detect(path, 1) == []


def test_extra_box_spanning_two_detected_charts_is_not_a_third_figure(tmp_path):
    # Nemotron boxed a whole page's two charts as one picture (BTIG page 4): it duplicates both.
    path = tmp_path / "stacked.pdf"
    path.write_bytes(_pdf(image_chart(72, 420, 540, 700) + image_chart(72, 80, 540, 380), image=(8, 8, GRAY)))
    union = {"page": 1, "bbox": [70 / W, 90 / H, 542 / W, 714 / H]}
    found = figures.catalogue(path, [1], tmp_path / "out", extras=[union])
    assert len(found) == 2 and all(f.get("origin") is None for f in found)

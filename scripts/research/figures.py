"""Deterministic chart detection from PDFium page objects.

Charts in bank research are vector paths (axes, gridlines, series, bars) or
raster images. Drawn objects are clustered by proximity; each cluster that is
large enough becomes a figure. Axis tick and legend text adjacent to the
cluster is pulled into the crop. The nearest text line above (title) and the
nearest ``Source:`` line below are attached as optional metadata and, when
found, included in the crop. Nothing here consults a model.

Coordinates in results are normalized [left, top, right, bottom] in the
displayed page frame, origin top-left, matching research.pdf.render crops.
"""
from __future__ import annotations
import math
import re
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

DRAWN = (pdfium_c.FPDF_PAGEOBJ_PATH, pdfium_c.FPDF_PAGEOBJ_IMAGE, pdfium_c.FPDF_PAGEOBJ_SHADING)
GAP = 0.012            # cluster join distance, fraction of page
MIN_AREA = 0.02        # figure must cover at least 2% of the page
MIN_SIDE = 0.06        # and be at least 6% wide and tall
MIN_OBJECTS = 3        # a lone rule or logo is not a chart
MAX_IMAGE_AREA = 0.9   # a page-sized scan or background is not a chart
TEXT_REACH = 0.03      # tick/legend text within this distance joins the crop
LINE_REACH = 0.07      # title above / source below within this distance
MARGIN = 0.006
SOURCE_RE = re.compile(r'^\s*(?:source|sources|note|notes)\s*[:：]', re.I)


class Catalogue(list):
    """Figure list plus pages the frame guard still skipped."""

    def __init__(self, figures=(), skipped_pages=None):
        super().__init__(figures)
        self.skipped_pages = list(skipped_pages or [])


def _norm(bounds, frame):
    left, bottom, right, top = bounds
    fl, fb, fr, ft = frame
    width, height = fr - fl, ft - fb
    return [max(0.0, (left - fl) / width), max(0.0, 1 - (top - fb) / height),
            min(1.0, (right - fl) / width), min(1.0, 1 - (bottom - fb) / height)]


def _frame(page):
    bbox = page.get_bbox()
    if bbox is None or len(bbox) != 4 or not all(math.isfinite(float(v)) for v in bbox):
        return None, 'invalid_bbox'
    left, bottom, right, top = (float(v) for v in bbox)
    if not (right > left and top > bottom):
        return None, 'invalid_bbox'
    return (left, bottom, right, top), None


def _to_displayed(box, rotation):
    left, top, right, bottom = box
    turn = rotation % 360
    if turn == 0:
        return box
    if turn == 90:
        return [1 - bottom, left, 1 - top, right]
    if turn == 180:
        return [1 - right, 1 - bottom, 1 - left, 1 - top]
    if turn == 270:
        return [top, 1 - right, bottom, 1 - left]
    return box


def _gap(a, b):
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return math.hypot(dx, dy)


def _union(a, b):
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _cluster(items, gap):
    """Greedy single-link clustering of (box, is_image) whose edges lie within gap."""
    clusters = []
    for box, is_image in items:
        merged = [box, 1, 1 if is_image else 0]
        rest = []
        for cluster in clusters:
            if _gap(cluster[0], merged[0]) <= gap:
                merged = [_union(cluster[0], merged[0]), cluster[1] + merged[1], cluster[2] + merged[2]]
            else:
                rest.append(cluster)
        clusters = rest + [merged]
    return clusters


def _lines(textpage, frame):
    """Text rectangles as normalized boxes with their text, top to bottom."""
    lines = []
    for index in range(min(textpage.count_rects(), 600)):
        left, bottom, right, top = textpage.get_rect(index)
        if not all(math.isfinite(v) for v in (left, bottom, right, top)):
            continue
        value = textpage.get_text_bounded(left, bottom, right, top).strip()
        if value:
            lines.append((_norm((left, bottom, right, top), frame), value))
    return sorted(lines, key=lambda item: item[0][1])


def _grow_text(box, lines, reach):
    """Pull tick and legend text that sits within reach of the drawn region into the crop."""
    grown = list(box)
    changed = True
    while changed:
        changed = False
        for line_box, _ in lines:
            if _gap(grown, line_box) <= reach and line_box[3] - line_box[1] < 0.03 and _gap(box, line_box) <= reach:
                merged = _union(grown, line_box)
                if merged != grown:
                    grown, changed = merged, True
    return grown


def _attach(box, lines, reach):
    """Nearest text line just above (title) and nearest Source line just below."""
    title = source = None
    above = [(box[1] - lb[3], lb, value) for lb, value in lines if lb[3] <= box[1] + 0.002 and box[1] - lb[3] <= reach
             and lb[2] > box[0] and lb[0] < box[2] and not SOURCE_RE.match(value)]
    if above:
        title = min(above, key=lambda item: item[0])
    below = [(lb[1] - box[3], lb, value) for lb, value in lines if lb[1] >= box[3] - 0.002 and lb[1] - box[3] <= reach
             and lb[2] > box[0] and lb[0] < box[2] and SOURCE_RE.match(value)]
    if below:
        source = min(below, key=lambda item: item[0])
    return title, source


def _kind(count, images):
    if images <= 0:
        return 'vector'
    if images >= count:
        return 'raster'
    return 'mixed'


def _figures_on_page(pdf_path, page_number):
    """(figures, skip_reason) for one 1-based page. skip_reason is set only when the frame is unusable."""
    with pdfium.PdfDocument(str(Path(pdf_path).resolve(strict=True))) as document:
        page = document[page_number - 1]
        frame, reason = _frame(page)
        if reason:
            page.close()
            return [], reason
        rotation = page.get_rotation()
        items = []
        for obj in page.get_objects(filter=DRAWN, max_depth=8):
            try:
                box = _norm(obj.get_bounds(), frame)
            except pdfium.PdfiumError:
                continue
            if box[2] > box[0] and box[3] > box[1]:
                items.append((box, obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE))
        textpage = page.get_textpage()
        lines = _lines(textpage, frame)
        textpage.close()
        page.close()
    found = []
    for box, count, images in _cluster(items, GAP):
        w, h = box[2] - box[0], box[3] - box[1]
        if not (count >= MIN_OBJECTS or images >= 1):
            continue
        if w * h < MIN_AREA or min(w, h) < MIN_SIDE:
            continue
        if images >= 1 and w * h > MAX_IMAGE_AREA:
            continue
        title, source = _attach(box, lines, LINE_REACH)
        crop = _grow_text(box, lines, TEXT_REACH)
        for hit in (title, source):
            if hit:
                crop = _union(crop, hit[1])
        crop = [max(0.0, crop[0] - MARGIN), max(0.0, crop[1] - MARGIN), min(1.0, crop[2] + MARGIN), min(1.0, crop[3] + MARGIN)]
        crop = _to_displayed(crop, rotation)
        crop = [max(0.0, crop[0]), max(0.0, crop[1]), min(1.0, crop[2]), min(1.0, crop[3])]
        if crop[2] <= crop[0] or crop[3] <= crop[1]:
            continue
        found.append({'page': page_number, 'bbox': [round(v, 4) for v in crop], 'objects': count,
                      'kind': _kind(count, images),
                      'title': title[2] if title else None, 'source_line': source[2] if source else None})
    return sorted(found, key=lambda f: (f['bbox'][1], f['bbox'][0])), None


def detect(pdf_path, page_number):
    """Figures on one page: [{page, bbox, objects, kind, title, source_line}] in reading order."""
    found, _reason = _figures_on_page(pdf_path, page_number)
    return found


def catalogue(pdf_path, pages, output_dir, dpi=216, extras=None):
    """Detect and render every figure on the given pages; ids f1.. in reading order.

    extras are Nemotron picture boxes ({page, bbox}) already in the displayed
    frame. A box that overlaps a detected figure is not rendered twice.
    """
    from research.pdf import render
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    result, skipped = [], []
    for number in pages:
        found, reason = _figures_on_page(pdf_path, number)
        if reason:
            skipped.append({'page': number, 'reason': reason})
        for figure in found:
            _append_figure(result, pdf_path, output, dpi, figure)
    for extra in extras or []:
        if not isinstance(extra, dict) or extra.get('page') not in pages:
            continue
        box = _extra_box(extra.get('bbox'))
        if box is None:
            continue
        if any(row['page'] == extra['page'] and _iou(row['bbox'], box) > 0.5 for row in result):
            continue
        figure = {'page': extra['page'], 'bbox': [round(v, 4) for v in box], 'objects': extra.get('objects') or 1,
                  'kind': extra.get('kind') or 'raster', 'title': extra.get('title'),
                  'source_line': extra.get('source_line'), 'origin': extra.get('origin') or 'nemotron'}
        _append_figure(result, pdf_path, output, dpi, figure)
    return Catalogue(result, skipped_pages=skipped)


def _append_figure(result, pdf_path, output, dpi, figure):
    from research.pdf import render
    index = len(result) + 1
    target = output / f'f{index}'
    record = render(pdf_path, target, [figure['page']], dpi=dpi, crop=figure['bbox'])[0]
    result.append({'id': f'f{index}', **figure, 'image_file': f'f{index}/' + record['image_file'],
                   'width': record['width'], 'height': record['height']})


def _extra_box(box):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if not all(isinstance(n, (int, float)) and math.isfinite(n) for n in box):
        return None
    if not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
        return None
    return [float(n) for n in box]


def _iou(a, b) -> float:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, right - left) * max(0.0, bottom - top)
    if inter <= 0:
        return 0.0
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0

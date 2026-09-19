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
TEXT_REACH = 0.03      # tick/legend text within this distance joins the crop
LINE_REACH = 0.07      # title above / source below within this distance
MARGIN = 0.006
SOURCE_RE = re.compile(r'^\s*(?:source|sources|note|notes)\s*[:：]', re.I)


def _norm(bounds, width, height):
    left, bottom, right, top = bounds
    return [max(0.0, left / width), max(0.0, 1 - top / height), min(1.0, right / width), min(1.0, 1 - bottom / height)]


def _gap(a, b):
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return math.hypot(dx, dy)


def _union(a, b):
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _cluster(boxes, gap):
    """Greedy single-link clustering of boxes whose edges lie within gap."""
    clusters = []
    for box in boxes:
        merged = [box, 1]
        rest = []
        for cluster in clusters:
            if _gap(cluster[0], merged[0]) <= gap:
                merged = [_union(cluster[0], merged[0]), cluster[1] + merged[1]]
            else:
                rest.append(cluster)
        clusters = rest + [merged]
    return clusters


def _lines(textpage, width, height):
    """Text rectangles as normalized boxes with their text, top to bottom."""
    lines = []
    for index in range(min(textpage.count_rects(), 600)):
        left, bottom, right, top = textpage.get_rect(index)
        if not all(math.isfinite(v) for v in (left, bottom, right, top)):
            continue
        value = textpage.get_text_bounded(left, bottom, right, top).strip()
        if value:
            lines.append((_norm((left, bottom, right, top), width, height), value))
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


def detect(pdf_path, page_number):
    """Figures on one page: [{page, bbox, objects, title, source_line}] in reading order."""
    with pdfium.PdfDocument(str(Path(pdf_path).resolve(strict=True))) as document:
        page = document[page_number - 1]
        width, height = page.get_size()
        bbox = page.get_bbox()
        if page.get_rotation() != 0 or any(abs(a - b) > .01 for a, b in zip(bbox, (0, 0, width, height))):
            page.close()
            return []          # Only a verified unrotated zero-origin frame maps to render crops.
        boxes = []
        for obj in page.get_objects(filter=DRAWN, max_depth=8):
            try:
                box = _norm(obj.get_bounds(), width, height)
            except pdfium.PdfiumError:
                continue
            if box[2] > box[0] and box[3] > box[1]:
                boxes.append(box)
        textpage = page.get_textpage()
        lines = _lines(textpage, width, height)
        textpage.close()
        page.close()
    found = []
    for box, count in _cluster(boxes, GAP):
        w, h = box[2] - box[0], box[3] - box[1]
        if count < MIN_OBJECTS or w * h < MIN_AREA or min(w, h) < MIN_SIDE:
            continue
        title, source = _attach(box, lines, LINE_REACH)
        crop = _grow_text(box, lines, TEXT_REACH)
        for hit in (title, source):
            if hit:
                crop = _union(crop, hit[1])
        crop = [max(0.0, crop[0] - MARGIN), max(0.0, crop[1] - MARGIN), min(1.0, crop[2] + MARGIN), min(1.0, crop[3] + MARGIN)]
        found.append({'page': page_number, 'bbox': [round(v, 4) for v in crop], 'objects': count,
                      'title': title[2] if title else None, 'source_line': source[2] if source else None})
    return sorted(found, key=lambda f: (f['bbox'][1], f['bbox'][0]))


def catalogue(pdf_path, pages, output_dir, dpi=216):
    """Detect and render every figure on the given pages; ids f1.. in reading order."""
    from research.pdf import render
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    result = []
    for number in pages:
        for figure in detect(pdf_path, number):
            index = len(result) + 1
            target = output / f'f{index}'
            record = render(pdf_path, target, [number], dpi=dpi, crop=figure['bbox'])[0]
            result.append({'id': f'f{index}', **figure, 'image_file': f'f{index}/' + record['image_file'],
                           'width': record['width'], 'height': record['height']})
    return result

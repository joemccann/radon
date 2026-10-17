"""Deterministic chart detection from PDFium page objects.

Charts in bank research are vector paths (axes, gridlines, series, bars) or
raster images. Drawn objects are clustered by proximity; each cluster that is
large enough becomes a figure. Page artwork (a path that bleeds to a page
edge: sidebars, header bands, rules; an image touching three edges or covering
the page) is removed before clustering, so it cannot bridge a logo into a chart, model-proposed boxes
pass the same size gates, and an element that renders identically on more
than one page is letterhead (header/footer band: dropped everywhere) or a
repeated copy (kept once). Axis tick and legend text adjacent to the
cluster is pulled into the crop. The nearest text line above (title) and the
nearest ``Source:`` line below are attached as optional metadata and, when
found, included in the crop. Nothing here consults a model.

Coordinates in results are normalized [left, top, right, bottom] in the
displayed page frame, origin top-left, matching research.pdf.render crops.
"""
from __future__ import annotations
import hashlib
import math
import re
import shutil
from ctypes import c_float
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
EDGE = 0.005           # a box within this of a page edge touches it
RUNNING_BAND = 0.15    # repeats wholly inside the top or bottom band are letterhead
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


def _edges(box):
    return sum((box[0] <= EDGE, box[1] <= EDGE, box[2] >= 1 - EDGE, box[3] >= 1 - EDGE))


def _page_artwork(box, is_image=True):
    """Chart paths sit inside the margins; a path bleeding to an edge is a sidebar, header band or rule."""
    return (_edges(box) >= (3 if is_image else 1)
            or (box[2] - box[0]) * (box[3] - box[1]) > MAX_IMAGE_AREA)


def _chart_sized(box):
    w, h = box[2] - box[0], box[3] - box[1]
    return w * h >= MIN_AREA and min(w, h) >= MIN_SIDE


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
    """Physical text lines, joining adjacent PDFium fragments on one baseline."""
    lines = []
    for index in range(min(textpage.count_rects(), 600)):
        left, bottom, right, top = textpage.get_rect(index)
        if not all(math.isfinite(v) for v in (left, bottom, right, top)):
            continue
        value = textpage.get_text_bounded(left, bottom, right, top).strip()
        if value:
            lines.append((_norm((left, bottom, right, top), frame), value))
    rows = []
    for box, value in sorted(lines, key=lambda item: (item[0][1], item[0][0])):
        for index, (other, text) in enumerate(rows):
            overlap = min(box[3], other[3]) - max(box[1], other[1])
            height = min(box[3] - box[1], other[3] - other[1])
            if overlap >= height * 0.3 and _gap(box, other) <= GAP:
                parts = [(other[0], text), (box[0], value)]
                rows[index] = (_union(other, box), ' '.join(t for _, t in sorted(parts)))
                break
        else:
            rows.append((box, value))
    return sorted(rows, key=lambda item: item[0][1])


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
    # Chart-local rows join subtitle/source fragments even when typesetting
    # leaves a large gap between them. Do not join neighboring columns.
    rows = []
    for lb, value in lines:
        if lb[2] <= box[0] or lb[0] >= box[2]:
            continue
        for index, (other, parts) in enumerate(rows):
            overlap = min(lb[3], other[3]) - max(lb[1], other[1])
            if overlap >= min(lb[3] - lb[1], other[3] - other[1]) * 0.3:
                rows[index] = (_union(other, lb), parts + [(lb[0], value)])
                break
        else:
            rows.append((lb, [(lb[0], value)]))
    lines = sorted([(lb, ' '.join(value for _, value in sorted(parts))) for lb, parts in rows],
                   key=lambda item: item[0][1])
    title = source = None
    above = [(box[1] - lb[3], lb, value) for lb, value in lines if lb[3] <= box[1] + 0.002 and box[1] - lb[3] <= reach
             and lb[2] > box[0] and lb[0] < box[2] and not SOURCE_RE.match(value)]
    if above:
        title = min(above, key=lambda item: item[0])
        # A title/subtitle block has compact line spacing. Stop at the blank
        # paragraph gutter, rather than treating the preceding prose as a title.
        for lb, value in reversed(lines):
            if (lb[3] <= title[1][1] and title[1][1] - lb[3] <= GAP
                    and lb[2] > box[0] and lb[0] < box[2] and not SOURCE_RE.match(value)):
                title = (title[0], _union(title[1], lb), value + '\n' + title[2])
    below = [(lb[1] - box[3], lb, value) for lb, value in lines if lb[1] >= box[3] - 0.002 and lb[1] - box[3] <= reach
             and lb[2] > box[0] and lb[0] < box[2] and SOURCE_RE.match(value)]
    if below:
        source = min(below, key=lambda item: item[0])
        # Sources and notes can wrap over several lines. Continue only across
        # compact, similarly sized lines, so the next body paragraph stays out.
        last = source[1]
        for lb, _value in lines:
            height = lb[3] - lb[1]
            if (lb[1] >= last[1] and lb[1] - last[3] <= GAP
                    and height <= (last[3] - last[1]) * 1.4
                    and lb[2] > box[0] and lb[0] < box[2]):
                source = (source[0], _union(source[1], lb), source[2])
                last = lb
    return title, source


def _kind(count, images):
    if images <= 0:
        return 'vector'
    if images >= count:
        return 'raster'
    return 'mixed'


def _clip_bounds(obj):
    """Conservative envelope of the object's clip, in its containing frame.

    Path bounds include invisible series points outside the plot viewport.
    Clip segments (including Bezier control points) bound the visible region;
    union all subpaths rather than incorrectly intersecting compound shapes.
    Like get_bounds(), PDFium has already applied the object's own matrix.
    """
    clip = pdfium_c.FPDFPageObj_GetClipPath(obj)
    points = []
    for index in range(max(0, pdfium_c.FPDFClipPath_CountPaths(clip))):
        count = pdfium_c.FPDFClipPath_CountPathSegments(clip, index)
        if count < 0:
            return None
        for segment_index in range(count):
            segment = pdfium_c.FPDFClipPath_GetPathSegment(clip, index, segment_index)
            x, y = c_float(), c_float()
            if not segment or not pdfium_c.FPDFPathSegment_GetPoint(segment, x, y):
                return None
            if not math.isfinite(x.value) or not math.isfinite(y.value):
                return None
            points.append((x.value, y.value))
    if not points:
        return None
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def _intersection(a, b):
    if b is None:
        return a
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def _transformed_bounds(bounds, matrix):
    left, bottom, right, top = bounds
    points = [matrix.on_point(x, y) for x, y in
              ((left, bottom), (left, top), (right, bottom), (right, top))]
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def _drawn_bounds(page, form=None, parent_matrix=None, depth=0, ancestor_clip=None):
    """Yield drawn bounds in page coordinates, composing ancestor Form matrices.

    PDFium reports a child's bounds in its containing Form's coordinate frame.
    The object's own matrix is already included in those bounds; only ancestor
    matrices belong here. Flattening get_objects() loses that distinction.
    """
    matrix = parent_matrix or pdfium.PdfMatrix()
    for obj in page.get_objects(max_depth=1, form=form):
        clip = _clip_bounds(obj)
        page_clip = _transformed_bounds(clip, matrix) if clip is not None else None
        if ancestor_clip is not None:
            page_clip = _intersection(page_clip, ancestor_clip) if page_clip is not None else ancestor_clip
        if obj.type == pdfium_c.FPDF_PAGEOBJ_FORM:
            if depth < 7:
                yield from _drawn_bounds(page, obj, obj.get_matrix().multiply(matrix), depth + 1, page_clip)
            continue
        if obj.type not in DRAWN:
            continue
        try:
            bounds = _intersection(obj.get_bounds(), clip)
            if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                continue
            bounds = _intersection(_transformed_bounds(bounds, matrix), page_clip)
        except pdfium.PdfiumError:
            continue
        if (all(math.isfinite(v) for v in bounds)
                and bounds[2] > bounds[0] and bounds[3] > bounds[1]):
            yield bounds, obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE


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
        for bounds, is_image in _drawn_bounds(page):
            box = _norm(bounds, frame)
            if box[2] > box[0] and box[3] > box[1] and not _page_artwork(box, is_image):
                items.append((box, is_image))
        textpage = page.get_textpage()
        lines = _lines(textpage, frame)
        textpage.close()
        page.close()
    found = []
    for box, count, images in _cluster(items, GAP):
        w, h = box[2] - box[0], box[3] - box[1]
        if not (count >= MIN_OBJECTS or images >= 1):
            continue
        if not _chart_sized(box):
            continue
        if (images >= 1 and w * h > MAX_IMAGE_AREA) or _edges(box) >= 3:
            continue
        title, source = _attach(box, lines, LINE_REACH)
        # Raster charts already contain their own labels; nearby prose is not part of the figure.
        crop = list(box) if images == count else _grow_text(box, lines, TEXT_REACH)
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
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    staged, skipped = [], []
    for number in pages:
        found, reason = _figures_on_page(pdf_path, number)
        if reason:
            skipped.append({'page': number, 'reason': reason})
        staged.extend(found)
    for extra in extras or []:
        if not isinstance(extra, dict) or extra.get('page') not in pages:
            continue
        box = _extra_box(extra.get('bbox'))
        if box is None or not _chart_sized(box) or _page_artwork(box):
            continue
        if any(row['page'] == extra['page'] and (_iou(row['bbox'], box) > 0.5 or _covered(row['bbox'], box) > 0.8)
               for row in staged):
            continue
        staged.append({'page': extra['page'], 'bbox': [round(v, 4) for v in box], 'objects': extra.get('objects') or 1,
                       'kind': extra.get('kind') or 'raster', 'title': extra.get('title'),
                       'source_line': extra.get('source_line'), 'origin': extra.get('origin') or 'nemotron'})
    rendered = [_render_figure(pdf_path, output / '.staging' / str(n), dpi, figure) for n, figure in enumerate(staged)]
    result = []
    for figure, record, digest in _drop_running(rendered):
        index = len(result) + 1
        target = output / f'f{index}'
        if target.exists():
            shutil.rmtree(target)
        Path(record['dir']).rename(target)
        result.append({'id': f'f{index}', **figure, 'image_file': f'f{index}/' + record['image_file'],
                       'width': record['width'], 'height': record['height']})
    shutil.rmtree(output / '.staging', ignore_errors=True)
    return Catalogue(result, skipped_pages=skipped)


def _render_figure(pdf_path, target, dpi, figure):
    from PIL import Image
    from research.pdf import render
    record = render(pdf_path, target, [figure['page']], dpi=dpi, crop=figure['bbox'])[0]
    with Image.open(Path(target) / record['image_file']) as image:
        digest = hashlib.sha256(image.convert('L').tobytes() + repr(image.size).encode()).hexdigest()
    return figure, dict(record, dir=str(target)), digest


def _drop_running(rendered):
    """Identical pixels on two or more pages: letterhead in the top/bottom band, else a repeat kept once."""
    pages = {}
    for figure, _record, digest in rendered:
        pages.setdefault(digest, set()).add(figure['page'])
    kept, seen = [], set()
    for figure, record, digest in rendered:
        if len(pages[digest]) > 1:
            box = figure['bbox']
            if box[3] <= RUNNING_BAND or box[1] >= 1 - RUNNING_BAND or digest in seen:
                continue
        seen.add(digest)
        kept.append((figure, record, digest))
    return kept


def _extra_box(box):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if not all(isinstance(n, (int, float)) and math.isfinite(n) for n in box):
        return None
    if not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
        return None
    return [float(n) for n in box]


def _covered(inner, outer) -> float:
    """Share of `inner` that lies inside `outer`."""
    left, top = max(inner[0], outer[0]), max(inner[1], outer[1])
    right, bottom = min(inner[2], outer[2]), min(inner[3], outer[3])
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return max(0.0, right - left) * max(0.0, bottom - top) / area if area > 0 else 0.0


def _iou(a, b) -> float:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, right - left) * max(0.0, bottom - top)
    if inter <= 0:
        return 0.0
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0

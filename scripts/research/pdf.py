"""Local Firecrawl extraction and original PDF page rendering; no hosted calls."""
import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import sys
from pathlib import Path

import pdf_inspector
import pypdfium2



def save_json(path, value):
    from utils.atomic_io import atomic_save
    atomic_save(str(path), value)


def parse(pdf_path, output):
    source = Path(pdf_path).resolve(strict=True)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    with pypdfium2.PdfDocument(str(source)) as doc:
        if not 0 < len(doc) <= 100:
            raise ValueError('Research PDF must contain 1..100 pages')
    extracted = pdf_inspector.extract_pages_markdown(str(source))
    positioned = pdf_inspector.extract_text_with_positions(str(source))
    fields = ['text', 'x', 'y', 'width', 'height', 'font', 'font_size',
              'page', 'item_type']
    items = {}
    for item in positioned:
        items.setdefault(item.page, []).append({f: getattr(item, f) for f in fields})
    records = []
    for page in extracted.pages:
        number = page.page + 1
        name = f'page-{number:04d}.md'
        (output / name).write_text(page.markdown)
        records.append({'page_number': number, 'markdown_file': name,
                        'needs_ocr': page.needs_ocr, 'ocr_reason': page.ocr_reason,
                        'position_frame_rotation': 'unknown: 1.17.0 wheel does not expose frame rotations',
                        'items': items.get(number, [])})
    result = {'source_path': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'parser': 'firecrawl/pdf-inspector', 'parser_version': importlib.metadata.version('pdf-inspector'),
              'page_numbering': '1-based original PDF page sequence, not printed folio',
              'position_coordinates': 'PDF points, visible box lower-left origin, y up; text y is baseline; /Rotate not applied; rebased frames unknown in this wheel; positions are evidence metadata, not auto-crop coordinates',
              'ocr_enabled': False,
              'page_count': len(records), 'pages': records}
    save_json(output / 'evidence.json', result)
    return result


def render(pdf_path, output, pages, dpi=144, crop=None):
    """crop is optional normalized (left,top,right,bottom) in rendered page frame."""
    if dpi < 72 or dpi > 300:
        raise ValueError('dpi must be 72..300')
    if crop is not None:
        if len(crop) != 4 or not (0 <= crop[0] < crop[2] <= 1 and 0 <= crop[1] < crop[3] <= 1):
            raise ValueError('crop must be left,top,right,bottom fractions within [0,1]')
    source = Path(pdf_path).resolve(strict=True)
    output = Path(output)
    with pypdfium2.PdfDocument(str(source)) as document:
        if any(p < 1 or p > len(document) for p in pages):
            raise ValueError(f'pages must be 1..{len(document)}')
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        results = []
        for number in pages:
            page = document[number - 1]
            if page.get_width() * page.get_height() * (dpi / 72) ** 2 > 24_000_000:
                raise ValueError('Rendered page exceeds pixel budget')
            bitmap = page.render(scale=dpi / 72)
            image = bitmap.to_pil()
            original_size = image.size
            if crop is not None:
                w, h = image.size
                image = image.crop((round(crop[0]*w), round(crop[1]*h), round(crop[2]*w), round(crop[3]*h)))
            name = f'page-{number:04d}' + ('-crop' if crop is not None else '') + '.png'
            image.save(output / name)
            record = {'page_number': number, 'image_file': name, 'dpi': dpi,
                      'width': image.width, 'height': image.height,
                      'original_pixel_size': original_size, 'crop_normalized_top_left': crop,
                      'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                      'renderer': 'pypdfium2', 'renderer_version': importlib.metadata.version('pypdfium2')}
            save_json(output / (name + '.json'), record)
            results.append(record)
            image.close()
            bitmap.close()
            page.close()
    return results


def text_anchors(pdf_path, pages):
    """PDFium glyph bounds only in a verified unrotated, zero-origin frame.

    These aid chart localization, never stand in for final crop inspection.
    Firecrawl positional metadata has different frame guarantees and is not used.
    """
    records = []
    with pypdfium2.PdfDocument(str(Path(pdf_path).resolve(strict=True))) as document:
        for number in pages:
            if type(number) is not int or not 1 <= number <= len(document):
                raise ValueError('Invalid anchor page')
            page = document[number - 1]
            width, height = page.get_size()
            bbox = page.get_bbox()
            safe_frame = (page.get_rotation() == 0 and all(math.isfinite(v) for v in bbox)
                          and all(abs(a-b) < .01 for a,b in zip(bbox, (0,0,width,height))))
            record = {'page_number': number, 'frame': 'unavailable', 'anchors': []}
            if safe_frame:
                record['frame'] = 'PDFium unrotated zero-origin page; normalized top-left'
                textpage = page.get_textpage()
                for index in range(min(textpage.count_rects(), 300)):
                    left, bottom, right, top = textpage.get_rect(index)
                    if not all(math.isfinite(v) for v in (left,bottom,right,top)):
                        continue
                    text = textpage.get_text_bounded(left,bottom,right,top).strip()
                    if text and 0 <= left <= right <= width and 0 <= bottom <= top <= height:
                        record['anchors'].append({'text': text[:300], 'box': [round(left/width,4),
                            round(1-top/height,4),round(right/width,4),round(1-bottom/height,4)]})
                textpage.close()
            records.append(record)
            page.close()
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf')
    parser.add_argument('output')
    parser.add_argument('--pages', help='comma-separated 1-based original pages to render')
    parser.add_argument('--dpi', type=int, default=144)
    parser.add_argument('--crop', help='normalized left,top,right,bottom in displayed page')
    parser.add_argument('--render-only', action='store_true')
    parser.add_argument('--anchors-only', action='store_true')
    args = parser.parse_args()
    if (args.render_only or args.anchors_only) and not args.pages:
        parser.error('--render-only / --anchors-only requires --pages')
    if args.anchors_only:
        print(json.dumps(text_anchors(args.pdf, [int(n) for n in args.pages.split(',')])))
        return
    if not args.render_only:
        result = parse(args.pdf, args.output)
        print(json.dumps({'page_count': result['page_count'], 'ocr_pages': [p['page_number'] for p in result['pages'] if p['needs_ocr']]}))
    if args.pages:
        print(json.dumps(render(args.pdf, args.output, [int(n) for n in args.pages.split(',')], args.dpi,
                                [float(v) for v in args.crop.split(',')] if args.crop else None)))


if __name__ == '__main__':
    import resource
    # macOS rejects RLIMIT_AS updates; the production Linux subprocess enforces it.
    if sys.platform == "linux":
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        limit = min(2_147_483_648, hard) if hard != resource.RLIM_INFINITY else 2_147_483_648
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    resource.setrlimit(resource.RLIMIT_CPU, (150, 150))
    os.umask(0o077)
    main()

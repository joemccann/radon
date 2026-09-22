"""Published chart crops keep a 4% margin of the ink box, not a blank tail."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from research.pipeline import Pipeline, fit_content_margin, ink_fraction_box


LOOSE = [0.05, 0.10, 0.95, 0.90]
# Ink pasted at x 40..960, y 50..400 on a 1000px crop image.
INK = [0.04, 0.05, 0.961, 0.401]


def test_margin_is_four_percent_of_the_content_box():
    assert fit_content_margin(LOOSE, INK) == pytest.approx(
        [0.052844, 0.128768, 0.948056, 0.432032])


def test_margin_is_unchanged_when_the_ink_already_has_it():
    crop = [0.176, 0.284, 0.824, 0.716]
    ink = [0.024 / 0.648, 0.016 / 0.432, 0.624 / 0.648, 0.416 / 0.432]
    assert fit_content_margin(crop, ink) == pytest.approx(crop)


def test_margin_clamps_at_the_page_edge():
    assert fit_content_margin([0, 0, 0.5, 0.5], [0, 0, 1, 1]) == pytest.approx(
        [0, 0, 0.52, 0.52])


def test_margin_refuses_a_crop_that_is_still_too_small():
    assert fit_content_margin([0.40, 0.40, 0.42, 0.42], [0, 0, 1, 1]) is None


def test_ink_box_uses_the_outer_pixel_of_a_real_image(tmp_path):
    image = Image.new("RGB", (1000, 1000), "white")
    image.paste(Image.new("RGB", (921, 351), (20, 20, 20)), (40, 50))
    path = tmp_path / "chart.png"
    image.save(path)
    assert ink_fraction_box(path) == pytest.approx(INK)
    assert ink_fraction_box(tmp_path / "missing.png") is None
    (tmp_path / "notes.txt").write_text("not an image")
    assert ink_fraction_box(tmp_path / "notes.txt") is None


def test_blank_tail_is_refit_before_inspection(tmp_path):
    renders = []
    calls = []

    def render(pdf, out, pages, dpi, crop=None):
        renders.append(list(crop))
        out.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (1000, 1000), "white")
        image.paste(Image.new("RGB", (921, 351), (20, 20, 20)), (40, 50))
        image.save(out / "chart.png")
        return [{"image_file": "chart.png", "width": 1000, "height": 1000, "page_number": pages[0]}]

    reviewer = SimpleNamespace(ask=lambda prompt, images: calls.append(images) or {
        "figures": [{"index": 0, "complete": True, "chart_titles": ["Yields"],
                     "axis_labels": ["bp"], "legend_labels": [], "source_labels": ["FactSet"],
                     "missing_or_clipped": [], "unrelated_prose": False, "reason": "Labels visible"}]})
    pipe = Pipeline(tmp_path, reviewer, None, renderer=render, anchor_reader=lambda *a: [])
    candidate = {"title": "Yields", "content": "Global", "publisher": "Pictet", "claim_key": "yields",
                 "document_date": "2026-09-16", "pages": [2], "tags": ["RATES"],
                 "figures": [{"page": 2, "crop": list(LOOSE), "caption": "2Y yields, 16 Sep 2026"}]}
    original = tmp_path / "original.png"
    original.write_bytes(b"original")
    figures = pipe.prepare_figures(tmp_path / "source.pdf", tmp_path / "charts", candidate, {2: original}, {2: (800, 1100)}, [])
    fitted = [0.052844, 0.128768, 0.948056, 0.432032]
    assert renders[0] == LOOSE
    assert renders[1] == pytest.approx(fitted)
    assert candidate["figures"][0]["crop"] == pytest.approx(fitted)
    assert figures[0][0]["crop"] == pytest.approx(fitted)
    assert Path(calls[0][0][1]).parent.name == "attempt-0-margin"

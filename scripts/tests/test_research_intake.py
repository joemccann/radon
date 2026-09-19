"""Intake v2 orchestration: code settles identity, figures, duplicates and numbers; the model is asked twice."""
import json
from types import SimpleNamespace

import pytest

from research import intake, model

PAGE1 = ("## Economics Research ## 16 September 2026 | 5:20PM EDT # TIC Data: Continued Purchases of US Equities in July. "
         "Foreign investors bought $45bn of US equities in July, the second consecutive month above $40bn. "
         "Official investors added $12bn of Treasuries. Chart 1 shows the 12-month rolling sum.")
PAGE2 = "Chart 1: Net foreign purchases of US equities, 12-month rolling sum (USD bn). Source: Treasury, Goldman Sachs Global Investment Research."


def work(name="tic data.pdf"):
    return {"key": "k" * 64, "folder_date": "2026-09-17",
            "metadata": {"name": name, "id": "id:one", "rev": "r1", "content_hash": "a" * 64,
                         "path_lower": f"/joe mccann/current/2026/september/sep 17/goldman sachs/{name}",
                         "client_modified": "2026-09-17T08:00:00Z"}}


def extractor(pdf, out):
    out.mkdir(parents=True, exist_ok=True)
    (out / "page-0001.md").write_text(PAGE1)
    (out / "page-0002.md").write_text(PAGE2)
    return {"page_count": 2, "source_sha256": "a" * 64, "source_path": str(pdf),
            "pages": [{"page_number": 1, "markdown_file": "page-0001.md"}, {"page_number": 2, "markdown_file": "page-0002.md"}]}


def catalogue(pdf, pages, output_dir, dpi=216):
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "f1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"crop")
    return [{"id": "f1", "page": 2, "bbox": [0.1, 0.2, 0.9, 0.6], "objects": 12, "title": "Net foreign purchases of US equities",
             "source_line": "Source: Treasury, Goldman Sachs Global Investment Research", "image_file": "f1.png", "width": 800, "height": 400}]


def selection(**overrides):
    item = {"title": "Foreign investors bought $45bn of US equities in July", "content": "TIC data show $45bn of net foreign buying of US equities in July, the second consecutive month above $40bn. Official investors added $12bn of Treasuries.",
            "claim_key": "tic-july-equity-buying", "pages": [1], "figure_ids": ["f1"], "tags": ["FLOWS", "EQUITIES"], "text_only": False,
            "captions": {"f1": "Net foreign purchases of US equities, 12-month rolling sum"}}
    item.update(overrides)
    return {"candidates": [item], "reason": "measured flow"}


def verdict(**overrides):
    value = {"supported": True, "material_new_evidence": True, "not_market_ear": True, "no_unresolved_conflicts": True,
             "not_forecast_as_flow": True, "reason": "matches page 1"}
    value.update(overrides)
    return value


class Reviewer:
    model = "test"

    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def ask_text(self, instruction):
        self.calls.append(("text", instruction, ()))
        return self.responses.pop(0)

    def ask(self, instruction, images=()):
        self.calls.append(("multimodal", instruction, tuple(images)))
        return self.responses.pop(0)


@pytest.fixture
def publisher():
    stored = []
    return SimpleNamespace(store_asset=lambda p: stored.append(str(p)) or "/api/newsfeed/research/files/" + "b" * 64 + "." + str(p).rsplit(".", 1)[-1],
                           stored=stored, recent_posts=lambda days=90: [])


def build(tmp_path, reviewer, publisher):
    return intake.Pipeline(tmp_path, reviewer, publisher, extractor=extractor, figure_catalogue=catalogue, pdf_created=lambda pdf: None)


def test_two_model_calls_select_is_text_only_verify_carries_one_crop(tmp_path, publisher):
    reviewer = Reviewer([selection(), verdict()])
    posts = build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    assert [c[0] for c in reviewer.calls] == ["text", "multimodal"]
    assert reviewer.calls[1][2] and reviewer.calls[1][2][0][0].startswith("Figure f1")
    select_prompt = reviewer.calls[0][1]
    assert '"publisher": "Goldman Sachs"' in select_prompt and '"date": "2026-09-16"' in select_prompt and '"dateSource": "text"' in select_prompt
    assert '"id": "f1"' in select_prompt and "12-month rolling sum" in select_prompt
    assert "Foreign investors bought $45bn" in select_prompt, "full page text, not a 1,500 character prefix"
    assert len(posts) == 1
    post = posts[0]
    assert post["id"].startswith("research-") and post["source"]["documentDate"] == "2026-09-16"
    assert post["source"]["dateSource"] == "text" and post["source"]["publisher"] == "Goldman Sachs"
    assert post["source"]["pages"] == [1, 2] and post["source"]["figures"][0]["page"] == 2
    assert post["images"] == [post["source"]["figures"][0]["url"]]
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert review["pipeline"] == "v2" and review["outcome"] == "reviewed" and review["identity"]["series"] == "tic data"


def test_dropped_document_makes_no_model_call(tmp_path, publisher):
    reviewer = Reviewer([])
    posts = build(tmp_path, reviewer, publisher).process(work("gbpusd_en_1666701.pdf"), tmp_path / "r.pdf", [])
    assert posts == [] and reviewer.calls == []
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert review["outcome"] == "dropped" and review["reason_code"] == "DOC_TYPE_FX_PAIR_NOTE"


def test_ungrounded_number_is_held_before_verification(tmp_path, publisher):
    reviewer = Reviewer([selection(title="Foreign investors bought $47bn of US equities in July")])
    posts = build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    assert posts == [] and len(reviewer.calls) == 1
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    held = [a for a in review["audit"] if a.get("held") == "NUMBER_NOT_ON_PAGE"]
    assert held and "$47bn" in json.dumps(held[0])
    assert publisher.stored == []


def test_verify_gate_failure_holds_and_stores_nothing(tmp_path, publisher):
    reviewer = Reviewer([selection(), verdict(material_new_evidence=False)])
    assert build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", []) == []
    assert publisher.stored == []


def test_selector_cannot_invent_figures_pages_or_dates(tmp_path, publisher):
    reviewer = Reviewer([selection(figure_ids=["f9"], pages=[7]), verdict()])
    assert build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", []) == []
    assert len(reviewer.calls) == 1
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert any(a.get("held") == "INVALID_CANDIDATE" for a in review["audit"])


def test_duplicate_of_published_document_is_dropped_before_any_call(tmp_path, publisher):
    reviewer = Reviewer([selection(), verdict()])
    pipe = build(tmp_path, reviewer, publisher)
    assert len(pipe.process(work(), tmp_path / "r.pdf", [])) == 1
    again = dict(work(), key="m" * 64)
    again["metadata"] = dict(again["metadata"], id="id:two")
    assert pipe.process(again, tmp_path / "r.pdf", []) == []
    assert len(reviewer.calls) == 2
    review = json.loads((tmp_path / "evidence" / ("m" * 64) / "review.json").read_text())
    assert review["outcome"] == "dropped" and review["reason_code"] == "DUPLICATE_OF_PUBLISHED"


def test_provider_outage_propagates_untouched(tmp_path, publisher):
    class Outage(Reviewer):
        def ask_text(self, instruction):
            raise model.ModelError("Model ladder exhausted after trying every keyed provider")
    with pytest.raises(model.ModelError):
        build(tmp_path, Outage([]), publisher).process(work(), tmp_path / "r.pdf", [])


def test_text_only_finding_publishes_without_images(tmp_path, publisher):
    reviewer = Reviewer([selection(figure_ids=[], text_only=True, captions={}), verdict()])
    posts = build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    assert len(posts) == 1 and posts[0]["images"] == [] and reviewer.calls[1][2] == ()

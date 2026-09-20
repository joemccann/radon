"""Intake v2 orchestration: code settles identity, figures, duplicates and numbers; the model is asked twice."""
import hashlib
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


def test_ungrounded_number_reaches_verify_with_unmatched_hint(tmp_path, publisher):
    reviewer = Reviewer([selection(title="Foreign investors bought $47bn of US equities in July"), verdict()])
    posts = build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    assert len(reviewer.calls) == 2
    assert "TOKENS NOT MATCHED BY CODE" in reviewer.calls[1][1] and "$47bn" in reviewer.calls[1][1]
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    entry = next(a for a in review["audit"] if a.get("claim_key") == "tic-july-equity-buying")
    assert entry["unmatched"] == ["$47bn"] and "held" not in entry
    assert len(posts) == 1 and posts[0]["id"].startswith("research-")


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


def catalogue_on(page):
    def impl(pdf, pages, output_dir, dpi=216):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "f1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"crop")
        return [{"id": "f1", "page": page, "bbox": [0.1, 0.2, 0.9, 0.6], "objects": 12, "kind": "raster",
                 "title": "Net foreign purchases of US equities",
                 "source_line": "Source: Treasury, Goldman Sachs Global Investment Research",
                 "image_file": "f1.png", "width": 800, "height": 400}]
    return impl


def test_text_only_candidate_on_a_page_with_catalogue_figures_is_held(tmp_path, publisher):
    reviewer = Reviewer([selection(figure_ids=[], text_only=True, captions={}, pages=[1]), verdict()])
    pipe = intake.Pipeline(tmp_path, reviewer, publisher, extractor=extractor,
                           figure_catalogue=catalogue_on(1), pdf_created=lambda pdf: None)
    posts = pipe.process(work(), tmp_path / "r.pdf", [])
    assert posts == [] and publisher.stored == [] and len(reviewer.calls) == 1
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    held = [a for a in review["audit"] if a.get("held") == "TEXT_ONLY_WITH_FIGURES"]
    assert held and held[0]["figures_on_cited_pages"] == ["f1"] and held[0]["claim_key"] == "tic-july-equity-buying"


def test_text_only_candidate_on_chart_free_pages_still_publishes(tmp_path, publisher):
    reviewer = Reviewer([selection(figure_ids=[], text_only=True, captions={}, pages=[1]), verdict()])
    posts = build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    assert len(posts) == 1 and posts[0]["images"] == [] and reviewer.calls[1][2] == ()


def test_operator_note_requeue_reaches_select_with_figure_guidance(tmp_path, publisher):
    post_id = "research-" + "1" * 64
    reviewer = Reviewer([selection(), verdict()])
    noted = dict(work(), note=json.dumps({"kind": "more", "comment": "add the chart", "post_id": post_id,
                                          "title": "Foreign investors bought $45bn"}))
    posts = build(tmp_path, reviewer, publisher).process(noted, tmp_path / "r.pdf", [])
    assert posts[0]["id"] == post_id
    assert "REVISE THIS PUBLISHED ITEM" in reviewer.calls[0][1]
    assert "attaching the supporting figure from the catalogue" in reviewer.calls[0][1]


def test_select_instruction_and_catalogue_include_kind_and_text_only_hold_rule(tmp_path, publisher):
    assert "a text_only candidate that cites a page with catalogue figures is held for operator review" in intake.SELECT_INSTRUCTION
    reviewer = Reviewer([selection(), verdict()])
    build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    assert '"kind"' in reviewer.calls[0][1]


def test_select_and_verify_prompts_are_targets_not_hard_length_or_prematched_numbers():
    assert "Targets, not gates" in intake.SELECT_INSTRUCTION
    assert "at most 8 pages" not in intake.SELECT_INSTRUCTION
    assert "title<=180" not in intake.SELECT_INSTRUCTION
    assert "The numbers have already been matched" not in intake.VERIFY_INSTRUCTION
    assert "lists the ones it could not find" in intake.VERIFY_INSTRUCTION


def test_long_title_body_and_claim_key_reach_verify(tmp_path, publisher):
    title = "Foreign investors bought $45bn of US equities in July " + ("x" * 160)
    content = selection()["candidates"][0]["content"] + (" more context." * 220)
    claim_key = "tic-july-equity-buying-" + ("k" * 180)
    assert len(title) >= 200 and len(content) >= 3000 and len(claim_key) >= 200
    reviewer = Reviewer([selection(title=title, content=content, claim_key=claim_key), verdict()])
    posts = build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    assert len(reviewer.calls) == 2 and len(posts) == 1
    expected = "research-" + hashlib.sha256(("id:one\0" + claim_key.strip().lower()).encode()).hexdigest()
    assert posts[0]["id"] == expected


def test_missing_caption_defaults_to_catalogue_title(tmp_path, publisher):
    reviewer = Reviewer([selection(captions={}), verdict()])
    posts = build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    assert posts[0]["source"]["figures"][0]["caption"] == "Net foreign purchases of US equities"


def test_long_caption_publishes_unchanged(tmp_path, publisher):
    caption = "x" * 400
    reviewer = Reviewer([selection(captions={"f1": caption}), verdict()])
    posts = build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    assert posts[0]["source"]["figures"][0]["caption"] == caption


def test_em_dash_title_reaches_verify(tmp_path, publisher):
    reviewer = Reviewer([selection(title="Foreign investors bought $45bn of US equities in July \u2014 TIC"), verdict()])
    posts = build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert len(reviewer.calls) == 2 and len(posts) == 1
    assert not any(a.get("held") == "INVALID_CANDIDATE" for a in review["audit"])


def _candidate(**overrides):
    item = {"title": "T", "content": "C", "claim_key": "k", "pages": [1], "figure_ids": [],
            "captions": {}, "tags": ["FLOWS"], "text_only": True}
    item.update(overrides)
    return item


def test_validate_candidate_drops_length_and_page_ceiling_keeps_shape():
    from research.pipeline import EvidenceError
    cat = {"f1": {"id": "f1", "page": 9, "title": "Nine", "source_line": "Source line"}}
    long = _candidate(title="T" * 200, content="C" * 3000, claim_key="K" * 200, pages=list(range(1, 13)))
    assert intake.validate_candidate(long, 12, {})["claim_key"] == "K" * 200
    attached = _candidate(pages=list(range(1, 9)), figure_ids=["f1"], text_only=False, captions={"f1": "ok"})
    assert 9 in intake.validate_candidate(attached, 12, cat)["pages"]
    untitled = _candidate(figure_ids=["f1"], text_only=False, captions={})
    untitled_cat = {"f1": {"id": "f1", "page": 1, "title": "", "source_line": ""}}
    assert intake.validate_candidate(untitled, 2, untitled_cat)["captions"]["f1"] == "Figure, page 1"
    for pages in ([], [0], [13], [True], [1.0]):
        with pytest.raises(EvidenceError, match="Invalid evidence pages"):
            intake.validate_candidate(_candidate(pages=pages), 12, {})
    for key in ("title", "content", "claim_key"):
        with pytest.raises(EvidenceError, match=f"Invalid {key}"):
            intake.validate_candidate(_candidate(**{key: ""}), 12, {})
    with pytest.raises(EvidenceError, match="Unknown figure id"):
        intake.validate_candidate(_candidate(figure_ids=["f9"], text_only=False), 12, cat)
    with pytest.raises(EvidenceError, match="Unknown figure id"):
        intake.validate_candidate(_candidate(figure_ids=["f1"] * 7, text_only=False), 12, cat)

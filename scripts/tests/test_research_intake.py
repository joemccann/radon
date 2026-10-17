"""Intake v2 orchestration: code settles identity, figures, duplicates and numbers; the model is asked twice."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from research import intake, model, publish

PAGE1 = ("## Economics Research ## 16 September 2026 | 5:20PM EDT # TIC Data: Continued Purchases of US Equities in July. "
         "Foreign investors bought $45bn of US equities in July, the second consecutive month above $40bn. "
         "Official investors added $12bn of Treasuries. Chart 1 shows the 12-month rolling sum.")
PAGE2 = "Chart 1: Net foreign purchases of US equities, 12-month rolling sum (USD bn). Source: Treasury, Goldman Sachs Global Investment Research."


def work(name="tic data.pdf", folder="goldman sachs", folder_date="2026-09-17"):
    return {"key": "k" * 64, "folder_date": folder_date,
            "metadata": {"name": name, "id": "id:one", "rev": "r1", "content_hash": "a" * 64,
                         "path_lower": f"/joe mccann/current/2026/september/sep 17/{folder}/{name}",
                         "client_modified": "2026-09-17T08:00:00Z"}}


def bofa_flow_show(name="the flow show friday, 19 september 2026.pdf"):
    return work(name, folder="bank of america", folder_date="2026-09-19")


def db_positioning(name="db positioning data - 19 september 2026.pdf"):
    return work(name, folder="deutsche bank", folder_date="2026-09-19")


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
             "not_forecast_as_flow": True, "figures_complete": True, "reason": "matches page 1"}
    value.update(overrides)
    return value


def offered_ids(instruction):
    return json.loads(instruction.split("\nATTACHED IDS:\n", 1)[1])


def match(passing=None, **overrides):
    """A FIGURE MATCH reply: every id in `passing` is a labelled chart of the finding, the rest are not."""
    def reply(offered):
        figures = []
        for i in offered:
            ok = passing is None or i in passing
            figure = {"id": i, "is_chart": ok, "complete_figure": ok, "visible_text": ["Net foreign purchases of US equities"] if ok else [],
                      "shows": "chart" if ok else "logo", "supports_finding": ok, "caption": f"{i} matched caption"}
            figure.update(overrides.get(i, {}))
            figures.append(figure)
        return {"figures": figures}
    reply.figure_match = True
    return reply


class Reviewer:
    """Scripted reviewer. FIGURE MATCH calls are answered by a queued match(...) or, if none is next, pass every figure."""
    model = "test"

    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def ask_text(self, instruction):
        self.calls.append(("text", instruction, ()))
        return self.responses.pop(0)

    def ask(self, instruction, images=()):
        if instruction.startswith(intake.FIGURE_MATCH_INSTRUCTION):
            self.calls.append(("match", instruction, tuple(images)))
            queued = self.responses[0] if self.responses else None
            reply = self.responses.pop(0) if getattr(queued, "figure_match", False) else match()
            return reply(offered_ids(instruction))
        self.calls.append(("multimodal", instruction, tuple(images)))
        return self.responses.pop(0)


class EmptyChartAgent:
    """Other orchestration tests keep chart generation offline and explicit."""
    env = {}

    def __init__(self, **kwargs):
        pass

    def ask_text(self, instruction):
        return {'charts': [], 'reason': 'No chart selected by this orchestration fixture'}

    def ask(self, instruction, images=()):
        raise AssertionError('Empty chart plans must not invoke image review')


@pytest.fixture
def publisher():
    stored = []
    return SimpleNamespace(store_asset=lambda p: stored.append(str(p)) or "/api/newsfeed/research/files/" + "b" * 64 + "." + str(p).rsplit(".", 1)[-1],
                           stored=stored, recent_posts=lambda days=90: [])


def build(tmp_path, reviewer, publisher):
    return intake.Pipeline(tmp_path, reviewer, publisher, extractor=extractor, figure_catalogue=catalogue, pdf_created=lambda pdf: None,
                           chart_producer=EmptyChartAgent(), chart_reviewer=EmptyChartAgent())


def test_two_model_calls_select_is_text_only_verify_carries_one_crop(tmp_path, publisher):
    reviewer = Reviewer([selection(), verdict()])
    posts = build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    assert [c[0] for c in reviewer.calls] == ["text", "match", "multimodal"]
    assert reviewer.calls[1][2][0][0].startswith("Figure f1"), "FIGURE MATCH looks at the crop"
    assert reviewer.calls[2][2] and reviewer.calls[2][2][0][0].startswith("Figure f1")
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


STOCK_PAGE = ("**Estimates Revised** **US Equity Research** 15 September 2026 RatingPrice Target **BUY US$11.00** "
              "Price **ASPI-NASDAQ US$3.16** We reiterate our Buy rating after the quarter.")


def stock_extractor(pdf, out):
    out.mkdir(parents=True, exist_ok=True)
    (out / "page-0001.md").write_text(STOCK_PAGE)
    return {"page_count": 1, "source_sha256": "b" * 64, "source_path": str(pdf),
            "pages": [{"page_number": 1, "markdown_file": "page-0001.md"}]}


def build_stock(tmp_path, reviewer, publisher, book):
    return intake.Pipeline(tmp_path, reviewer, publisher, extractor=stock_extractor, figure_catalogue=catalogue,
                           pdf_created=lambda pdf: None, book_tickers=book)


def test_single_stock_off_book_is_dropped_without_a_model_call(tmp_path, publisher):
    reviewer = Reviewer([])
    pipe = build_stock(tmp_path, reviewer, publisher, lambda: frozenset({"NVDA"}))
    posts = pipe.process(work("aspi - a sum of its parts.pdf"), tmp_path / "r.pdf", [])
    assert posts == [] and reviewer.calls == []
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert review["outcome"] == "dropped" and review["reason_code"] == "SINGLE_STOCK_NOT_IN_BOOK"


def test_unknown_publisher_is_held_before_any_model_call(tmp_path, publisher):
    # PAGE1/PAGE2 name no publisher the scorer accepts; filed under a topic folder the source is unknown.
    reviewer = Reviewer([selection(), verdict()])
    posts = build(tmp_path, reviewer, publisher).process(work(folder="other"), tmp_path / "r.pdf", [])
    assert posts == [] and reviewer.calls == []
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert review["identity"]["publisher"] == "unknown"
    assert review["outcome"] == "held"
    row = publish.outcome_row(work(folder="other"), review)
    assert row["outcome"] == "held" and json.loads(row["reason_codes"]) == ["PUBLISHER_UNKNOWN"]


def test_publisher_found_in_a_topic_folder_reaches_selection(tmp_path, publisher):
    def named(pdf, out):
        result = extractor(pdf, out)
        (out / "page-0002.md").write_text(PAGE2 + "\n© 2026 Northern Trust Corporation. All rights reserved.")
        return result
    reviewer = Reviewer([{"candidates": [], "reason": "nothing new"}])
    pipe = intake.Pipeline(tmp_path, reviewer, publisher, extractor=named, figure_catalogue=catalogue, pdf_created=lambda pdf: None)
    pipe.process(work(folder="other"), tmp_path / "r.pdf", [])
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert review["identity"]["publisher"] == "Northern Trust" and [c[0] for c in reviewer.calls] == ["text"]


def test_single_stock_in_book_reaches_selection(tmp_path, publisher):
    reviewer = Reviewer([{"candidates": [], "reason": "nothing new"}])
    pipe = build_stock(tmp_path, reviewer, publisher, lambda: frozenset({"ASPI"}))
    assert pipe.process(work("aspi - a sum of its parts.pdf"), tmp_path / "r.pdf", []) == []
    assert [c[0] for c in reviewer.calls] == ["text"]
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert review["outcome"] == "reviewed" and review["triage"]["decision"] == "review"


def test_book_is_reloaded_per_document_so_membership_tracks_the_live_book(tmp_path, publisher):
    # Watchlist and portfolio are dynamic: the same series must drop before the
    # operator holds the name and reach review after, within one pipeline instance.
    books = [frozenset(), frozenset({"ASPI"})]
    reviewer = Reviewer([{"candidates": [], "reason": "nothing new"}])
    pipe = build_stock(tmp_path, reviewer, publisher, lambda: books.pop(0))
    assert pipe.process(work("aspi - a sum of its parts.pdf"), tmp_path / "r.pdf", []) == []
    first = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert first["outcome"] == "dropped" and first["reason_code"] == "SINGLE_STOCK_NOT_IN_BOOK"
    later = dict(work("aspi - a sum of its parts.pdf"), key="n" * 64)
    later["metadata"] = dict(later["metadata"], id="id:two", rev="r2")
    assert pipe.process(later, tmp_path / "r.pdf", []) == []
    second = json.loads((tmp_path / "evidence" / ("n" * 64) / "review.json").read_text())
    assert second["outcome"] == "reviewed" and second["triage"]["decision"] == "review"
    assert books == []


def test_book_is_not_consulted_for_non_single_stock_documents(tmp_path, publisher):
    loads = []
    reviewer = Reviewer([{"candidates": [], "reason": "nothing new"}])
    pipe = intake.Pipeline(tmp_path, reviewer, publisher, extractor=extractor, figure_catalogue=catalogue,
                           pdf_created=lambda pdf: None, book_tickers=lambda: loads.append(1) or frozenset())
    pipe.process(work(), tmp_path / "r.pdf", [])
    assert loads == []


def test_ungrounded_number_reaches_verify_with_unmatched_hint(tmp_path, publisher):
    reviewer = Reviewer([selection(title="Foreign investors bought $47bn of US equities in July"), verdict()])
    posts = build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    assert len(reviewer.calls) == 3
    assert "TOKENS NOT MATCHED BY CODE" in reviewer.calls[2][1] and "$47bn" in reviewer.calls[2][1]
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    entry = next(a for a in review["audit"] if a.get("claim_key") == "tic-july-equity-buying" and "unmatched" in a)
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
    assert len(reviewer.calls) == 3
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
    assert posts[0]['source']['chartGeneration']['status'] == 'no_comparable_data'
    assert 'charts' not in posts[0]['source']


def test_text_only_without_a_reviewed_png_does_not_store_a_browser_plan(tmp_path, publisher):
    content = "Name A yields 3.5-4.0%. Name B sits at 2.1%."
    reviewer = Reviewer([selection(title="Two printed yields", content=content, figure_ids=[], text_only=True, captions={}), verdict()])
    posts = build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    assert 'charts' not in posts[0]['source']
    assert posts[0]['source']['chartGeneration']['status'] == 'no_comparable_data'


def test_figured_finding_does_not_store_a_generated_chart(tmp_path, publisher):
    content = "Name A yields 3.5-4.0%. Name B sits at 2.1%."
    reviewer = Reviewer([selection(title="Two printed yields", content=content), verdict()])
    posts = build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    assert posts[0]["images"] and "charts" not in posts[0]["source"]


def catalogue_on(page):
    def impl(pdf, pages, output_dir, dpi=216):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "f1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"crop")
        return [{"id": "f1", "page": page, "bbox": [0.1, 0.2, 0.9, 0.6], "objects": 12, "kind": "raster",
                 "title": "Net foreign purchases of US equities",
                 "source_line": "Source: Treasury, Goldman Sachs Global Investment Research",
                 "image_file": "f1.png", "width": 800, "height": 400}]
    return impl


def test_text_only_on_cited_figures_reselect_attaches_and_continues(tmp_path, publisher):
    first = selection(figure_ids=[], text_only=True, captions={}, pages=[1])
    reviewer = Reviewer([first, selection(), verdict()])
    pipe = intake.Pipeline(tmp_path, reviewer, publisher, extractor=extractor,
                           figure_catalogue=catalogue_on(1), pdf_created=lambda pdf: None)
    posts = pipe.process(work(), tmp_path / "r.pdf", [])
    assert [c[0] for c in reviewer.calls] == ["text", "text", "match", "multimodal"]
    assert "RESELECT" in reviewer.calls[1][1] and "f1" in reviewer.calls[1][1]
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert not any(a.get("held") == "TEXT_ONLY_WITH_FIGURES" for a in review["audit"])
    first_miss = next(a for a in review["audit"] if a.get("text_only_with_figures"))
    assert first_miss["figures_on_cited_pages"] == ["f1"] and "held" not in first_miss
    attached = next(a for a in review["audit"] if a.get("reselect") == "attached")
    assert attached["figure_ids"] == ["f1"]
    assert len(posts) == 1 and posts[0]["images"]


def test_text_only_on_cited_figures_reselect_still_text_only_is_held(tmp_path, publisher):
    text_only = selection(figure_ids=[], text_only=True, captions={}, pages=[1])
    unrelated_chart = match(passing=set(), f1={'visual_type': 'chart', 'is_chart': True,
        'complete_figure': True, 'visible_text': ['Unrelated data'], 'shows': 'Chart of an unrelated market'})
    reviewer = Reviewer([text_only, text_only, unrelated_chart])
    pipe = intake.Pipeline(tmp_path, reviewer, publisher, extractor=extractor,
                           figure_catalogue=catalogue_on(1), pdf_created=lambda pdf: None)
    posts = pipe.process(work(), tmp_path / "r.pdf", [])
    assert posts == [] and publisher.stored == [] and [c[0] for c in reviewer.calls] == ["text", "text", "match"]
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    first_miss = next(a for a in review["audit"] if a.get("text_only_with_figures") and "held" not in a)
    assert first_miss["figures_on_cited_pages"] == ["f1"]
    held = [a for a in review['audit'] if a.get('held') in ('TEXT_ONLY_WITH_FIGURES', 'FIGURE_MISMATCH')]
    assert held and held[0]['claim_key'] == 'tic-july-equity-buying'


def test_text_only_on_cited_figures_reselect_bare_candidate_attaches(tmp_path, publisher):
    first = selection(figure_ids=[], text_only=True, captions={}, pages=[1])
    reviewer = Reviewer([first, selection()["candidates"][0], verdict()])
    pipe = intake.Pipeline(tmp_path, reviewer, publisher, extractor=extractor,
                           figure_catalogue=catalogue_on(1), pdf_created=lambda pdf: None)
    posts = pipe.process(work(), tmp_path / "r.pdf", [])
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert not any(a.get("held") == "TEXT_ONLY_WITH_FIGURES" for a in review["audit"])
    assert next(a for a in review["audit"] if a.get("reselect") == "attached")["figure_ids"] == ["f1"]
    assert len(posts) == 1 and posts[0]["images"]


def test_text_only_on_cited_figures_reselect_invalid_figure_is_held(tmp_path, publisher):
    reviewer = Reviewer([
        selection(figure_ids=[], text_only=True, captions={}, pages=[1]),
        selection(figure_ids=["f9"], pages=[1]),
    ])
    pipe = intake.Pipeline(tmp_path, reviewer, publisher, extractor=extractor,
                           figure_catalogue=catalogue_on(1), pdf_created=lambda pdf: None)
    posts = pipe.process(work(), tmp_path / "r.pdf", [])
    assert posts == [] and [c[0] for c in reviewer.calls] == ["text", "text"]
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    held = [a for a in review["audit"] if a.get("held") == "TEXT_ONLY_WITH_FIGURES"]
    assert held and held[0]["reselect"] is True
    assert not any(a.get("held") == "INVALID_CANDIDATE" for a in review["audit"])


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


def test_select_instruction_and_catalogue_include_kind_and_text_only_reselect_rule(tmp_path, publisher):
    assert "held for operator review" not in intake.SELECT_INSTRUCTION
    assert "asked once" in intake.SELECT_INSTRUCTION
    reviewer = Reviewer([selection(), verdict()])
    build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    assert '"kind"' in reviewer.calls[0][1]


def test_select_and_verify_prompts_are_targets_not_hard_length_or_prematched_numbers():
    assert "Targets, not gates" in intake.SELECT_INSTRUCTION
    assert "at most 8 pages" not in intake.SELECT_INSTRUCTION
    assert "title<=180" not in intake.SELECT_INSTRUCTION
    assert "The numbers have already been matched" not in intake.VERIFY_INSTRUCTION
    assert "lists the ones it could not find" in intake.VERIFY_INSTRUCTION


def test_select_instruction_humanizes_title_and_body_without_length_holds():
    text = intake.SELECT_INSTRUCTION
    assert "the finding in few words" in text
    assert "no throat-clearing" in text
    assert "no filler" in text
    assert "hedging stacks" in text
    assert "delve" in text
    assert "landscape" in text
    assert "it is important to note" in text
    assert "template bank-speak" in text
    assert "copied exactly" in text
    assert "title<=180" not in text
    assert "asked once" in text


def test_long_title_body_and_claim_key_reach_verify(tmp_path, publisher):
    title = "Foreign investors bought $45bn of US equities in July " + ("x" * 160)
    content = selection()["candidates"][0]["content"] + (" more context." * 220)
    claim_key = "tic-july-equity-buying-" + ("k" * 180)
    assert len(title) >= 200 and len(content) >= 3000 and len(claim_key) >= 200
    reviewer = Reviewer([selection(title=title, content=content, claim_key=claim_key), verdict()])
    posts = build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    assert len(reviewer.calls) == 3 and len(posts) == 1
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
    assert len(reviewer.calls) == 3 and len(posts) == 1
    assert not any(a.get("held") == "INVALID_CANDIDATE" for a in review["audit"])


def _candidate(**overrides):
    item = {"title": "T", "content": "C", "claim_key": "k", "pages": [1], "figure_ids": [],
            "captions": {}, "tags": ["FLOWS"], "text_only": True}
    item.update(overrides)
    return item


def test_force_include_overrides_a_series_denylist_drop(tmp_path, publisher, monkeypatch):
    monkeypatch.setattr(intake.learn, "load_rules", lambda root: {"series_deny": {"the flow show"},
                                                                  "doc_type_drop": set(), "publisher_deny": set()})
    reviewer = Reviewer([selection(), verdict()])
    posts = build(tmp_path, reviewer, publisher).process(bofa_flow_show(), tmp_path / "r.pdf", [])
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert review["force_include"] is True and review["triage"]["overridden"] is True
    assert review["outcome"] == "reviewed" and len(posts) == 1
    assert "always in scope" in reviewer.calls[0][1]


def test_force_include_empty_select_reselects_once_then_publishes(tmp_path, publisher):
    reviewer = Reviewer([{"candidates": [], "reason": "nothing measured"}, selection(), verdict()])
    posts = build(tmp_path, reviewer, publisher).process(bofa_flow_show(), tmp_path / "r.pdf", [])
    assert [c[0] for c in reviewer.calls] == ["text", "text", "match", "multimodal"]
    assert "empty candidates is incorrect" in reviewer.calls[0][1]
    assert "RESELECT" in reviewer.calls[1][1] and "always in scope" in reviewer.calls[1][1]
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert any(a.get("force_include_reselect") for a in review["audit"])
    assert len(posts) == 1 and posts[0]["source"]["publisher"] == "BofA Global Research"


def test_force_include_empty_after_reselect_allows_no_candidates(tmp_path, publisher):
    reviewer = Reviewer([{"candidates": [], "reason": "first"}, {"candidates": [], "reason": "second"}])
    posts = build(tmp_path, reviewer, publisher).process(db_positioning(), tmp_path / "r.pdf", [])
    assert posts == [] and [c[0] for c in reviewer.calls] == ["text", "text"]
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert review["force_include"] is True and review["outcome"] == "reviewed"
    assert review["selection"]["candidates"] == []


def test_wrong_publisher_matching_title_does_not_force(tmp_path, publisher):
    reviewer = Reviewer([{"candidates": [], "reason": "nothing new"}])
    posts = build(tmp_path, reviewer, publisher).process(work("the flow show friday, 19 september 2026.pdf"),
                                                         tmp_path / "r.pdf", [])
    assert posts == [] and [c[0] for c in reviewer.calls] == ["text"]
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert review["force_include"] is False
    assert not any(a.get("force_include_reselect") for a in review["audit"])


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


def logo_and_chart(pdf, pages, output_dir, dpi=216):
    """BTIG 2026-10-07 page 2: a letterhead crop (f1) sits beside the chart the copy is about (f2)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("f1.png", "f2.png"):
        (output_dir / name).write_bytes(b"\x89PNG\r\n\x1a\n" + name.encode())
    return [{"id": "f1", "page": 2, "bbox": [0.6965, 0.0371, 0.8918, 0.0736], "objects": 1, "kind": "raster",
             "title": None, "source_line": None, "image_file": "f1.png", "width": 358, "height": 87},
            {"id": "f2", "page": 2, "bbox": [0.1095, 0.1005, 0.8104, 0.433], "objects": 1, "kind": "raster",
             "title": None, "source_line": None, "image_file": "f2.png", "width": 1287, "height": 790}]


def build_logo(tmp_path, reviewer, publisher):
    return intake.Pipeline(tmp_path, reviewer, publisher, extractor=extractor, figure_catalogue=logo_and_chart,
                           pdf_created=lambda pdf: None)


def audit_of(tmp_path):
    return json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())["audit"]


def test_letterhead_picked_blind_is_replaced_by_the_chart_the_copy_describes(tmp_path, publisher):
    reviewer = Reviewer([selection(figure_ids=["f1"], pages=[1, 2], captions={"f1": "TIC equity purchases"}),
                         match(passing={"f2"}), verdict()])
    posts = build_logo(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    assert [c[0] for c in reviewer.calls] == ["text", "match", "multimodal"]
    assert [label for label, _ in reviewer.calls[1][2]] == ["Figure f1, source page 2", "Figure f2, source page 2"]
    assert offered_ids(reviewer.calls[1][1]) == ["f1", "f2"] and "Foreign investors bought $45bn" in reviewer.calls[1][1]
    assert [str(path).endswith("f2.png") for _, path in reviewer.calls[2][2]] == [True], "VERIFY sees only the matched chart"
    assert len(posts) == 1 and len(posts[0]["images"]) == 1
    assert posts[0]["source"]["figures"][0]["caption"] == "f2 matched caption"
    assert [p for p in publisher.stored if p.endswith(".png")] == [str(tmp_path / "evidence" / ("k" * 64) / "figures" / "f2.png")]
    entry = next(a for a in audit_of(tmp_path) if "figure_match" in a)
    assert entry["selected"] == ["f1"] and entry["attached"] == ["f2"] and "held" not in entry


def test_selected_chart_that_matches_is_kept_without_adding_others(tmp_path, publisher):
    reviewer = Reviewer([selection(figure_ids=["f2"], pages=[2], captions={"f2": "selector caption"}), match(), verdict()])
    posts = build_logo(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    assert [f["caption"] for f in posts[0]["source"]["figures"]] == ["selector caption"]


def test_no_figure_showing_the_finding_holds_and_publishes_nothing(tmp_path, publisher):
    reviewer = Reviewer([selection(figure_ids=["f1"], pages=[2]), match(passing=set())])
    posts = build_logo(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    assert posts == [] and not any(p.endswith(".png") for p in publisher.stored)
    assert [c[0] for c in reviewer.calls] == ["text", "match"], "no VERIFY for a mismatched figure"
    assert any(a.get("held") == "FIGURE_MISMATCH" for a in audit_of(tmp_path))


def test_a_chart_claim_without_transcribed_labels_does_not_pass():
    claimed = {"figures": [{"id": "f1", "is_chart": True, "complete_figure": True, "supports_finding": True, "visible_text": [], "shows": "x"},
                           {"id": "f2", "is_chart": True, "complete_figure": True, "supports_finding": True, "visible_text": [" "], "shows": "x"},
                           {"id": "f3", "is_chart": False, "supports_finding": True, "visible_text": ["BTIG"], "shows": "logo"},
                           {"id": "f9", "is_chart": True, "complete_figure": True, "supports_finding": True, "visible_text": ["RSI (14)"], "shows": "x"},
                           {"id": "f4", "is_chart": True, "complete_figure": True, "supports_finding": True, "visible_text": ["RSI (14)"], "shows": "x"}]}
    assert set(intake.figure_match_passed(claimed, {"f1", "f2", "f3", "f4"})) == {"f4"}
    assert intake.figure_match_passed({"figures": "nope"}, {"f1"}) == {}
    assert intake.figure_match_passed(None, {"f1"}) == {}


def test_malformed_figure_match_holds(tmp_path, publisher):
    bad = lambda offered: {"verdict": "looks fine"}
    bad.figure_match = True
    reviewer = Reviewer([selection(figure_ids=["f2"], pages=[2]), bad])
    assert build_logo(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", []) == []
    assert any(a.get("held") == "FIGURE_MISMATCH" for a in audit_of(tmp_path))


def test_select_catalogue_carries_each_figure_bbox(tmp_path, publisher):
    reviewer = Reviewer([selection(figure_ids=["f2"], pages=[2]), verdict()])
    build_logo(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", [])
    assert '"bbox": [0.6965, 0.0371, 0.8918, 0.0736]' in reviewer.calls[0][1]


@pytest.mark.parametrize('has_decorative_assets', [False, True])
def test_generated_png_is_reviewed_by_separate_agent_before_publication(tmp_path, publisher, has_decorative_assets):
    from research.generated_charts import REVIEW_GATES
    plan = {'kind': 'bar', 'title': 'Foreign purchases by asset', 'unit': '$bn', 'axis': [0, 50], 'ticks': [0, 10, 20, 30, 40, 50],
            'bars': [{'id': 'equities', 'label': 'US equities', 'value': 45}, {'id': 'treasuries', 'label': 'Treasuries', 'value': 12}]}
    producer = Reviewer([{'charts': [{'plan': plan, 'evidence': [{'id': row['id'], 'page': 1, 'source_quote': PAGE1} for row in plan['bars']]}]}])
    image_agent = Reviewer([{**{key: True for key in REVIEW_GATES}, 'visible_data': plan['bars'], 'reason': 'Exact sourced values, labelled units and zero baseline.',
                             'visible_attribution':{'publisher':'Goldman Sachs','radon_url':'https://radon.run'}}])
    def renderer(plan, path, **kwargs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'\x89PNG\r\n\x1a\nrendered pixels')
        return {'path': str(path), 'width': 2400, 'height': 1440, 'renderer': 'test-renderer'}
    text_only = selection(figure_ids=[], text_only=True, captions={}, pages=[1, 2] if has_decorative_assets else [1])
    replies = [text_only, text_only, nonchart_reply(), verdict()] if has_decorative_assets else [text_only, verdict()]
    claim_agent = Reviewer(replies)
    figures = decorative_catalogue() if has_decorative_assets else lambda *a: []
    pipe = intake.Pipeline(tmp_path, claim_agent, publisher, extractor=extractor, figure_catalogue=figures,
                           pdf_created=lambda pdf: None, chart_producer=producer, chart_reviewer=image_agent, chart_renderer=renderer)
    posts = pipe.process(work(), tmp_path / 'r.pdf', [])
    assert posts[0]['images'][0].endswith('.png')
    assert posts[0]['source']['figures'][0]['generated'] is True
    assert posts[0]['source']['chartGeneration']['status'] == 'passed'
    assert posts[0]['source']['chartGeneration']['charts'][0]['review']['visible_data'] == plan['bars']
    assert image_agent.calls[0][2][0][1].read_bytes().startswith(b'\x89PNG')
    assert len(claim_agent.calls) == (4 if has_decorative_assets else 2) and len(image_agent.calls) == 1
    assert claim_agent.calls[-1][2] == (), 'Claim VERIFY receives no decorative image'
    assert not any(path.endswith('/figures/f1.png') or path.endswith('/figures/f2.png') for path in publisher.stored)


def test_rejected_generated_image_preserves_text_and_disables_browser_fallback(tmp_path, publisher):
    producer = Reviewer([{'charts': [{'plan': {'kind': 'bar', 'title': 'Wrong', 'unit': '%', 'axis': [4, 6], 'ticks': [4, 5, 6],
                           'bars': [{'id': 'a', 'label': 'A', 'value': 4.15}, {'id': 'b', 'label': 'B', 'value': 4.35}]}, 'evidence': []}]}])
    claims = Reviewer([selection(figure_ids=[], text_only=True, captions={}), verdict()])
    pipe = intake.Pipeline(tmp_path, claims, publisher, extractor=extractor, figure_catalogue=lambda *a: [],
                           pdf_created=lambda pdf: None, chart_producer=producer, chart_reviewer=Reviewer([]))
    posts = pipe.process(work(), tmp_path / 'r.pdf', [])
    assert len(posts) == 1 and posts[0]['images'] == []
    assert posts[0]['source']['chartGeneration']['status'] == 'rejected'
    assert 'charts' not in posts[0]['source']


def test_optional_chart_budget_does_not_discard_verified_article(tmp_path, publisher):
    from research.pipeline import DocumentDeadlineExceeded
    reviewer = Reviewer([selection(figure_ids=[], text_only=True, captions={}), verdict()])
    pipe = intake.Pipeline(tmp_path, reviewer, publisher, extractor=extractor, figure_catalogue=lambda *a: [],
                           pdf_created=lambda pdf: None, chart_producer=EmptyChartAgent(), chart_reviewer=EmptyChartAgent())
    original = pipe._guard_call
    def guard(stage):
        if stage == 'chart-plan':
            raise DocumentDeadlineExceeded('Optional chart budget exhausted')
        original(stage)
    pipe._guard_call = guard
    posts = pipe.process(work(), tmp_path / 'r.pdf', [])
    assert len(posts) == 1 and posts[0]['images'] == []
    assert posts[0]['source']['chartGeneration']['status'] == 'unavailable'
    assert posts[0]['source']['chartGeneration']['attempts'] == [{'status':'budget_unavailable'}]


@pytest.mark.parametrize('role,labels', [
    ('publisher_logo', ['LSEG', 'DATA & ANALYTICS']),
    ('publisher_logo', ['LSEG DATA & ANALYTICS']),
    ('legal_owner', ['© 2026 London Stock Exchange Group plc. All rights reserved.']),
])
def test_publisher_logo_is_read_from_boundary_page_pixels_when_text_has_no_brand(tmp_path, publisher, role, labels):
    identity = intake.identify.identify({1:'THE DAY AHEAD',11:'Closing page'}, work()['metadata'] | {'path_lower':'/joe mccann/current/2026/october/oct 9/other/report.pdf'}, '2026-10-09')
    assert identity.publisher == 'unknown'
    logo = Reviewer([{'publisher':'LSEG','page':11,'role':role,'identified_as_publisher':True,
                      'readable':True,'no_conflicts':True,'visible_text':labels}])
    def page_renderer(pdf, output, pages):
        output.mkdir(parents=True,exist_ok=True)
        values=[]
        for page in pages:
            name=f'page-{page:04d}.png';(output/name).write_bytes(b'\x89PNG\r\n\x1a\nlogo')
            values.append({'page_number':page,'image_file':name})
        return values
    pipe=intake.Pipeline(tmp_path, Reviewer([]), publisher, publisher_reviewer=logo,publisher_renderer=page_renderer)
    pipe._deadline=pipe.clock()+600
    audit={}
    resolved=pipe._resolve_publisher(identity,tmp_path/'r.pdf',{1:'THE DAY AHEAD',11:'Closing page'},11,tmp_path/'publisher',audit)
    assert resolved.publisher=='LSEG' and resolved.publisher_page==11
    assert resolved.publisher_quote == '\n'.join(labels)
    assert len(logo.calls[0][2])==2
    assert audit['publisher_identification']['status']=='verified'


def test_ambiguous_body_brand_never_becomes_publisher(tmp_path,publisher):
    identity=intake.identify.identify({1:'Market commentary'}, work()['metadata'] | {'path_lower':'/joe mccann/current/2026/october/oct 9/other/report.pdf'}, '2026-10-09')
    def renderer(pdf,output,pages):
        output.mkdir(parents=True,exist_ok=True);(output/'page.png').write_bytes(b'\x89PNG\r\n\x1a\nlogo')
        return [{'page_number':1,'image_file':'page.png'}]
    reviewer=Reviewer([{'publisher':'LSEG','page':1,'role':'quoted_company','identified_as_publisher':True,
                       'readable':True,'no_conflicts':True,'visible_text':['LSEG']}])
    pipe=intake.Pipeline(tmp_path,Reviewer([]),publisher,publisher_reviewer=reviewer,publisher_renderer=renderer)
    pipe._deadline=pipe.clock()+600
    audit={}
    assert pipe._resolve_publisher(identity,tmp_path/'r.pdf',{1:'Market commentary'},1,tmp_path/'publisher',audit).publisher=='unknown'
    assert audit['publisher_identification']['status']=='unresolved'


@pytest.mark.parametrize('role,labels', [
    ('publisher_logo', ['LSEG DATA & ANALYTICS']),
    ('legal_owner', ['© 2026 London Stock Exchange Group plc. All rights reserved.']),
])
def test_publisher_evidence_preserves_the_approved_visual_role_in_posts(tmp_path, publisher, role, labels):
    logo = Reviewer([{'publisher':'LSEG','page':2,'role':role,'identified_as_publisher':True,
                      'readable':True,'no_conflicts':True,'visible_text':labels}])
    def renderer(pdf, output, pages):
        output.mkdir(parents=True, exist_ok=True)
        items = []
        for page in pages:
            name=f'page-{page}.png'; (output/name).write_bytes(b'\x89PNG\r\n\x1a\nlogo')
            items.append({'page_number':page,'image_file':name})
        return items
    claims=Reviewer([selection(figure_ids=[],text_only=True,captions={}),verdict()])
    pipe=intake.Pipeline(tmp_path,claims,publisher,extractor=extractor,figure_catalogue=lambda *a:[],
                         pdf_created=lambda pdf:None,publisher_reviewer=logo,publisher_renderer=renderer,
                         chart_producer=EmptyChartAgent(),chart_reviewer=EmptyChartAgent())
    metadata=work() | {'metadata':work()['metadata'] | {'path_lower':'/joe mccann/current/2026/september/sep 17/other/report.pdf'}}
    posts=pipe.process(metadata,tmp_path/'r.pdf',[])
    assert len(posts)==1
    assert posts[0]['source']['publisher']=='LSEG'
    assert posts[0]['source']['publisherEvidence']=={'page':2,'source_quote':'\n'.join(labels),'kind':role}


@pytest.mark.parametrize("complete", [False, None, "true", 1])
def test_incomplete_labelled_chart_is_held_before_factual_verification(tmp_path, publisher, complete):
    reviewer = Reviewer([selection(), match(f1={
        "complete_figure": complete,
        "visible_text": ["T50 Longs - SPX (Daily)", "Morgan Stanley Prime Brokerage"],
        "shows": "Axes, legend and report footer; plot body is cropped off",
    }), verdict()])
    assert build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", []) == []
    assert [call[0] for call in reviewer.calls] == ["text", "match"]
    assert not publisher.stored
    assert any(entry.get("held") == "FIGURE_MISMATCH" for entry in audit_of(tmp_path))


def test_missing_figure_completeness_is_not_accepted_as_legacy_match():
    result = match()(["f1"])
    result["figures"][0].pop("complete_figure")
    assert intake.figure_match_passed(result, {"f1"}) == {}


@pytest.mark.parametrize("complete", [False, None, "true", 1])
def test_generic_factual_verdict_cannot_publish_incomplete_image(tmp_path, publisher, complete):
    reviewer = Reviewer([selection(), verdict(figures_complete=complete)])
    assert build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", []) == []
    assert [call[0] for call in reviewer.calls] == ["text", "match", "multimodal"]
    assert not publisher.stored
    assert any(entry.get("held") == "VERIFY_FAILED" for entry in audit_of(tmp_path))


def test_figure_verdict_requires_explicit_completeness_but_text_only_does_not():
    factual = verdict()
    factual.pop("figures_complete")
    assert not intake.verdict_passed(factual, has_figures=True)
    assert intake.verdict_passed(factual)


def test_completeness_prompts_reject_footer_strip_without_requiring_table_axes():
    assert "legend/footer strips" in intake.FIGURE_MATCH_INSTRUCTION
    assert "tables require their headers and all rows/columns, not chart axes" in intake.FIGURE_MATCH_INSTRUCTION
    assert "only axes, legends or a report footer fails" in intake.VERIFY_INSTRUCTION


def test_final_publication_requires_completeness_field_not_just_factual_gates(tmp_path, publisher):
    factual = verdict()
    factual.pop("figures_complete")
    reviewer = Reviewer([selection(), factual])
    assert build(tmp_path, reviewer, publisher).process(work(), tmp_path / "r.pdf", []) == []
    assert not publisher.stored
    assert any(entry.get("held") == "VERIFY_FAILED" for entry in audit_of(tmp_path))


def decorative_catalogue(count=2):
    def render(pdf, pages, output, dpi=216):
        output.mkdir(parents=True, exist_ok=True)
        result = []
        for index in range(1, count + 1):
            name = f'f{index}.png'
            (output / name).write_bytes(b'\x89PNG\r\n\x1a\n' + name.encode())
            result.append({'id': f'f{index}', 'page': 2, 'bbox': [.1, .2, .9, .6], 'objects': 1,
                           'kind': 'raster', 'title': 'Catalogue title does not prove a data chart',
                           'source_line': None, 'image_file': name, 'width': 800, 'height': 400})
        return result
    return render


def nonchart_reply(visual_types=('photo', 'prose'), mutate=None):
    def reply(offered):
        result = {'figures': [
            {'id': identity, 'visual_type': visual_types[index % len(visual_types)],
             'is_chart': False, 'supports_finding': False, 'complete_figure': True,
             'visible_text': [], 'shows': f'A decorative {visual_types[index % len(visual_types)]} without data marks'}
            for index, identity in enumerate(offered)
        ]}
        return mutate(result) if mutate else result
    reply.figure_match = True
    return reply


def decorative_pipeline(tmp_path, reviewer, publisher, count=2, **kwargs):
    return intake.Pipeline(tmp_path, reviewer, publisher, extractor=extractor,
                           figure_catalogue=decorative_catalogue(count), pdf_created=lambda pdf: None,
                           chart_producer=EmptyChartAgent(), chart_reviewer=EmptyChartAgent(), **kwargs)


@pytest.mark.parametrize('initially_text_only', [False, True])
def test_independently_proven_photos_and_prose_reach_text_verification(tmp_path, publisher, initially_text_only):
    first = selection(pages=[1, 2], figure_ids=[] if initially_text_only else ['f1'],
                      text_only=initially_text_only, captions={} if initially_text_only else {'f1': 'Proposed figure'})
    responses = [first]
    if initially_text_only:
        responses.append(selection(pages=[1, 2], figure_ids=[], text_only=True, captions={}))
    responses.extend([nonchart_reply(), verdict()])
    reviewer = Reviewer(responses)
    posts = decorative_pipeline(tmp_path, reviewer, publisher).process(work(), tmp_path / 'r.pdf', [])
    assert len(posts) == 1 and posts[0]['images'] == [] and posts[0]['source']['figures'] == []
    assert posts[0]['source']['chartGeneration']['status'] == 'no_comparable_data'
    expected = ['text', 'text', 'match', 'multimodal'] if initially_text_only else ['text', 'match', 'multimodal']
    assert [call[0] for call in reviewer.calls] == expected
    inspected = next(call for call in reviewer.calls if call[0] == 'match')
    assert offered_ids(inspected[1]) == ['f1', 'f2'] and len(inspected[2]) == 2
    verified = reviewer.calls[-1]
    assert verified[2] == () and 'TOKENS NOT MATCHED BY CODE' in verified[1]
    assert not any(path.endswith('.png') for path in publisher.stored)


@pytest.mark.parametrize('visual_type', ['photo', 'prose', 'logo', 'letterhead', 'banner', 'signature'])
@pytest.mark.parametrize('complete', [False, True])
def test_explicit_non_data_visual_types_are_eligible_for_text_only(tmp_path, publisher, visual_type, complete):
    def annotate(result):
        for figure in result['figures']:
            figure['complete_figure'] = complete
        return result
    reviewer = Reviewer([selection(pages=[1, 2]), nonchart_reply((visual_type,), mutate=annotate), verdict()])
    posts = decorative_pipeline(tmp_path, reviewer, publisher).process(work(), tmp_path / 'r.pdf', [])
    assert len(posts) == 1 and posts[0]['images'] == []
    assert reviewer.calls[-1][0] == 'multimodal' and reviewer.calls[-1][2] == ()


def test_text_only_reselect_can_still_attach_a_visually_proven_real_chart(tmp_path, publisher):
    text_only = selection(pages=[1, 2], figure_ids=[], text_only=True, captions={})
    reviewer = Reviewer([text_only, text_only, match(passing={'f2'}), verdict()])
    pipe = intake.Pipeline(tmp_path, reviewer, publisher, extractor=extractor,
                           figure_catalogue=logo_and_chart, pdf_created=lambda pdf: None)
    posts = pipe.process(work(), tmp_path / 'r.pdf', [])
    assert len(posts) == 1 and len(posts[0]['images']) == 1
    assert posts[0]['source']['figures'][0]['caption'] == 'f2 matched caption'
    assert 'chartGeneration' not in posts[0]['source']
    assert len(reviewer.calls[-1][2]) == 1 and str(reviewer.calls[-1][2][0][1]).endswith('f2.png')


@pytest.mark.parametrize('defect', [
    'missing_id', 'missing_id_field', 'list_id', 'dict_id', 'integer_id', 'duplicate_id',
    'duplicate_existing', 'extra_id', 'non_object', 'missing_type', 'nonstring_type', 'chart', 'table',
    'chart_fragment', 'unknown', 'is_chart_true', 'supports_true', 'string_false',
    'is_chart_integer', 'supports_integer', 'supports_missing',
    'missing_completeness', 'nonboolean_completeness', 'missing_labels', 'nonlist_labels',
    'nonstring_label', 'blank_shows', 'missing_shows', 'nonstring_shows', 'nonlist_figures',
])
def test_uncertain_or_incomplete_nonchart_proof_holds(tmp_path, publisher, defect):
    def mutate(result):
        figures = result['figures']
        if defect == 'missing_id': figures.pop()
        elif defect == 'missing_id_field': figures[0].pop('id')
        elif defect == 'list_id': figures[0]['id'] = []
        elif defect == 'dict_id': figures[0]['id'] = {}
        elif defect == 'integer_id': figures[0]['id'] = 1
        elif defect == 'duplicate_id': figures[1]['id'] = figures[0]['id']
        elif defect == 'duplicate_existing': figures.append(dict(figures[0]))
        elif defect == 'extra_id': figures.append(dict(figures[0], id='unoffered'))
        elif defect == 'non_object': figures.append(None)
        elif defect == 'missing_type': figures[0].pop('visual_type')
        elif defect == 'nonstring_type': figures[0]['visual_type'] = ['photo']
        elif defect in ('chart', 'table', 'chart_fragment', 'unknown'): figures[0]['visual_type'] = defect
        elif defect == 'is_chart_true': figures[0]['is_chart'] = True
        elif defect == 'supports_true': figures[0]['supports_finding'] = True
        elif defect == 'string_false': figures[0]['is_chart'] = 'false'
        elif defect == 'is_chart_integer': figures[0]['is_chart'] = 0
        elif defect == 'supports_integer': figures[0]['supports_finding'] = 0
        elif defect == 'supports_missing': figures[0].pop('supports_finding')
        elif defect == 'missing_completeness': figures[0].pop('complete_figure')
        elif defect == 'nonboolean_completeness': figures[0]['complete_figure'] = 0
        elif defect == 'missing_labels': figures[0].pop('visible_text')
        elif defect == 'nonlist_labels': figures[0]['visible_text'] = ''
        elif defect == 'nonstring_label': figures[0]['visible_text'] = [None]
        elif defect == 'blank_shows': figures[0]['shows'] = ' '
        elif defect == 'missing_shows': figures[0].pop('shows')
        elif defect == 'nonstring_shows': figures[0]['shows'] = 123
        elif defect == 'nonlist_figures': result['figures'] = 'no charts'
        return result
    reviewer = Reviewer([selection(pages=[1, 2]), nonchart_reply(mutate=mutate), verdict()])
    posts = decorative_pipeline(tmp_path, reviewer, publisher).process(work(), tmp_path / 'r.pdf', [])
    assert posts == [] and not publisher.stored
    assert [call[0] for call in reviewer.calls] == ['text', 'match']
    assert any(entry.get('held') for entry in audit_of(tmp_path))


@pytest.mark.parametrize('count', [8, 9])
def test_all_cited_assets_must_be_visually_inspected_within_cap(tmp_path, publisher, count):
    reviewer = Reviewer([selection(pages=[1, 2]), nonchart_reply(), verdict()])
    posts = decorative_pipeline(tmp_path, reviewer, publisher, count=count).process(work(), tmp_path / 'r.pdf', [])
    inspected = [call for call in reviewer.calls if call[0] == 'match']
    assert all(len(call[2]) <= 8 for call in inspected)
    if count == 8:
        assert len(posts) == 1 and posts[0]['images'] == []
        assert len(inspected) == 1 and set(offered_ids(inspected[0][1])) == {f'f{i}' for i in range(1, 9)}
    else:
        assert posts == [] and not publisher.stored
        assert not any(call[0] == 'multimodal' for call in reviewer.calls)


@pytest.mark.parametrize('failed_gate', list(intake.GATES))
def test_nonchart_clearance_cannot_bypass_normal_claim_verification(tmp_path, publisher, failed_gate):
    reviewer = Reviewer([selection(pages=[1, 2]), nonchart_reply(), verdict(**{failed_gate: False})])
    posts = decorative_pipeline(tmp_path, reviewer, publisher).process(work(), tmp_path / 'r.pdf', [])
    assert posts == [] and not publisher.stored
    assert [call[0] for call in reviewer.calls] == ['text', 'match', 'multimodal']
    assert reviewer.calls[-1][2] == ()
    assert any(entry.get('held') == 'VERIFY_FAILED' for entry in audit_of(tmp_path))


@pytest.mark.parametrize('initially_text_only', [False, True])
def test_nonchart_visual_inspection_outage_propagates_for_queue_retry(tmp_path, publisher, initially_text_only):
    text_only = selection(pages=[1, 2], figure_ids=[], text_only=True, captions={})
    def unavailable(offered):
        raise model.ModelError('Model ladder exhausted: provider unavailable')
    unavailable.figure_match = True
    responses = [text_only, text_only] if initially_text_only else [selection(pages=[1, 2])]
    reviewer = Reviewer(responses + [unavailable])
    with pytest.raises(model.ModelError, match='provider unavailable'):
        decorative_pipeline(tmp_path, reviewer, publisher).process(work(), tmp_path / 'r.pdf', [])
    assert not publisher.stored


@pytest.mark.parametrize('bad_reselect', [
    {'candidates': []},
    {'candidates': [{'title': 'Missing candidate shape'}]},
    selection(pages=[], figure_ids=[], text_only=True, captions={}),
    selection(pages=[1, 2], figure_ids=[], text_only=False, captions={}),
])
def test_malformed_text_only_reselect_cannot_reuse_old_candidate(tmp_path, publisher, bad_reselect):
    first = selection(pages=[1, 2], figure_ids=[], text_only=True, captions={})
    reviewer = Reviewer([first, bad_reselect, nonchart_reply(), verdict()])
    assert decorative_pipeline(tmp_path, reviewer, publisher).process(work(), tmp_path / 'r.pdf', []) == []
    assert [call[0] for call in reviewer.calls] == ['text', 'text'] and not publisher.stored


def test_nonchart_proof_prompt_requests_explicit_visual_types_and_full_id_coverage():
    prompt = intake.FIGURE_MATCH_INSTRUCTION
    assert 'visual_type' in prompt and 'chart_fragment' in prompt
    for kind in ('photo', 'prose', 'logo', 'letterhead', 'banner', 'signature', 'unknown'):
        assert kind in prompt

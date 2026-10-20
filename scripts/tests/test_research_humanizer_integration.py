"""Humanizer gates final research summaries before the publisher sees them."""
import copy
import json
import pytest
from types import SimpleNamespace

from research import humanizer, intake, summary_editor


PAGE = (
    "## Economics Research ## 16 September 2026 | TIC Data. "
    "Foreign investors bought $45bn of US equities in July, the second consecutive month above $40bn. "
    "Official investors added $12bn of Treasuries."
)


def post(title="Foreign investors bought $45bn of US equities in July", content=None):
    return {
        "id": "research-test", "title": title,
        "content": content or "TIC data show $45bn of net foreign buying of US equities in July, above $40bn for a second month.",
        "source": {"publisher": "Goldman Sachs", "documentDate": "2026-09-16", "pages": [1]},
    }


def clean_review(_prompt):
    return {"findings": [], "qualitative": []}


def test_clean_summary_gets_copy_bound_certificate(tmp_path):
    value = post()
    before = copy.deepcopy(value)
    audit = []
    result = summary_editor.edit_summaries(
        [value], ask=clean_review, text={1: PAGE}, audit=audit, out=tmp_path,
    )

    assert len(result) == 1 and result[0]["title"] == before["title"]
    cert = result[0]["source"]["humanizer"]
    assert cert["score"] >= 90
    assert cert["copy_sha256"] == humanizer.copy_digest(result[0])
    assert "humanizer" not in value["source"], "helper must not mutate its caller's source object"
    assert audit


def test_low_score_rewrite_is_verified_rescored_and_is_the_certified_copy(tmp_path):
    calls = []
    audit = []

    def ask(prompt):
        calls.append(prompt)
        if summary_editor.VERIFY_REWRITE_INSTRUCTION in prompt:
            return {"supported": True, "same_meaning": True,
                    "no_unresolved_conflicts": True, "not_forecast_as_flow": True,
                    "reason": "Both claims match page 1."}
        if prompt.startswith("HUMANIZER REWRITE."):
            return {"title": "Foreign investors bought $45bn of US equities in July",
                    "content": "Foreign investors bought $45bn of US equities in July."}
        if len(calls) == 1:
            return {"findings": [{"field": "content", "start": 2, "end": 9,
                                  "category": "promotional_language"}, {"field": "content", "start": 10, "end": 16,
                                  "category": "vocabulary"}], "qualitative": []}
        return clean_review(prompt)

    result = summary_editor.edit_summaries(
        [post(content="A vibrant robust surge saw foreign investors buy $45bn of US equities in July.")],
        ask=ask, text={1: PAGE}, audit=audit, out=tmp_path,
    )

    assert len(result) == 1
    assert result[0]["content"].startswith("Foreign investors bought $45bn")
    assert result[0]["source"]["humanizer"]["score"] >= 90
    assert result[0]["source"]["humanizer"]["copy_sha256"] == humanizer.copy_digest(result[0])
    assert any(summary_editor.VERIFY_REWRITE_INSTRUCTION in prompt for prompt in calls)
    assert len(audit) >= 1


def test_unverified_rewrite_fails_closed_and_preserves_private_audit(tmp_path):
    audit = []
    calls = 0

    def ask(prompt):
        nonlocal calls
        calls += 1
        if prompt.startswith("HUMANIZER REWRITE."):
            return {"title": "July foreign equity buying reached $90bn",
                    "content": "Foreign investors bought $90bn of US equities in July."}
        if summary_editor.VERIFY_REWRITE_INSTRUCTION in prompt:
            return {"supported": False, "same_meaning": False,
                    "no_unresolved_conflicts": False, "not_forecast_as_flow": True,
                    "reason": "Source says $45bn, not $90bn."}
        return {"findings": [{"field": "content", "start": 0, "end": 10,
                              "category": "promotional_language"}], "qualitative": []}

    result = summary_editor.edit_summaries(
        [post(content="A vibrant robust pivotal report says foreign investors bought $45bn of US equities in July.")],
        ask=ask, text={1: PAGE}, audit=audit, out=tmp_path,
    )

    assert result == []
    assert audit, "failed rewrite must leave an operator-readable private audit"


def test_malformed_or_unavailable_style_review_fails_closed(tmp_path):
    for reply in (None, {"score": 100}, {"findings": "bad", "qualitative": []}):
        audit = []
        result = summary_editor.edit_summaries(
            [post()], ask=lambda _prompt, reply=reply: reply,
            text={1: PAGE}, audit=audit, out=tmp_path,
        )
        assert result == []
        assert audit


def test_intake_publishes_only_final_rewritten_text_and_keeps_audit_private(tmp_path, monkeypatch):
    """The production process must apply the gate after candidate VERIFY."""
    import test_research_intake as fixtures

    class GateReviewer(fixtures.Reviewer):
        def ask_text(self, instruction):
            if instruction.startswith(humanizer.RUBRIC_INSTRUCTION):
                return {"findings": [], "qualitative": []}
            return super().ask_text(instruction)

    reviewer = GateReviewer([fixtures.selection(), fixtures.verdict()])
    publisher = SimpleNamespace(
        store_asset=lambda path: "/api/newsfeed/research/files/" + "a" * 64 + ".pdf",
        stored=[], recent_posts=lambda days=90: [],
    )
    pipeline = intake.Pipeline(
        tmp_path, reviewer, publisher, extractor=fixtures.extractor,
        figure_catalogue=fixtures.catalogue, pdf_created=lambda _pdf: None,
        chart_producer=fixtures.EmptyChartAgent(), chart_reviewer=fixtures.EmptyChartAgent(),
    )
    posts = pipeline.process(fixtures.work(), tmp_path / "source.pdf", [])

    assert len(posts) == 1
    assert "humanizer" in posts[0]["source"]
    assert "humanizer" not in posts[0]["content"]
    review_file = next(tmp_path.glob("evidence/*/review.json"))
    private_review = json.loads(review_file.read_text())
    assert any("humanizer" in json.dumps(item).lower() for item in private_review.get("audit", []))


def test_style_provider_outage_persists_attempt_before_retry(tmp_path):
    audit = []

    def unavailable(_prompt):
        raise TimeoutError("review provider unavailable")

    with pytest.raises(TimeoutError, match="review provider"):
        summary_editor.edit_summaries(
            [post()], ask=unavailable, text={1: PAGE}, audit=audit, out=tmp_path,
        )

    saved = json.loads((tmp_path / "humanizer-research-test.json").read_text())
    assert saved["humanizer"]["attempts"][-1]["status"] == "reviewer_error"
    assert audit[0]["humanizer"]["attempts"][-1]["status"] == "reviewer_error"


def test_always_publish_fallback_also_requires_final_editorial_certificate(tmp_path):
    import test_research_rubner as fixtures

    class GateReviewer(fixtures.Reviewer):
        def ask_text(self, instruction):
            if instruction.startswith(humanizer.RUBRIC_INSTRUCTION):
                self.calls.append(("text", instruction, ()))
                return clean_review(instruction)
            return super().ask_text(instruction)

    reviewer = GateReviewer([
        {"candidates": [], "reason": "no candidate"},
        {"candidates": [], "reason": "still empty"},
        fixtures.verdict(),
    ])
    publisher = SimpleNamespace(
        store_asset=lambda path: "/api/newsfeed/research/files/" + "b" * 64 + ".pdf",
        stored=[], recent_posts=lambda days=90: [],
    )
    posts = fixtures.build_rubner(tmp_path, reviewer, publisher).process(
        fixtures.rubner_work(), tmp_path / "source.pdf", [],
    )

    assert len(posts) == 1
    assert posts[0]["source"]["humanizer"]["score"] >= 90
    humanizer.validate_certificate(posts[0])
    review = json.loads(next(tmp_path.glob("evidence/*/review.json")).read_text())
    assert review.get("always_publish_fallback") is True
    assert any("humanizer" in json.dumps(item).lower() for item in review["audit"])


def test_v1_pipeline_final_post_receives_certificate(tmp_path):
    from tests.test_research_runtime import fixture_pipeline, item
    from tests.test_research_pipeline import gates

    proposal = item()
    proposal["date_evidence"] = {
        "page": 1, "date_text": "4 September 2026",
        "source_quote": "Report date: 4 September 2026", "role": "report",
    }
    verification = gates() | {
        "numeric_checks": [],
        "date_evidence": {
            "page": 1, "date_text": "4 September 2026",
            "source_quote": "Report date: 4 September 2026",
            "role": "report", "role_verified": True,
        },
    }
    pipeline, work = fixture_pipeline(
        tmp_path, [{"candidates": [proposal]}, verification],
    )
    posts = pipeline.process(work, tmp_path / "source.pdf", [])

    assert len(posts) == 1
    humanizer.validate_certificate(posts[0])


@pytest.mark.parametrize("mutation", ["missing", "stale"])
def test_final_publisher_rejects_missing_or_stale_humanizer_proof(tmp_path, monkeypatch, mutation):
    import test_research_publish as fixtures
    from research import publish

    value = fixtures.post.__wrapped__(tmp_path, monkeypatch)
    if mutation == "missing":
        value["source"].pop("humanizer")
    else:
        value["content"] += " Edited after review."

    monkeypatch.setattr(publish, "hrana_transaction", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("invalid certificate reached persistence")))
    with pytest.raises(ValueError, match="Humanizer"):
        publish.publish(value)


def test_same_numbers_cannot_turn_a_forecast_into_a_measured_flow(tmp_path):
    source = "The desk forecasts $45bn of equity purchases in July."
    value = post(content="A vibrant robust pivotal forecast: The desk forecasts $45bn of equity purchases in July.")
    value["title"] = "Desk forecasts $45bn in July"
    audit = []
    calls = []

    def ask(prompt):
        calls.append(prompt)
        if prompt.startswith(humanizer.RUBRIC_INSTRUCTION):
            return clean_review(prompt)
        if prompt.startswith("HUMANIZER REWRITE."):
            return {"title": "Desk records $45bn in July",
                    "content": "The desk recorded $45bn of equity purchases in July."}
        if summary_editor.VERIFY_REWRITE_INSTRUCTION in prompt:
            return {"supported": False, "same_meaning": False,
                    "no_unresolved_conflicts": False, "not_forecast_as_flow": False,
                    "reason": "The source forecasts purchases; it does not report completed purchases."}
        raise AssertionError("unexpected reviewer prompt")

    result = summary_editor.edit_summaries(
        [value], ask=ask, text={1: source}, audit=audit, out=tmp_path,
    )

    assert result == []
    assert any(summary_editor.VERIFY_REWRITE_INSTRUCTION in prompt for prompt in calls)
    assert audit[0]["humanizer"]["factual_checks"][-1]["passed"] is False
    assert audit[0]["held"] == "HUMANIZER_FAILED"


def test_optional_chart_timeout_cannot_starve_the_copy_gate(tmp_path):
    import test_research_intake as fixtures
    from research.pipeline import DocumentDeadlineExceeded

    now = [0.0]
    events = []

    class GateReviewer(fixtures.Reviewer):
        def ask_text(self, instruction):
            if instruction.startswith(humanizer.RUBRIC_INSTRUCTION):
                events.append("humanizer")
                return clean_review(instruction)
            return super().ask_text(instruction)

    class LateChartAgent:
        env = {}

        def ask_text(self, _instruction):
            events.append("chart")
            now[0] = 300.0
            raise DocumentDeadlineExceeded("chart review budget exhausted")

        def ask(self, _instruction, images=()):
            raise AssertionError("empty chart plan must not request image review")

    reviewer = GateReviewer([
        fixtures.selection(figure_ids=[], text_only=True, captions={}, pages=[1]),
        fixtures.verdict(),
    ])
    publisher = SimpleNamespace(
        store_asset=lambda path: "/api/newsfeed/research/files/" + "c" * 64 + ".pdf",
        stored=[], recent_posts=lambda days=90: [],
    )
    pipeline = intake.Pipeline(
        tmp_path, reviewer, publisher, extractor=fixtures.extractor,
        figure_catalogue=fixtures.catalogue, pdf_created=lambda _pdf: None,
        chart_producer=LateChartAgent(), chart_reviewer=fixtures.EmptyChartAgent(),
        clock=lambda: now[0], document_budget_secs=300,
    )
    posts = pipeline.process(fixtures.work(), tmp_path / "source.pdf", [])

    assert len(posts) == 1
    humanizer.validate_certificate(posts[0])
    chart_event = next(
        entry for entry in json.loads(next(tmp_path.glob("evidence/*/review.json")).read_text())["audit"]
        if "chart_generation" in entry
    )
    assert chart_event["chart_generation"]["status"] == "unavailable"
    assert events == ["humanizer", "chart"]
    assert now[0] == 300.0


def test_rejected_summary_remains_visible_in_held_review(tmp_path):
    from research.publish import outcome_row

    audit = []
    summary_editor.edit_summaries([post()], ask=lambda _: {}, text={1: PAGE}, audit=audit, out=tmp_path)
    row = outcome_row({'key': 'work', 'metadata': {}}, {'audit': audit, 'posts': []})
    drafts = json.loads(row['drafts_json'])
    assert row['outcome'] == 'held'
    assert drafts[0]['title'] == post()['title']
    assert drafts[0]['content'] == post()['content']
    assert 'writing review' in drafts[0]['detail']


@pytest.mark.parametrize("context,passed", [
    ({"date": "2026-09-16", "date_page": 2}, True),
    (None, False),
    ({"date": "2026-09-15", "date_page": 2}, False),
])
def test_rewrite_preserves_trusted_identity_date_outside_cited_excerpt(tmp_path, context, passed):
    value = post(title="Report dated 16 September 2026",
                 content="Robust innovative seamless data show foreign investors bought $45bn in July.")
    audit = []

    def ask(prompt):
        if prompt.startswith(humanizer.REWRITE_INSTRUCTION):
            return {"title": value["title"], "content": "Foreign investors bought $45bn in July."}
        if prompt.startswith(summary_editor.VERIFY_REWRITE_INSTRUCTION):
            return {"supported": True, "same_meaning": True, "no_unresolved_conflicts": True,
                    "not_forecast_as_flow": True, "reason": "Exact measurements and trusted report date retained."}
        return clean_review(prompt)

    result = summary_editor.edit_summaries([value], ask=ask, text={1: "Foreign investors bought $45bn in July."},
                                           audit=audit, out=tmp_path, source_context=context)
    assert bool(result) is passed
    assert audit[0]["humanizer"]["factual_checks"][-1]["passed"] is passed

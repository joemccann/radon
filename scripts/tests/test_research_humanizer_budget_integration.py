"""Resource bounds must not hide findings or produce partial Humanizer scores."""
import json

from research import humanizer, summary_editor


def make_post(content, title="June flow summary"):
    return {"id": "research-budget", "title": title, "content": content,
            "source": {"pages": [1], "publisher": "Example Desk"}}


def test_occurrence_amplification_over_budget_is_held_with_private_audit(tmp_path):
    content = "Experts argue " * 4097
    audit = []

    result = summary_editor.edit_summaries(
        [make_post(content)],
        ask=lambda _prompt: {"findings": [{"field": "content", "text": "Experts argue",
                                             "category": "vague_attribution"}],
                             "qualitative": []},
        text={1: "Source report, June 2026."}, audit=audit, out=tmp_path,
    )

    assert result == []
    attempt = audit[0]["humanizer"]["attempts"][-1]
    assert attempt["status"] == "finding_budget_exceeded"
    assert attempt["score"] is None
    assert attempt["findings"] == [] and attempt["qualitative"] == []
    assert audit[0]["held"] == "HUMANIZER_FAILED"
    assert audit[0]["candidate"]["content"] == content
    saved = json.loads((tmp_path / "humanizer-research-budget.json").read_text())
    assert saved["humanizer"]["attempts"][-1]["status"] == "finding_budget_exceeded"


def test_duplicate_descriptors_deduplicate_before_span_expansion():
    post = make_post("Experts argue demand rose.")
    duplicate = {"field": "content", "text": "Experts argue",
                 "category": "vague_attribution"}
    reply = {"findings": [duplicate.copy() for _ in range(4000)], "qualitative": []}
    audits = []

    result = humanizer.review_post(
        post, ask=lambda _prompt: reply, verify=lambda _revised: True,
        record=audits.append,
    )

    assert result is not None
    assert audits[-1]["score"] == 92
    assert len(audits[-1]["findings"]) == 1
    humanizer.validate_certificate(result)


def test_overlapping_categories_and_separate_fields_keep_separate_deductions():
    phrase = "stands as a pivotal moment"
    title = f"The report {phrase}."
    content = f"It {phrase} for positioning."
    post = make_post(content, title)
    reply = {"findings": [
        {"field": "title", "text": phrase, "category": "significance_inflation"},
        {"field": "title", "text": "pivotal moment", "category": "significance_inflation"},
        {"field": "title", "text": "pivotal moment", "category": "promotional_language"},
        {"field": "content", "text": phrase, "category": "significance_inflation"},
        {"field": "content", "text": "pivotal moment", "category": "promotional_language"},
    ], "qualitative": []}
    audits = []

    result = humanizer.review_post(
        post, ask=lambda _prompt: reply, verify=lambda _revised: True,
        record=audits.append, max_rewrites=0,
    )

    assert result is None
    findings = audits[-1]["findings"]
    assert audits[-1]["score"] == 54
    assert sum(item["category"] == "significance_inflation" for item in findings) == 2
    assert sum(item["category"] == "promotional_language" for item in findings) == 2
    assert sum(item["category"] == "vocabulary" for item in findings) == 2

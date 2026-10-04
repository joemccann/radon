"""Citadel Rubner GMI always-publish: triage, novelty, fallback, TTL, idempotence."""
import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from research import intake, novelty, publish
from research.pipeline import EvidenceError
from tests.test_research_intake import (
    Reviewer, build, catalogue, extractor, selection, verdict, work,
)
from tests.test_research_force_include import RUBNER_PAGE


@pytest.fixture
def publisher():
    stored = []
    return SimpleNamespace(store_asset=lambda p: stored.append(str(p)) or "/api/newsfeed/research/files/" + "b" * 64 + "." + str(p).rsplit(".", 1)[-1],
                           stored=stored, recent_posts=lambda days=90: [])


def rubner_work(name="Citadel - The Q4 Reload October 1 Oct 2026.pdf"):
    return work(name, folder="citadel", folder_date="2026-10-01")


def rubner_extractor(pdf, out):
    out.mkdir(parents=True, exist_ok=True)
    (out / "page-0001.md").write_text(RUBNER_PAGE)
    (out / "page-0002.md").write_text("Chart 1. CTA positioning. Source: Citadel.")
    return {"page_count": 2, "source_sha256": "c" * 64, "source_path": str(pdf),
            "pages": [{"page_number": 1, "markdown_file": "page-0001.md"},
                      {"page_number": 2, "markdown_file": "page-0002.md"}]}


def rubner_catalogue(pdf, pages, output_dir, dpi=216):
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "f1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"crop")
    return [{"id": "f1", "page": 2, "bbox": [0.1, 0.2, 0.9, 0.6], "objects": 8,
             "title": "CTA positioning", "source_line": "Source: Citadel",
             "image_file": "f1.png", "width": 800, "height": 400}]


def build_rubner(tmp_path, reviewer, publisher):
    return intake.Pipeline(tmp_path, reviewer, publisher, extractor=rubner_extractor,
                           figure_catalogue=rubner_catalogue, pdf_created=lambda pdf: None)


def test_rubner_overrides_triage_drops(tmp_path, publisher, monkeypatch):
    monkeypatch.setattr(intake.learn, "load_rules", lambda root: {
        "series_deny": {"citadel - the q4 reload october"},
        "doc_type_drop": {"research"}, "publisher_deny": {"Citadel"}})
    reviewer = Reviewer([selection(figure_ids=[], text_only=True, captions={}), verdict()])
    posts = build_rubner(tmp_path, reviewer, publisher).process(rubner_work(), tmp_path / "r.pdf", [])
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert review["force_include"] is True and review["always_publish"] is True
    assert review["triage"]["overridden"] is True
    assert review["outcome"] == "reviewed" and len(posts) == 1


def test_rubner_skips_novelty_against_other_publishers(tmp_path, publisher):
    reviewer = Reviewer([selection(), verdict(),
                         selection(figure_ids=[], text_only=True, captions={}), verdict()])
    pipe = build(tmp_path, reviewer, publisher)
    assert len(pipe.process(work(), tmp_path / "r.pdf", [])) == 1
    rubner_pipe = build_rubner(tmp_path, reviewer, publisher)
    # Same fingerprint file as the Goldman publish (copied below).
    index = json.loads((tmp_path / "fingerprints.json").read_text())
    (tmp_path / "fingerprints.json").write_text(json.dumps(index))
    # Force the Rubner doc to hash as a near-duplicate of the published GS note.
    gs_fp = novelty.fingerprint(
        (tmp_path / "evidence" / ("k" * 64) / "page-0001.md").read_text()
        + "\n" + (tmp_path / "evidence" / ("k" * 64) / "page-0002.md").read_text())
    stored = json.loads((tmp_path / "fingerprints.json").read_text())
    stored.setdefault("publishers", {})
    stored["publishers"][("k" * 64)] = "Goldman Sachs"
    (tmp_path / "fingerprints.json").write_text(json.dumps(stored))
    # Rewrite Rubner extract to the GS page text so simhash matches, but keep Rubner identity via filename.
    def dup_extract(pdf, out):
        out.mkdir(parents=True, exist_ok=True)
        p1 = (tmp_path / "evidence" / ("k" * 64) / "page-0001.md").read_text()
        p2 = (tmp_path / "evidence" / ("k" * 64) / "page-0002.md").read_text()
        (out / "page-0001.md").write_text(p1)
        (out / "page-0002.md").write_text(p2)
        return {"page_count": 2, "source_sha256": "d" * 64, "source_path": str(pdf),
                "pages": [{"page_number": 1, "markdown_file": "page-0001.md"},
                          {"page_number": 2, "markdown_file": "page-0002.md"}]}
    rubner = dict(rubner_work("CS Rubner October the Q4 Reload.pdf"), key="r" * 64)
    other = intake.Pipeline(tmp_path, reviewer, publisher, extractor=dup_extract,
                            figure_catalogue=catalogue, pdf_created=lambda pdf: None)
    posts = other.process(rubner, tmp_path / "r.pdf", [])
    review = json.loads((tmp_path / "evidence" / ("r" * 64) / "review.json").read_text())
    assert review["novelty"]["duplicate_of"] == "k" * 64
    assert review["outcome"] != "dropped" and review.get("reason_code") != "DUPLICATE_OF_PUBLISHED"
    assert len(posts) == 1
    assert gs_fp == review["novelty"]["fingerprint"]


def test_rubner_empty_select_reselects_then_fallback(tmp_path, publisher):
    reviewer = Reviewer([{"candidates": [], "reason": "first"}, {"candidates": [], "reason": "second"}, verdict()])
    posts = build_rubner(tmp_path, reviewer, publisher).process(rubner_work(), tmp_path / "r.pdf", [])
    assert [c[0] for c in reviewer.calls] == ["text", "text", "multimodal"]
    assert reviewer.calls[-1][2] == ()
    assert '"claim_key": "summary"' in reviewer.calls[-1][1]
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert any(a.get("force_include_reselect") for a in review["audit"])
    assert any(a.get("reason_code") == "ALWAYS_PUBLISH_FALLBACK" or a.get("always_publish_fallback")
               for a in review["audit"]) or review.get("always_publish_fallback") is True
    assert len(posts) == 1
    assert posts[0]["id"] == "research-" + hashlib.sha256(
        (rubner_work()["metadata"]["id"] + "\0summary").encode()).hexdigest()
    assert "Scott Rubner" in posts[0]["content"] and "Citadel Securities" in posts[0]["content"]
    assert posts[0]["images"] == []
    assert posts[0]["source"]["dateSource"]


def test_rubner_verify_fail_all_falls_back_without_unverified_numbers(tmp_path, publisher):
    failed = verdict(supported=False, reason="CTA z-score not on the page")
    reviewer = Reviewer([selection(title="CTA +2.35z to -0.80z",
                                   content="US equity CTA positioning moved from +2.35z to -0.80z.",
                                   claim_key="cta-z", figure_ids=["f1"], text_only=False,
                                   captions={"f1": "CTA +2.35z to -0.80z"}), failed, verdict()])
    posts = build_rubner(tmp_path, reviewer, publisher).process(rubner_work(), tmp_path / "r.pdf", [])
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert any(a.get("held") == "VERIFY_FAILED" for a in review["audit"])
    assert len(posts) == 1
    blob = posts[0]["title"] + posts[0]["content"]
    assert "2.35" not in blob and "0.80" not in blob
    assert posts[0]["images"] == []
    assert review.get("always_publish_fallback") is True


def test_rubner_partial_verify_publishes_only_passed_no_fallback(tmp_path, publisher):
    ok = selection(claim_key="constructive", figure_ids=[], text_only=True, captions={},
                   pages=[1],
                   title="Rubner is turning constructive on US equities into Q4",
                   content="We are turning more constructive on US equities into Q4.")
    bad = selection(claim_key="cta-z", title="CTA +2.35z",
                    content="CTA positioning +2.35z to -0.80z.",
                    pages=[2], figure_ids=["f1"], captions={"f1": "CTA +2.35z"})
    reviewer = Reviewer([
        {"candidates": [ok["candidates"][0], bad["candidates"][0]], "reason": "two"},
        verdict(),
        verdict(supported=False, reason="z-score missing"),
    ])
    posts = build_rubner(tmp_path, reviewer, publisher).process(rubner_work(), tmp_path / "r.pdf", [])
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert len(posts) == 1
    assert posts[0]["id"] == "research-" + hashlib.sha256(
        (rubner_work()["metadata"]["id"] + "\0constructive").encode()).hexdigest()
    assert review.get("always_publish_fallback") is not True
    assert any(a.get("held") == "VERIFY_FAILED" for a in review["audit"])


def test_rubner_rerun_is_idempotent_same_post_ids(tmp_path, publisher):
    reviewer = Reviewer([
        {"candidates": [], "reason": "first"}, {"candidates": [], "reason": "second"}, verdict(),
        {"candidates": [], "reason": "first"}, {"candidates": [], "reason": "second"}, verdict(),
    ])
    pipe = build_rubner(tmp_path, reviewer, publisher)
    first = pipe.process(rubner_work(), tmp_path / "r.pdf", [])
    second = pipe.process(rubner_work(), tmp_path / "r.pdf", [])
    assert [p["id"] for p in first] == [p["id"] for p in second]
    assert first[0]["id"].startswith("research-")


def test_expire_skips_always_publish_held_rows(monkeypatch):
    calls = []

    def fake(sql, args=(), **kw):
        calls.append((sql, args))
        if "SELECT" in sql:
            return [("rubner-key", "Citadel", "the q4 reload",
                     "Citadel - The Q4 Reload October 1 Oct 2026.pdf",
                     json.dumps({"alwaysPublish": True, "excerpt": RUBNER_PAGE}))]
        return []

    monkeypatch.setattr(publish, "hrana_execute", fake)
    published = []
    monkeypatch.setattr(publish, "publish", lambda post: published.append(post))
    count = publish.expire_stale_held(now=datetime(2026, 10, 3, 15, 0, tzinfo=timezone.utc))
    assert count == 0 and published == []
    assert any("SELECT" in sql for sql, _ in calls)
    assert not any("UPDATE" in sql and "HELD_EXPIRED" in sql for sql, _ in calls)


def test_expire_still_drops_ordinary_held_rows(monkeypatch):
    calls = []

    def fake(sql, args=(), **kw):
        calls.append((sql, args))
        if "SELECT" in sql:
            return [("old-key", "Goldman Sachs", "tic data", "tic data.pdf", "{}")]
        return [("old-key",)]

    monkeypatch.setattr(publish, "hrana_execute", fake)
    count = publish.expire_stale_held(now=datetime(2026, 10, 3, 15, 0, tzinfo=timezone.utc))
    assert count == 1
    assert any("HELD_EXPIRED" in sql for sql, _ in calls)


def test_outcome_row_records_always_publish_flag_and_fallback_code():
    row = publish.outcome_row(
        work("Citadel - The Q4 Reload October 1 Oct 2026.pdf", folder="citadel"),
        {"pipeline": "v2", "outcome": "reviewed", "always_publish": True,
         "always_publish_fallback": True,
         "identity": {"publisher": "Citadel", "series": "the q4 reload", "doc_type": "research",
                      "date": "2026-10-01", "date_source": "text"},
         "audit": [{"held": "VERIFY_FAILED", "claim_key": "cta-z",
                    "verification": {"reason": "ungrounded z"}},
                   {"always_publish_fallback": True, "reason_code": "ALWAYS_PUBLISH_FALLBACK"}],
         "selection": {"candidates": [], "reason": "empty"},
         "posts": [{"id": "research-summary"}],
         "document": {"page_count": 17, "excerpt": RUBNER_PAGE}},
    )
    assert json.loads(row["context_json"])["alwaysPublish"] is True
    assert "ALWAYS_PUBLISH_FALLBACK" in json.loads(row["reason_codes"])
    assert row["outcome"] == "published"


def test_classify_error_keeps_evidence_message():
    from research.model import classify_error
    from research.state import _persist_error
    err = EvidenceError("PDF extraction failed; original retained for review")
    assert "PDF extraction failed" in classify_error(err)
    assert "PDF extraction failed" in _persist_error(err)


@pytest.mark.parametrize("gate", intake.GATES)
def test_rel306_fallback_requires_independent_verification(tmp_path, publisher, gate):
    """R-725 / REL-306: an always-publish desk cannot bypass VERIFY."""
    reviewer = Reviewer([
        {"candidates": [], "reason": "empty"},
        {"candidates": [], "reason": "still empty"},
        verdict(**{gate: False, "reason": "fallback has unresolved source meaning"}),
    ])
    posts = build_rubner(tmp_path, reviewer, publisher).process(rubner_work(), tmp_path / "r.pdf", [])
    assert posts == []
    assert [c[0] for c in reviewer.calls] == ["text", "text", "multimodal"]
    review = json.loads((tmp_path / "evidence" / ("k" * 64) / "review.json").read_text())
    assert any(a.get("claim_key") == "summary" and a.get("held") == "VERIFY_FAILED"
               for a in review["audit"])
    assert review["posts"] == []
    assert not (tmp_path / "fingerprints.json").exists()
    assert publish.outcome_row(rubner_work(), review)["outcome"] == "held"


def test_rel306_fallback_verification_outage_preserves_retry(tmp_path, publisher):
    from research.model import ModelError
    reviewer = Reviewer([{"candidates": [], "reason": "empty"}] * 2)
    def unavailable(prompt, images=()):
        raise ModelError("verification unavailable")
    reviewer.ask = unavailable
    with pytest.raises(ModelError, match="verification unavailable"):
        build_rubner(tmp_path, reviewer, publisher).process(rubner_work(), tmp_path / "r.pdf", [])
    assert publisher.stored == []
    assert not (tmp_path / "fingerprints.json").exists()


def test_rel306_fallback_honors_remaining_document_budget(tmp_path, publisher):
    from research.pipeline import DocumentDeadlineExceeded, REVIEWER_CALL_TIMEOUT_SECS
    reviewer = Reviewer([{"candidates": [], "reason": "empty"}] * 2)
    now = [0.0]
    original = reviewer.ask_text
    def select(prompt):
        response = original(prompt)
        if len(reviewer.calls) == 2:
            now[0] = 3 * REVIEWER_CALL_TIMEOUT_SECS - REVIEWER_CALL_TIMEOUT_SECS + 1
        return response
    reviewer.ask_text = select
    pipe = build_rubner(tmp_path, reviewer, publisher)
    pipe.clock = lambda: now[0]
    pipe.document_budget_secs = 3 * REVIEWER_CALL_TIMEOUT_SECS
    with pytest.raises(DocumentDeadlineExceeded, match="before.*fallback"):
        pipe.process(rubner_work(), tmp_path / "r.pdf", [])
    assert publisher.stored == []
    assert not (tmp_path / "fingerprints.json").exists()

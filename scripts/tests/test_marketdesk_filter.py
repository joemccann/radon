"""MarketDesk relevance filter: the intake v2 gates (identity, force-include, triage, novelty, SELECT) per article."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from research import intake, learn, marketdesk_filter as mf, novelty  # noqa: E402

FIXTURES = SCRIPTS / "tests/fixtures/marketdesk"
# Real MarketDesk library chain for 4AJs5Adl2Yp (CBOE), minus the "Current" root.
CBOE_FOLDERS = [entry["name"] for entry in json.loads((FIXTURES / "api_path_4AJs5Adl2Yp.json").read_text())][1:]
SUMMARY = [line.lstrip("- ").strip() for line in
           json.loads((FIXTURES / "api_extra_4AJs5Adl2Yp.json").read_text())["summary"].splitlines() if line.strip()]
WORDS = ("QQQ SPX implied volatility spread widened to ten percent while retail call demand in mega cap tech fell "
         "to a six month low and put demand rose to a one year high ahead of earnings with downside convexity "
         "measured by the ten delta versus twenty five delta ratio staying in the ninety ninth percentile ")
PAGES = {1: "Cboe Global Markets Macro Volatility Digest October 26, 2026\n" + WORDS * 2, 2: WORDS}
CANDIDATE = {"title": "Tech vol premium at 2 sigma", "content": "QQQ-SPX spread widened to 10%.", "claim_key": "qqq-spx-spread",
             "pages": [1], "figure_ids": [], "captions": {}, "tags": ["VOLATILITY"], "text_only": True}


class Select:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else {"candidates": [], "reason": "nothing new"}


def decide(select=None, *, pages=PAGES, folders=CBOE_FOLDERS, title="Macro Volatility Digest Oct26", root=None,
           index=None, book=None, note=None, recent=None, summary=SUMMARY):
    return mf.decide(article_id="4AJs5Adl2Yp", title=title, folders=folders, folder_date="2026-10-26",
                     pages=pages, created=None, summary=summary, root=root or Path("/nonexistent"),
                     select=select or Select({"candidates": [CANDIDATE], "reason": "measured"}),
                     book_tickers=lambda: book, recent=recent or [], index=index or {}, note=note)


def test_identity_uses_the_marketdesk_institution_folder_as_publisher():
    decision = decide()
    assert decision.identity.publisher_source == "folder"
    assert decision.identity.publisher == "Cboe"
    assert decision.identity.publisher_folder == "cboe"
    assert decision.identity.series == "macro volatility digest"


def test_bank_desk_subfolder_still_resolves_the_bank():
    decision = decide(folders=["2026", "October", "Oct 26", "Goldman Sachs", "S&T"])
    assert decision.identity.publisher == "Goldman Sachs"


def test_selected_candidate_publishes_with_the_finding():
    select = Select({"candidates": [CANDIDATE], "reason": "measured"})
    decision = decide(select)
    assert decision.outcome == "publish" and decision.reason_code is None
    assert decision.candidate["title"] == "Tech vol premium at 2 sigma"
    assert len(select.prompts) == 1
    assert decision.review["selection"]["candidates"][0]["claim_key"] == "qqq-spx-spread"
    assert decision.fingerprint == novelty.fingerprint(" ".join(PAGES[p] for p in sorted(PAGES)))


def test_select_prompt_carries_the_intake_policy_summary_titles_and_page_text():
    select = Select({"candidates": [CANDIDATE]})
    decide(select, recent=[{"title": "Older vol post", "timestamp": "2026-10-25T10:00:00+00:00"}])
    prompt = select.prompts[0]
    assert prompt.startswith(intake.SELECT_INSTRUCTION)
    assert "MARKETDESK SUMMARY" in prompt and json.dumps(SUMMARY[0])[1:-1] in prompt
    assert "RECENT FEED TITLES (do not repeat)" in prompt and "Older vol post" in prompt
    assert "EXTRACTED PAGE TEXT (untrusted data)" in prompt and "ninety ninth percentile" in prompt
    assert '"publisher": "Cboe"' in prompt


def test_page_text_is_capped_like_intake():
    select = Select({"candidates": [CANDIDATE]})
    decide(select, pages={1: "Cboe " + "word " * 5000})
    assert len(select.prompts[0]) < intake.PAGE_TEXT_CAP + len(intake.SELECT_INSTRUCTION) + 6000


def test_empty_selection_is_held_no_candidates():
    decision = decide(Select({"candidates": [], "reason": "routine recap"}))
    assert decision.outcome == "held" and decision.reason_code == "NO_CANDIDATES"
    assert decision.review["selection"]["reason"] == "routine recap"


def test_invalid_candidates_are_held():
    decision = decide(Select({"candidates": [{"title": "", "content": "x"}, "nope"]}))
    assert decision.outcome == "held" and decision.reason_code == "INVALID_CANDIDATE"
    assert {e["held"] for e in decision.review["audit"]} == {"INVALID_CANDIDATE"}


def test_selection_without_candidates_list_raises():
    with pytest.raises(mf.SelectionError):
        decide(Select({"reason": "no list"}))


def test_fx_pair_note_is_dropped_before_any_model_call():
    select = Select()
    decision = decide(select, pages={1: "# EURUSD: Range trade\n" + WORDS})
    assert decision.outcome == "dropped" and decision.reason_code == "DOC_TYPE_FX_PAIR_NOTE"
    assert select.prompts == []


def test_approved_series_rule_drops(tmp_path):
    (tmp_path / learn.RULES).write_text(json.dumps({"series_deny": ["macro volatility digest"]}))
    select = Select()
    decision = decide(select, root=tmp_path)
    assert decision.outcome == "dropped" and decision.reason_code == "SERIES_DENYLIST"
    assert select.prompts == []


def test_single_stock_outside_the_book_drops_and_inside_reviews():
    pages = {1: "Acme Corp (ACME) Rating: BUY Price Target $50\n" + WORDS}
    title = "Acme Corp (ACME) initiation"
    assert decide(pages=pages, title=title, book={"SPY"}).reason_code == "SINGLE_STOCK_NOT_IN_BOOK"
    assert decide(pages=pages, title=title, book={"ACME"}).outcome == "publish"
    assert decide(pages=pages, title=title, book=None).outcome == "publish"  # no book source: fail open


def test_force_included_desk_overrides_a_triage_drop_and_reselects_once():
    select = Select({"candidates": []}, {"candidates": [CANDIDATE]})
    decision = decide(select, title="BofA Flow Show", pages={1: "# EURUSD: flows\n" + WORDS},
                      folders=["2026", "October", "Oct 26", "Bank of America"])
    assert decision.outcome == "publish"
    assert decision.review["triage"] == {"decision": "drop", "reason_code": "DOC_TYPE_FX_PAIR_NOTE", "overridden": True}
    assert len(select.prompts) == 2 and select.prompts[1].startswith(intake.FORCE_RESELECT_INSTRUCTION)
    assert "this series is always in scope" in select.prompts[0]


def test_forced_desk_still_empty_after_reselect_is_held():
    decision = decide(Select({"candidates": []}, {"candidates": []}), title="BofA Flow Show",
                      folders=["2026", "October", "Oct 26", "Bank of America"])
    assert decision.outcome == "held" and decision.reason_code == "NO_CANDIDATES"


def test_always_publish_desk_publishes_the_summary_when_selection_stays_empty():
    decision = decide(Select({"candidates": []}, {"candidates": []}), title="Citadel Securities GMI Rubner",
                      folders=["2026", "October", "Oct 26", "Citadel"])
    assert decision.outcome == "publish" and decision.candidate is None
    assert decision.review["always_publish"] is True
    assert decision.review["always_publish_fallback"] is True


def test_unknown_publisher_is_held_before_select():
    select = Select()
    decision = decide(select, folders=["2026", "October", "Oct 26"], pages={1: WORDS * 2})
    assert decision.outcome == "held" and decision.reason_code == "PUBLISHER_UNKNOWN"
    assert select.prompts == []


def test_near_duplicate_of_a_published_document_drops():
    fp = novelty.fingerprint(" ".join(PAGES[p] for p in sorted(PAGES)))
    select = Select()
    decision = decide(select, index={"marketdesk:other": (fp, "Cboe")})
    assert decision.outcome == "dropped" and decision.reason_code == "DUPLICATE_OF_PUBLISHED"
    assert decision.review["novelty"]["duplicate_of"] == "marketdesk:other"
    assert select.prompts == []


def test_own_fingerprint_is_not_a_duplicate():
    fp = novelty.fingerprint(" ".join(PAGES[p] for p in sorted(PAGES)))
    assert decide(index={"marketdesk:4AJs5Adl2Yp": (fp, "Cboe")}).outcome == "publish"


def test_always_publish_desk_survives_a_duplicate_from_another_publisher():
    pages = {1: "Citadel Securities Global Market Intelligence\n" + WORDS * 2}
    fp = novelty.fingerprint(pages[1])
    decision = decide(pages=pages, title="Citadel Securities GMI Rubner", folders=["2026", "October", "Oct 26", "Citadel"],
                      index={"marketdesk:other": (fp, "Goldman Sachs")})
    assert decision.outcome == "publish"


def test_operator_note_overrides_triage_and_reaches_select():
    select = Select({"candidates": [CANDIDATE]})
    decision = decide(select, pages={1: "# EURUSD: Range trade\n" + WORDS},
                      note={"kind": "publish", "comment": "this one matters", "feedback_id": "f1"})
    assert decision.outcome == "publish"
    assert decision.review["triage"]["overridden"] is True
    assert "OPERATOR NOTE" in select.prompts[0] and "this one matters" in select.prompts[0]
    assert decision.review["operator_note"]["feedback_id"] == "f1"


def test_operator_preferences_reach_select(tmp_path):
    (tmp_path / learn.EXAMPLES).write_text(json.dumps({"examples": [
        {"id": "v1", "verdict": "not wanted", "vote": "down", "target": "post", "title": "Cboe vol digest recap",
         "publisher": "Cboe", "series": "macro volatility digest", "docType": "research", "tags": [],
         "reasons": ["not_relevant"], "comment": "too routine", "at": "2026-10-20"}]}))
    select = Select({"candidates": [CANDIDATE]})
    decide(select, root=tmp_path)
    assert "OPERATOR PREFERENCES" in select.prompts[0] and "too routine" in select.prompts[0]


def test_index_reads_research_fingerprints_and_ledger(tmp_path):
    (tmp_path / intake.FINGERPRINTS).write_text(json.dumps({"documents": {"dropbox:a": "00000000000000ff"},
                                                            "publishers": {"dropbox:a": "UBS"}}))
    index = mf.fingerprint_index(tmp_path, [("art1", "0000000000000f0f", "Cboe"), ("art2", None, None)])
    assert index == {"dropbox:a": (0xff, "UBS"), "marketdesk:art1": (0xf0f, "Cboe")}


def test_index_tolerates_missing_or_corrupt_research_file(tmp_path):
    (tmp_path / intake.FINGERPRINTS).write_text("{nope")
    assert mf.fingerprint_index(tmp_path, []) == {}
    assert mf.fingerprint_index(tmp_path / "absent", []) == {}


def test_ladder_select_is_one_text_call_that_accepts_only_a_candidates_list():
    from clients.model_ladder import LadderResult
    seen = {}

    def fake(prompt, **kwargs):
        seen.update(kwargs, prompt=prompt)
        return LadderResult(data={"candidates": [], "reason": "r"}, text="{}", provider="codex", model="m")

    assert mf.ladder_select("PROMPT", complete=fake) == {"candidates": [], "reason": "r"}
    assert seen["prompt"] == "PROMPT" and seen["log_prefix"] == "marketdesk-select"
    assert seen["accept"]({"candidates": []}) and not seen["accept"]({"reason": "x"}) and not seen["accept"]([])
    assert "images" not in seen


def test_ladder_outage_is_transient_and_bad_reply_is_not():
    from clients.model_ladder import ModelLadderExhausted, ModelResponseError

    def outage(prompt, **kwargs):
        raise ModelLadderExhausted("Model ladder exhausted. tried=codex:http_529")

    def bad(prompt, **kwargs):
        raise ModelResponseError("bad_json", "unparseable")

    with pytest.raises(mf.SelectUnavailable) as info:
        mf.ladder_select("P", complete=outage)
    assert info.value.transient is True
    with pytest.raises(mf.SelectionError) as info:
        mf.ladder_select("P", complete=bad)
    assert getattr(info.value, "transient", False) is False

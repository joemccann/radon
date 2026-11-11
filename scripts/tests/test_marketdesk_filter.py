"""MarketDesk all-suitable admission preserves source identity and duplicate safety."""
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


def decide(*, pages=PAGES, folders=CBOE_FOLDERS, title="Macro Volatility Digest Oct26", root=None,
           index=None, book=None, note=None, summary=SUMMARY):
    return mf.decide(article_id="4AJs5Adl2Yp", title=title, folders=folders, folder_date="2026-10-26",
                     pages=pages, created=None, summary=summary, root=root or Path("/nonexistent"),
                     book_tickers=lambda: book, index=index or {}, note=note)


def test_identity_uses_the_marketdesk_institution_folder_as_publisher():
    decision = decide()
    assert decision.identity.publisher_source == "folder"
    assert decision.identity.publisher == "Cboe"
    assert decision.identity.publisher_folder == "cboe"
    assert decision.identity.series == "macro volatility digest"


def test_bank_desk_subfolder_still_resolves_the_bank():
    decision = decide(folders=["2026", "October", "Oct 26", "Goldman Sachs", "S&T"])
    assert decision.identity.publisher == "Goldman Sachs"


def test_suitable_report_enters_copy_without_a_selector_finding():
    decision = decide()
    assert decision.outcome == 'publish' and decision.reason_code is None
    assert decision.candidate is None
    assert decision.review['admission_policy'] == 'current_book_issuer_research'
    assert decision.fingerprint == novelty.fingerprint(' '.join(PAGES[p] for p in sorted(PAGES)))

def test_approved_dropbox_relevance_rules_do_not_exclude_marketdesk(tmp_path):
    (tmp_path / learn.RULES).write_text(json.dumps({'series_deny': ['macro volatility digest'],
        'publisher_deny': ['Cboe'], 'doc_type_drop': ['research']}))
    assert decide(root=tmp_path).outcome == 'publish'


def test_always_publish_desk_metadata_is_retained_without_model_selection():
    decision = decide(title='Citadel Securities GMI Rubner',
                      folders=['2026', 'October', 'Oct 26', 'Citadel'])
    assert decision.outcome == 'publish' and decision.candidate is None
    assert decision.review['always_publish'] is True


def test_unknown_publisher_is_held():
    decision = decide(folders=["2026", "October", "Oct 26"], pages={1: WORDS * 2})
    assert decision.outcome == "held" and decision.reason_code == "PUBLISHER_UNKNOWN"


def test_near_duplicate_of_a_published_document_drops():
    fp = novelty.fingerprint(" ".join(PAGES[p] for p in sorted(PAGES)))
    decision = decide(index={"marketdesk:other": (fp, "Cboe")})
    assert decision.outcome == "dropped" and decision.reason_code == "DUPLICATE_OF_PUBLISHED"
    assert decision.review["novelty"]["duplicate_of"] == "marketdesk:other"


def test_own_fingerprint_is_not_a_duplicate():
    fp = novelty.fingerprint(" ".join(PAGES[p] for p in sorted(PAGES)))
    assert decide(index={"marketdesk:4AJs5Adl2Yp": (fp, "Cboe")}).outcome == "publish"


def test_always_publish_desk_survives_a_duplicate_from_another_publisher():
    pages = {1: "Citadel Securities Global Market Intelligence\n" + WORDS * 2}
    fp = novelty.fingerprint(pages[1])
    decision = decide(pages=pages, title="Citadel Securities GMI Rubner", folders=["2026", "October", "Oct 26", "Citadel"],
                      index={"marketdesk:other": (fp, "Goldman Sachs")})
    assert decision.outcome == "publish"


def test_operator_note_is_retained_without_overriding_safety():
    note = {'kind': 'publish', 'comment': 'this one matters', 'feedback_id': 'f1'}
    decision = decide(note=note)
    assert decision.outcome == 'publish'
    assert decision.review['operator_note'] == note
    assert decide(note=note, summary=[]).reason_code == 'SOURCE_MATERIAL_MISSING'


def test_operator_preferences_cannot_restore_model_relevance_veto(tmp_path):
    (tmp_path / learn.EXAMPLES).write_text(json.dumps({'examples': [
        {'id': 'v1', 'verdict': 'not wanted', 'vote': 'down', 'target': 'post', 'title': 'Cboe recap',
         'publisher': 'Cboe', 'series': 'macro volatility digest', 'docType': 'research',
         'reasons': ['not_relevant'], 'comment': 'too routine'}]}))
    assert decide(root=tmp_path).outcome == 'publish'


def test_index_reads_research_fingerprints_and_ledger(tmp_path):
    (tmp_path / intake.FINGERPRINTS).write_text(json.dumps({"documents": {"dropbox:a": "00000000000000ff"},
                                                            "publishers": {"dropbox:a": "UBS"}}))
    index = mf.fingerprint_index(tmp_path, [("art1", "0000000000000f0f", "Cboe"), ("art2", None, None)])
    assert index == {"dropbox:a": (0xff, "UBS"), "marketdesk:art1": (0xf0f, "Cboe")}


def test_index_tolerates_missing_or_corrupt_research_file(tmp_path):
    (tmp_path / intake.FINGERPRINTS).write_text("{nope")
    assert mf.fingerprint_index(tmp_path, []) == {}
    assert mf.fingerprint_index(tmp_path / "absent", []) == {}


@pytest.mark.parametrize('pages,title,book', [
    ({1: '# EURUSD: Range trade\n' + WORDS}, 'EURUSD note', {'SPY'}),

])
def test_fx_reports_bypass_book_and_model_relevance(pages, title, book):
    decision = decide(pages=pages, title=title, book=book)
    assert decision.outcome == 'publish' and decision.candidate is None
    assert decision.review['admission_policy'] == 'current_book_issuer_research'


@pytest.mark.parametrize("pages,summary", [({1: "Only a title"}, []), ({1: "Only a title"}, ["", "  "]), ({}, SUMMARY)])
def test_all_suitable_policy_cannot_admit_empty_source_material(pages, summary):
    decision = decide(pages=pages, summary=summary)
    assert decision.outcome == 'held' and decision.reason_code == 'SOURCE_MATERIAL_MISSING'

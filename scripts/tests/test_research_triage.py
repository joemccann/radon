"""Recall-first triage: only document types that never published are dropped."""
from research import identify, triage


def ident(doc_type, series_name="x"):
    return identify.Identity("UBS", "folder", "ubs", series_name, "2026-09-17", "text", 1, "17 September 2026", doc_type, "")


def test_fx_pair_notes_are_dropped_with_a_reason_code():
    assert triage.decide(ident("fx_pair_note")) == ("drop", "DOC_TYPE_FX_PAIR_NOTE")


def test_calendars_reach_review_until_the_operator_approves_a_rule():
    # The operator upvoted "MACRO WEEK AHEAD" (Fed speaker dates) on 2026-09-20, so a calendar is not dropped by code.
    assert triage.decide(ident("calendar")) == ("review", None)
    assert triage.decide(ident("calendar"), rules={"doc_type_drop": {"calendar"}}) == ("drop", "RULE_DOC_TYPE")


def test_types_that_have_published_are_kept():
    for kind in ("research", "single_stock", "conference", "digest"):
        assert triage.decide(ident(kind)) == ("review", None)


def test_series_denylist_is_explicit_and_wins():
    assert triage.decide(ident("research", "daily shortactivity us cleared"), denylist={"daily shortactivity us cleared"}) == ("drop", "SERIES_DENYLIST")

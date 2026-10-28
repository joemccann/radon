"""Offline publication gate for PDF newsfeed title/body editorial scoring."""
import copy

import pytest

from research import humanizer


def post(content="Goldman Sachs reports $45bn of net equity purchases in July.", **extra):
    return {"title": "Foreign equity purchases rose in July", "content": content,
            "source": {"pages": [1], "figures": [{"caption": "Equity purchases"}]} , **extra}


def finding(content, text, category="significance_inflation", field="content"):
    start = content.index(text)
    return {"field": field, "start": start, "end": start + len(text), "category": category}


def scripted(*responses):
    calls = []
    def ask(instruction):
        calls.append(instruction)
        response = responses[len(calls)-1]
        if isinstance(response, Exception):
            raise response
        return response
    ask.calls = calls
    return ask


def clean():
    return {"findings": [], "qualitative": []}


def run(value=None, *responses, verify=lambda _: True, max_rewrites=2):
    records = []
    ask = scripted(*(responses or (clean(),)))
    result = humanizer.review_post(value or post(), ask=ask, verify=verify,
                                   record=records.append, max_rewrites=max_rewrites)
    latest = {item["attempt"]: item for item in records}
    return result, list(latest.values()), ask.calls


def test_clean_copy_preserves_source_and_certifies_all_copy():
    original = post()
    value, records, calls = run(original)
    assert original == post()
    assert value["source"]["figures"] == original["source"]["figures"]
    assert value["source"]["humanizer"]["score"] == 100
    assert records[0]["title"] == original["title"]
    assert records[0]["content"] == original["content"]
    assert records[0]["status"] == "passed"
    assert "Equity purchases" in calls[0]
    humanizer.validate_certificate(value)


def test_local_vocabulary_repeated_case_insensitive_and_exact_boundaries():
    value, audits, _ = run(post("ROBUST robust robustly"), clean(), max_rewrites=0)
    assert value is not None
    assert audits[0]["score"] == 90
    assert [(f["text"], f["deduction"]) for f in audits[0]["findings"]] == [("ROBUST", 5), ("robust", 5)]


def test_threshold_requires_rewrite_rescore_and_fact_check():
    original = post("Robust innovative seamless equity purchases were $45bn.")
    revised = {"title": original["title"], "content": "Equity purchases were $45bn."}
    verified = []
    value, audits, calls = run(original, clean(), revised, clean(),
                              verify=lambda p: verified.append(copy.deepcopy(p)) or True)
    assert [a["score"] for a in audits] == [85, 100]
    assert [a["status"] for a in audits] == ["rewrite_required", "passed"]
    assert len(calls) == 3 and len(verified) == 1
    assert verified[0]["source"] == original["source"]
    assert "REWRITE" in calls[1]
    humanizer.validate_certificate(value)


def test_model_score_cannot_override_code_score():
    value, audits, _ = run(post("Robust innovative seamless."), {**clean(), "score": 100}, max_rewrites=0)
    assert value is None and audits[0]["score"] == 85


@pytest.mark.parametrize("category,weight", list({
    "significance_inflation": 10, "undue_notability": 5, "superficial_ing": 8,
    "promotional_language": 8, "vague_attribution": 8}.items()))
def test_fixed_numeric_categories_and_double_counting(category, weight):
    text = "A robust result."
    f = finding(text, "robust", category)
    _, audits, _ = run(post(text), {"findings": [f, f], "qualitative": []}, max_rewrites=0)
    assert audits[0]["score"] == 95-weight
    assert len(audits[0]["findings"]) == 2


def test_score_clamps_at_zero():
    _, audits, _ = run(post(" ".join(["robust"]*30)), clean(), max_rewrites=0)
    assert audits[0]["score"] == 0


@pytest.mark.parametrize("word", ["landscape", "underscore", "highlight"])
def test_contextual_vocabulary_requires_contextual_model_finding(word):
    text = f"A {word} appears in the source."
    value, audits, _ = run(post(text), clean())
    assert value and audits[0]["score"] == 100
    f = finding(text, word, "vocabulary")
    value, audits, _ = run(post(text), {"findings": [f], "qualitative": []})
    assert value and audits[0]["score"] == 95


def test_unprovided_weights_are_qualitative_blockers_even_at_100():
    original = post("In order to report purchases, Goldman Sachs counted $45bn.")
    f = finding(original["content"], "In order to", "filler")
    revised = {"title": original["title"], "content": "Goldman Sachs counted $45bn of purchases."}
    value, audits, _ = run(original, {"findings": [], "qualitative": [f]}, revised, clean())
    assert audits[0]["score"] == 100 and audits[0]["qualitative"][0]["deduction"] == 0
    assert value["content"] == revised["content"]


def test_unchanged_rewrite_cannot_get_a_new_score():
    original = post("Robust innovative seamless.")
    value, audits, calls = run(original, clean(), {k: original[k] for k in ("title", "content")}, clean())
    assert value is None and len(calls) == 2
    assert audits[-1]["status"] == "unchanged_rewrite"


def test_reviewer_cannot_drop_unchanged_qualitative_passage_after_other_edit():
    original = post("In order to count purchases, Goldman Sachs checked July data.")
    f = finding(original["content"], "In order to", "filler")
    revised = {"title": "July equity purchases", "content": original["content"]}
    value, audits, _ = run(original, {"findings": [], "qualitative": [f]}, revised, clean(), max_rewrites=1)
    assert value is None and audits[-1]["qualitative"][0]["text"] == "In order to"


def test_rewrite_factual_failure_holds_before_style_reassessment():
    original = post("Robust innovative seamless purchases of $45bn.")
    value, audits, calls = run(original, clean(), {"title": original["title"], "content": "$46bn."}, verify=lambda _: False)
    assert value is None and len(calls) == 2
    assert audits[-1]["status"] == "factual_verification_failed"


@pytest.mark.parametrize("response", [None, [], "{}", {}, {"findings": []},
    {"findings": [], "qualitative": "none"},
    {"findings": ["invalid"], "qualitative": []},
    {"findings": [{"field": "source", "start": 0, "end": 1, "category": "vocabulary"}], "qualitative": []},
    {"findings": [{"field": "title", "start": True, "end": 2, "category": "vocabulary"}], "qualitative": []},
    {"findings": [{"field": "title", "start": 0, "end": 999, "category": "vocabulary"}], "qualitative": []},
    {"findings": [{"field": "title", "start": 0, "end": 7, "category": "invented"}], "qualitative": []},
    {"findings": [{"field": "title", "start": 0, "end": 7, "category": "vocabulary"}], "qualitative": []}])
def test_malformed_review_fails_closed(response):
    value, audits, calls = run(post(), response)
    assert value is None and audits[-1]["status"] == "malformed_review" and len(calls) == 1


@pytest.mark.parametrize("rewrite", [{}, {"title": "x", "content": "x", "source": {}},
    {"title": "x", "content": ""}, {"title": 1, "content": "x"}, []])
def test_malformed_rewrite_cannot_edit_structural_fields(rewrite):
    value, audits, _ = run(post("Robust innovative seamless."), clean(), rewrite)
    assert value is None and audits[-1]["status"] == "malformed_rewrite"


def test_rewrite_limit_is_finite():
    original = post("Robust innovative seamless.")
    value, audits, calls = run(original, clean(), {"title": "Different", "content": original["content"]}, clean(), max_rewrites=1)
    assert value is None and len(calls) == 3 and audits[-1]["status"] == "rewrite_limit"


@pytest.mark.parametrize("count", [-1, True, 10])
def test_invalid_rewrite_budget_rejected(count):
    with pytest.raises(ValueError):
        run(max_rewrites=count)


def test_provider_errors_are_recorded_and_propagated_for_durable_retry():
    audits = []
    with pytest.raises(TimeoutError):
        humanizer.review_post(post(), ask=scripted(TimeoutError("offline")), verify=lambda _: True, record=audits.append)
    assert audits[-1]["status"] == "reviewer_error"


def test_verifier_errors_propagate_for_durable_retry():
    audits = []
    def fail(_):
        raise TimeoutError("offline")
    original = post("Robust innovative seamless.")
    with pytest.raises(TimeoutError):
        humanizer.review_post(original, ask=scripted(clean(), {"title": "July data", "content": "Purchases rose."}), verify=fail, record=audits.append)
    assert audits[-1]["status"] == "verification_error"


@pytest.mark.parametrize("field", ["title", "content"])
def test_certificate_binds_every_final_copy_field(field):
    value, _, _ = run()
    value[field] += " Changed."
    with pytest.raises(ValueError):
        humanizer.validate_certificate(value)


@pytest.mark.parametrize("mutation", [
    lambda p: p["source"].pop("humanizer"),
    lambda p: p["source"]["humanizer"].update(score=89),
    lambda p: p["source"]["humanizer"].update(score=True),
    lambda p: p["source"]["humanizer"].update(rubric_version="old"),
    lambda p: p["source"]["humanizer"].update(findings=None),
    lambda p: p["source"]["humanizer"].update(qualitative=[{}]),
])
def test_certificate_validation_fails_closed(mutation):
    value, _, _ = run()
    mutation(value)
    with pytest.raises(ValueError):
        humanizer.validate_certificate(value)


def test_certificate_cannot_drop_known_vocabulary_findings():
    value, _, _ = run(post("Robust purchases."))
    value["source"]["humanizer"].update(findings=[], score=100)
    with pytest.raises(ValueError):
        humanizer.validate_certificate(value)


def test_certificate_cannot_edit_deduction_or_text():
    value, _, _ = run(post("Robust purchases."))
    value["source"]["humanizer"]["findings"][0]["deduction"] = 0
    with pytest.raises(ValueError):
        humanizer.validate_certificate(value)


def test_prompt_vendors_full_rule_without_inventing_missing_weights():
    _, _, calls = run()
    for phrase in ("90", "significance_inflation", "undue_notability", "superficial_ing",
                   "promotional_language", "vague_attribution", "generic_positive_conclusion",
                   "untrusted", "source", "no numerical weights", "do not rewrite"):
        assert phrase.lower() in calls[0].lower()


@pytest.mark.parametrize("extra", [{"text": "invented"}, {"start": 7, "end": 8}])
def test_findings_must_quote_nonempty_actual_text(extra):
    original = post("Robust purchases.")
    f = finding(original["content"], "Robust", "promotional_language")
    f.update(extra)
    if "start" in extra:
        f.update(start=6, end=7)
    value, audits, _ = run(original, {"findings": [f], "qualitative": []})
    assert value is None and audits[-1]["status"] == "malformed_review"


def test_missing_source_rejected_without_provider_call():
    with pytest.raises(ValueError, match="source"):
        run(post(source=None))


def test_rewrite_outage_is_recorded_and_propagated():
    audits = []
    with pytest.raises(TimeoutError):
        humanizer.review_post(post("Robust innovative seamless."),
                              ask=scripted(clean(), TimeoutError("offline")),
                              verify=lambda _: True, record=audits.append)
    assert audits[-1]["status"] == "rewrite_error"


def test_initial_draft_persisted_before_model_call():
    events = []
    def ask(_):
        assert events[-1]["status"] == "reviewing"
        assert events[-1]["copy_sha256"] == humanizer.copy_digest(post())
        return clean()
    humanizer.review_post(post(), ask=ask, verify=lambda _: True, record=events.append)


def test_both_rewrites_are_fact_verified_and_fully_rescored():
    initial = post("Robust innovative seamless.")
    first = {"title": initial["title"], "content": "Robust innovative seamless evidence."}
    second = {"title": initial["title"], "content": "July evidence."}
    verified = []
    value, audits, calls = run(initial, clean(), first, clean(), second, clean(),
                              verify=lambda p: verified.append(p["content"]) or True)
    assert value and [a["score"] for a in audits] == [85, 85, 100]
    assert verified == [first["content"], second["content"]] and len(calls) == 5


@pytest.mark.parametrize("word", ["underscores", "underscored", "underscoring", "highlights", "highlighted", "highlighting"])
def test_contextual_verbs_accept_grammatical_forms(word):
    text = f"The report {word} the change."
    f = finding(text, word, "vocabulary")
    value, audits, _ = run(post(text), {"findings": [f], "qualitative": []})
    assert value and audits[0]["score"] == 95
    humanizer.validate_certificate(value)


def test_contextual_verb_can_receive_both_vocabulary_and_pattern_deduction():
    text = "Buying rose, highlighting the importance of equities."
    vocabulary = finding(text, "highlighting", "vocabulary")
    pattern = finding(text, "highlighting the importance of equities", "superficial_ing")
    value, audits, _ = run(post(text), {"findings": [vocabulary, pattern], "qualitative": []}, max_rewrites=0)
    assert value is None and audits[0]["score"] == 87


def test_vocabulary_cannot_be_fabricated_inside_an_unlisted_word():
    original = post("A robustly measured result.")
    f = finding(original["content"], "robust", "vocabulary")
    value, audits, _ = run(original, {"findings": [f], "qualitative": []})
    assert value is None and audits[-1]["status"] == "malformed_review"


def test_exact_quote_contract_does_not_require_counting_unicode_offsets():
    text = "€ data: experts argue demand rose; experts argue supply fell."
    f = {"field": "content", "text": "experts argue", "category": "vague_attribution"}
    value, audits, _ = run(post(text), {"findings": [f], "qualitative": []}, max_rewrites=0)
    assert value is None and audits[0]["score"] == 84
    assert [item["start"] for item in audits[0]["findings"]] == [8, 35]


def test_overlapping_same_category_findings_are_one_distinct_passage():
    text = "It stands as a pivotal moment in equities."
    first = finding(text, "stands as a pivotal moment", "significance_inflation")
    second = finding(text, "a pivotal moment in equities", "significance_inflation")
    value, audits, _ = run(post(text), {"findings": [second, first], "qualitative": []}, max_rewrites=0)
    assert value is None and audits[0]["score"] == 85  # one -10 passage plus pivotal -5
    patterns = [item for item in audits[0]["findings"] if item["category"] == "significance_inflation"]
    assert len(patterns) == 1 and patterns[0]["text"] == "stands as a pivotal moment in equities"


def test_broad_finding_cannot_bridge_two_disjoint_same_category_passages():
    text = "This reflects broader conditions. It sets the stage for growth."
    first = finding(text, "reflects broader conditions", "significance_inflation")
    second = finding(text, "sets the stage for growth", "significance_inflation")
    bridge = finding(text, text, "significance_inflation")
    value, audits, _ = run(post(text), {"findings": [first, second, bridge], "qualitative": []}, max_rewrites=0)
    assert value is None and audits[0]["score"] == 80
    assert [item["text"] for item in audits[0]["findings"]] == [
        "reflects broader conditions", "sets the stage for growth",
    ]


def test_new_broad_finding_cannot_bridge_unchanged_previous_passages():
    text = "This reflects broader conditions. It sets the stage for growth."
    first = finding(text, "reflects broader conditions", "significance_inflation")
    second = finding(text, "sets the stage for growth", "significance_inflation")
    bridge = finding(text, text, "significance_inflation")
    previous = humanizer._normalise_findings(post(text), [first, second])
    current = humanizer._normalise_findings(post(text), [bridge])
    combined = humanizer._dedupe(previous + current)
    assert [item["text"] for item in combined] == [
        "reflects broader conditions", "sets the stage for growth",
    ]


def test_moving_capitalized_offending_passage_between_fields_does_not_erase_it():
    text = "In order to count purchases, Goldman Sachs checked July data."
    original = post(text)
    f = finding(text, "In order to", "filler")
    revised = {"title": "IN ORDER TO count purchases", "content": "Goldman Sachs checked July data."}
    value, audits, _ = run(original, {"findings": [], "qualitative": [f]}, revised, clean(), max_rewrites=1)
    assert value is None
    assert audits[-1]["qualitative"][0]["field"] == "title"
    assert audits[-1]["qualitative"][0]["text"] == "IN ORDER TO"


@pytest.mark.parametrize("text", [None, "", "  ", "invented passage"])
def test_quoted_findings_require_nonempty_literal_copy(text):
    f = {"field": "content", "text": text, "category": "vague_attribution"}
    value, audits, _ = run(post(), {"findings": [f], "qualitative": []})
    assert value is None and audits[-1]["status"] == "malformed_review"


def test_quoted_vocabulary_locates_all_whole_word_occurrences_only():
    text = "Robust robustly Robust"
    f = {"field": "content", "text": "Robust", "category": "vocabulary"}
    value, audits, _ = run(post(text), {"findings": [f], "qualitative": []})
    assert value and audits[0]["score"] == 90
    assert len(audits[0]["findings"]) == 2
    humanizer.validate_certificate(value)


def test_coalesced_pattern_certificate_validates_merged_exact_copy():
    text = "Experts argue demand grew this month."
    first = {"field": "content", "text": "Experts argue demand", "category": "vague_attribution"}
    second = {"field": "content", "text": "argue demand grew", "category": "vague_attribution"}
    value, audits, _ = run(post(text), {"findings": [first, second], "qualitative": []})
    assert value and audits[0]["score"] == 92
    assert audits[0]["findings"][0]["text"] == "Experts argue demand grew"
    humanizer.validate_certificate(value)


def test_duplicate_model_quotes_are_deduplicated_before_occurrence_expansion(monkeypatch):
    original = humanizer.re.finditer
    searches = []
    def counted(*args, **kwargs):
        searches.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(humanizer.re, "finditer", counted)
    f = {"field": "content", "text": "e", "category": "filler"}
    values = humanizer._normalise_findings(post("e"*2000), [f]*32, qualitative=True)
    assert len(values) == 2000 and len(searches) == 1


def test_carry_deduplicates_casefolded_needles_before_searching_all_fields(monkeypatch):
    original = humanizer.re.finditer
    searches = []
    def counted(*args, **kwargs):
        searches.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(humanizer.re, "finditer", counted)
    previous = [{"field": "content", "start": i*7, "end": i*7+6,
                 "text": "robust" if i%2 else "ROBUST", "category": "vocabulary", "deduction": 5}
                for i in range(200)]
    values = humanizer._carry_unchanged(post(" ".join(["robust"]*200)), previous)
    assert len(values) == 200 and len(searches) == 3


def test_local_finding_overflow_holds_before_model_call_and_keeps_full_draft():
    original = post(" ".join(["robust"]*4097))
    value, audits, calls = run(original, clean(), max_rewrites=0)
    assert value is None and calls == []
    assert audits[-1]["status"] == "finding_budget_exceeded" and audits[-1]["score"] is None
    assert audits[-1]["content"] == original["content"] and audits[-1]["findings"] == []


def test_model_finding_overflow_holds_without_partial_score():
    f = {"field": "content", "text": "e", "category": "filler"}
    value, audits, calls = run(post("e"*4097), {"findings": [], "qualitative": [f]}, max_rewrites=0)
    assert value is None and len(calls) == 1
    assert audits[-1]["status"] == "finding_budget_exceeded" and audits[-1]["score"] is None
    assert audits[-1]["findings"] == audits[-1]["qualitative"] == []


def test_finding_budget_is_shared_across_local_and_model_collections():
    f = {"field": "content", "text": "e", "category": "filler"}
    value, audits, _ = run(post("robust " + "e"*4096), {"findings": [], "qualitative": [f]}, max_rewrites=0)
    assert value is None and audits[-1]["status"] == "finding_budget_exceeded"
    assert audits[-1]["score"] is None


def test_occurrence_expansion_stops_while_iterating_at_budget(monkeypatch):
    original = humanizer.re.finditer
    visits = []
    def counted(*args, **kwargs):
        for match in original(*args, **kwargs):
            visits.append(1)
            yield match
    monkeypatch.setattr(humanizer.re, "finditer", counted)
    f = {"field": "content", "text": "e", "category": "filler"}
    value, audits, _ = run(post("e"*20000), {"findings": [], "qualitative": [f]}, max_rewrites=0)
    assert value is None and audits[-1]["status"] == "finding_budget_exceeded"
    assert len(visits) == 4097


def test_oversized_certificate_is_rejected_before_duplicate_expansion():
    value, _, _ = run()
    f = finding(value["content"], "Goldman Sachs", "vague_attribution")
    value["source"]["humanizer"].update(findings=[f]*4097, score=92)
    with pytest.raises(ValueError, match="budget"):
        humanizer.validate_certificate(value)


def test_carry_finding_budget_caps_generated_occurrences():
    f = {"field": "content", "start": 0, "end": 1, "text": "e", "category": "filler", "deduction": 0}
    with pytest.raises(ValueError, match="budget"):
        humanizer._carry_unchanged(post(" ".join(["e"]*4097)), [f])


def test_dedupe_refuses_oversized_materialized_collection():
    f = {"field": "content", "start": 0, "end": 1, "text": "e", "category": "filler", "deduction": 0}
    with pytest.raises(ValueError, match="budget"):
        humanizer._dedupe([f]*4097)


def test_carry_refuses_oversized_previous_inventory_before_search(monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail("oversized carry must fail before searching")
    monkeypatch.setattr(humanizer.re, "finditer", unexpected)
    f = {"field": "content", "text": "e", "category": "filler", "deduction": 0}
    with pytest.raises(ValueError, match="budget"):
        humanizer._carry_unchanged(post("e"), [f]*4097)


def test_duplicate_descriptor_cannot_hide_malformed_weight():
    f = {"field": "content", "text": "e", "category": "filler"}
    with pytest.raises(ValueError, match="deduction"):
        humanizer._normalise_findings(post("e"), [f, {**f, "deduction": 5}], qualitative=True)


def _review_prompt(value):
    return humanizer.RUBRIC_INSTRUCTION + "\nUNTRUSTED SUMMARY JSON:\n" + __import__("json").dumps(
        humanizer._summary(value), ensure_ascii=False)


@pytest.mark.parametrize("response", [None, [], "{}", {}, {"findings": []},
    {"findings": [], "qualitative": "none"},
    {"findings": ["invalid"], "qualitative": []},
    {"findings": [{"field": "source", "start": 0, "end": 1, "category": "vocabulary"}], "qualitative": []},
    {"findings": [{"field": "title", "start": True, "end": 2, "category": "vocabulary"}], "qualitative": []},
    {"findings": [{"field": "title", "start": 0, "end": 999, "category": "vocabulary"}], "qualitative": []},
    {"findings": [{"field": "title", "start": 0, "end": 7, "category": "invented"}], "qualitative": []},
    {"findings": [{"field": "content", "text": "not in the copy", "category": "filler"}], "qualitative": []}])
def test_reply_ok_rejects_every_review_review_post_would_call_malformed(response):
    """A ladder uses reply_ok as its accept hook, so a rung that misquotes the copy falls to the next rung
    instead of failing the article closed (malformed_review)."""
    assert humanizer.reply_ok(_review_prompt(post()), response) is False


def test_reply_ok_accepts_well_formed_reviews():
    value = post()
    assert humanizer.reply_ok(_review_prompt(value), clean()) is True
    flagged = {"findings": [finding(value["content"], "net equity purchases")], "qualitative": []}
    assert humanizer.reply_ok(_review_prompt(value), flagged) is True


@pytest.mark.parametrize("rewrite,ok", [({"title": "x", "content": "y"}, True), ({}, False),
    ({"title": "x", "content": "y", "source": {}}, False), ({"title": "x", "content": " "}, False),
    ({"title": 1, "content": "x"}, False), ([], False)])
def test_reply_ok_checks_rewrite_shape(rewrite, ok):
    assert humanizer.reply_ok(humanizer.REWRITE_INSTRUCTION + "{}", rewrite) is ok


@pytest.mark.parametrize('text', [
    "That's a conditional dollar-strength setup, not a confirmed breakout.",
    'DXY is testing 102.86, not a confirmed breakout.',
    "It isn't a breakout. It's a conditional setup.",
    "This is not a rally but a positioning squeeze.",
    'Not only demand but also supply improved.',
    'The risk is positioning—not earnings.',
    "That's a conditional setup. Not a confirmed breakout.",
    "That's a conditional setup. It's not a confirmed breakout.",
    'The risk is positioning. It is not earnings.',
    'The risk is positioning.\nNot earnings.',
    'I hope this helps.',
])
def test_clean_model_verdict_cannot_bypass_local_qualitative_rules(text):
    value, audits, _ = run(post(text), clean(), max_rewrites=0)
    assert value is None
    assert audits[0]['qualitative']
    assert all(f['deduction'] == 0 for f in audits[0]['qualitative'])


def test_local_qualitative_keeps_measured_contrasts_and_conditionality():
    text = 'DXY rose 2%, not 3%. The measured gain was 2%. Not 3%. If DXY closes above 104.59, Pictet targets 110. USD/CHF fell while USD/JPY rose.'
    value, _, _ = run(post(text), clean())
    assert value['content'] == text


def test_sentence_split_negative_parallelism_cannot_pass_clean_review():
    text = "That's a conditional dollar-strength setup. Not a confirmed breakout."
    value, audits, _ = run(post(text), clean(), max_rewrites=0)
    assert value is None
    assert audits[-1]['qualitative'][0]['category'] == 'negative_parallelism'


def test_certificate_cannot_forge_clean_verdict_for_negative_parallelism():
    value, _, _ = run(post(), clean())
    value['content'] = "That's a conditional setup, not a confirmed breakout."
    value['source']['humanizer']['copy_sha256'] = humanizer.copy_digest(value)
    with pytest.raises(ValueError, match='rubric'):
        humanizer.validate_certificate(value)


def test_caption_is_reviewed_and_bound_to_certificate():
    original = post()
    original['source']['figures'][0]['caption'] = 'Robust chart highlight'
    value, _, calls = run(original, clean())
    assert 'Robust chart highlight' in calls[0]
    assert value['source']['humanizer']['score'] == 95
    value['source']['figures'][0]['caption'] = 'An altered caption'
    with pytest.raises(ValueError, match='stale'):
        humanizer.validate_certificate(value)


def test_caption_qualitative_blocks_clean_model_verdict():
    original = post()
    original['source']['figures'][0]['caption'] = 'A setup, not a confirmed breakout.'
    value, _, _ = run(original, clean(), max_rewrites=0)
    assert value is None


def test_model_can_flag_caption_with_qualitative_rubric():
    original = post()
    quote = original['source']['figures'][0]['caption']
    reply = {'findings': [], 'qualitative': [{'field': 'caption:0', 'text': quote, 'category': 'filler'}]}
    assert humanizer.reply_ok(_review_prompt(original), reply)
    value, audits, _ = run(original, reply, max_rewrites=0)
    assert value is None
    assert audits[-1]['qualitative'][0]['field'] == 'caption:0'


def test_flat_caption_key_cannot_override_source_caption_at_publication():
    original = post()
    original['source']['figures'][0]['caption'] = "A setup, not a confirmed breakout."
    original['caption:0'] = 'Flows'
    value, _, _ = run(original, clean(), max_rewrites=0)
    assert value is None


def test_rewrite_is_rescanned_when_model_calls_new_contrast_clean():
    original = post('Robust innovative seamless purchases were $45bn.')
    revision = {'title': original['title'], 'content': 'Purchases were $45bn, not a breakout.'}
    value, audits, _ = run(original, clean(), revision, clean(), max_rewrites=1)
    assert value is None
    assert audits[-1]['score'] == 100
    assert audits[-1]['qualitative'][0]['category'] == 'negative_parallelism'


def test_same_caption_finding_cannot_disappear_after_body_rewrite():
    original = post('Robust innovative seamless purchases were $45bn.')
    reply = {'findings': [], 'qualitative': [{'field': 'caption:0', 'text': 'Equity purchases', 'category': 'filler'}]}
    revision = {'title': original['title'], 'content': 'Purchases were $45bn.'}
    value, audits, _ = run(original, reply, revision, clean(), max_rewrites=1)
    assert value is None
    assert audits[-1]['qualitative'][0]['field'] == 'caption:0'

"""Exact source grounding prevents a verifier from waiving unsupported numbers."""
from research.pipeline import numeric_evidence_passed


def candidate(content="Real yields rose more than 70bps.",title="Yield divergence",captions=()):
    return {"title":title,"content":content,"pages":[6],"figures":[{"caption":c} for c in captions]}


def check(proposal,source,page=6):
    return {"proposal_quote":proposal,"page":page,"source_quote":source,"supported":True}


def test_recognized_unsupported_70bps_cannot_be_waived_as_directional():
    value=candidate();source={6:"Real yields have risen; policy easing is constrained."}
    verdict={"supported":True,"reason":"70bps is unsupported but directional", "numeric_checks":[check("more than 70bps",source[6])]}
    assert not numeric_evidence_passed(value,verdict,source)


def test_invented_source_quote_is_rejected_even_with_supported_true():
    value=candidate();source={6:"Real yields have risen."}
    assert not numeric_evidence_passed(value,{"numeric_checks":[check("more than 70bps","Real yields rose more than 70bps.")]},source)


def test_every_number_in_title_body_and_caption_requires_grounding():
    value=candidate("Sugar rose 21.5%.","Agriculture in 2026",["Since 2012"])
    source={6:"In 2026, sugar rose +21.5%, its strongest move since 2012."}
    checks=[check("21.5%","+21.5%"),check("2026","2026")]
    assert not numeric_evidence_passed(value,{"numeric_checks":checks},source)
    checks.append(check("2012","2012"))
    assert numeric_evidence_passed(value,{"numeric_checks":checks},source)


def test_wrong_unit_or_wrong_page_is_rejected():
    value=candidate();source={6:"Real yields rose 70%."}
    assert not numeric_evidence_passed(value,{"numeric_checks":[check("70bps","70%")]},source)
    source={6:"Real yields rose 70bps.",5:"Real yields rose 70bps."}
    assert not numeric_evidence_passed(value,{"numeric_checks":[check("70bps","70bps",page=5)]},source)


def test_whitespace_nfkc_and_equivalent_unit_spelling_are_supported():
    value=candidate("Real yields rose ７０ bps.")
    source={6:"Real yields rose 70\n basis points."}
    assert numeric_evidence_passed(value,{"numeric_checks":[check("７０ bps","70 basis points")]},source)


def test_instrument_tenor_dates_and_quarters_require_matching_values():
    value=candidate("US 10yr in Q4 2026",captions=["Figure 2"])
    source={6:"US 10yr in Q4 2026. Figure 2"}
    assert numeric_evidence_passed(value,{"numeric_checks":[check(value["content"],value["content"]),check("Figure 2","Figure 2")]},source)
    assert not numeric_evidence_passed(value,{"numeric_checks":[check("Q4 2026","Q4 2026"),check("Figure 2","Figure 2")]},source)


def test_qualitative_finding_can_pass_without_numeric_checks():
    assert numeric_evidence_passed(candidate("Real yields and equities diverged."),{}, {6:"Real yields and equities diverged."})


def test_leading_decimal_is_not_integer():
    value=candidate("Value 81")
    assert not numeric_evidence_passed(value,{"numeric_checks":[check("81",".81")]},{6:".81"})
    value=candidate("Value .81")
    assert numeric_evidence_passed(value,{"numeric_checks":[check(".81","0.81")]},{6:"0.81"})


def test_currency_identity_cannot_be_changed():
    for proposal,source in [("USD18.9bn","JPY18.9bn"),("¥18.9bn","$18.9bn")]:
        assert not numeric_evidence_passed(candidate(proposal),{"numeric_checks":[check(proposal,source)]},{6:source})
        assert numeric_evidence_passed(candidate(proposal),{"numeric_checks":[check(proposal,proposal)]},{6:proposal})


def test_malformed_and_incomplete_evidence_is_held():
    value=candidate('70bps');source={6:'70bps'}
    for verdict in [None,{}, {'numeric_checks':[]},{'numeric_checks':[{}]*81},
            {'numeric_checks':[None]}, {'numeric_checks':[check('70bps','70bps',True)]},
            {'numeric_checks':[dict(check('70bps','70bps'),supported=False)]},
            {'numeric_checks':[check('', '70bps')]},
            {'numeric_checks':[check('x'*2001, '70bps')]},
            {'numeric_checks':[check('70bps','x'*3001)]},
            {'numeric_checks':[check('70bps',None)]},
            {'numeric_checks':[check('absent70bps','70bps')]},
            {'numeric_checks':[check('70bps','70bps',5)]}]:
        assert not numeric_evidence_passed(value,verdict,source)
    assert not numeric_evidence_passed(value,{'numeric_checks':[check('70bps','70bps')]},{})
    value=candidate('Yields 70bps')
    assert not numeric_evidence_passed(value,{'numeric_checks':[check('Yields','Yields')]},{6:'Yields 70bps'})


def test_signed_values_and_ranges_are_not_conflated():
    assert numeric_evidence_passed(candidate('2011-2026'),{'numeric_checks':[check('2011-2026','2011–2026')]},{6:'2011–2026'})
    assert not numeric_evidence_passed(candidate('-21.5%'),{'numeric_checks':[check('-21.5%','+21.5%')]},{6:'+21.5%'})


def test_literal_substring_cannot_strip_decimal_sign_or_currency():
    for proposal,quote,source in [("81","81",".81"),("21.5%","21.5%","-21.5%"),("18.9bn","18.9bn","JPY18.9bn")]:
        assert not numeric_evidence_passed(candidate(proposal),{"numeric_checks":[check(proposal,quote)]},{6:source})


def test_selection_contract_preserves_source_numbers_and_ages():
    from research.pipeline import SELECT_SCHEMA, VERIFY_INSTRUCTION
    assert 'highest since early 2023' in SELECT_SCHEMA
    assert 'Do not derive relative ages, round values' in SELECT_SCHEMA
    assert 'not JSON metadata' in VERIFY_INSTRUCTION
    assert 'no arithmetic, rounding' in VERIFY_INSTRUCTION


def test_required_quotes_exclude_metadata_and_deduplicate_visible_values():
    from research.pipeline import required_numeric_quotes
    value=candidate('Sugar +21.5%, wheat +18.3%',title='Sugar +21.5%',captions=['Since 2012'])
    value['document_date']='2026-09-07'
    assert required_numeric_quotes(value)==['+21.5%', '+18.3%', '2012']

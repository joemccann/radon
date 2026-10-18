"""Generated evidence is publishable only after a fresh pixel + source review."""
import copy
from pathlib import Path

import pytest

from research import generated_charts as gc

TEXT = 'Japan September consumer inflation expectations rose to 4.35% from 4.15% in August.'
PLAN = {'kind': 'bar', 'title': 'Japan consumer inflation expectations', 'metric': 'Expected inflation (%)',
        'unit': '%', 'axis': [0, 5], 'ticks': [0, 1, 2, 3, 4, 5],
        'bars': [{'id': 'aug', 'label': 'August', 'value': 4.15}, {'id': 'sep', 'label': 'September', 'value': 4.35}]}
PROPOSAL = {'charts': [{'plan': PLAN, 'evidence': [
    {'id': 'aug', 'page': 1, 'source_quote': TEXT}, {'id': 'sep', 'page': 1, 'source_quote': TEXT}]}]}


def verdict(**updates):
    value = {key: True for key in ('interpretable', 'appropriate_chart_type', 'faithful_to_source',
             'labels_units_periods_correct', 'honest_axes', 'readable', 'no_invented_data',
             'compatible_measurements', 'estimation_status_correct')}
    value.update(visible_data=copy.deepcopy(PLAN['bars']), reason='August and September expectations, correctly ordered and labelled.',
                 visible_attribution={'publisher':'Goldman Sachs','radon_url':'https://radon.run'})
    value.update(updates)
    return value


class Agent:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def ask_text(self, prompt):
        self.calls.append((prompt, ()))
        return copy.deepcopy(self.reply)

    def ask(self, prompt, images=()):
        self.calls.append((prompt, images))
        return copy.deepcopy(self.reply)


def render(plan, output, **kwargs):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b'\x89PNG\r\n\x1a\n' + b'pixels')
    return {'path': str(output), 'width': 2400, 'height': 1440, 'renderer': 'd3-svg-playwright-v1'}


def generate(tmp_path, proposal=None, reviewed=None, renderer=render):
    producer = Agent(PROPOSAL if proposal is None else proposal)
    reviewer = Agent(verdict() if reviewed is None else reviewed)
    result = gc.generate({'title': TEXT, 'content': TEXT}, {1: TEXT}, tmp_path,
                         producer=producer, reviewer=reviewer, renderer=renderer,
                         publisher='Goldman Sachs', document_date='2026-10-09')
    return result, producer, reviewer


def test_separate_agents_review_actual_png_and_cited_source_before_acceptance(tmp_path):
    result, producer, reviewer = generate(tmp_path)
    assert result['status'] == 'passed'
    assert len(result['charts']) == 1
    assert result['charts'][0]['plan'] == PLAN
    assert len(producer.calls) == len(reviewer.calls) == 1
    assert reviewer.calls[0][1][0][1].read_bytes().startswith(b'\x89PNG')
    assert TEXT in reviewer.calls[0][0]
    assert 'untrusted' in producer.calls[0][0].lower()
    assert result['charts'][0]['review']['visible_data'] == PLAN['bars']


def test_attribution_page_outside_measurement_pages_reaches_image_auditor(tmp_path):
    proof = tmp_path / 'original-page-11.png'
    proof.write_bytes(b'original publisher page')
    quote = '© 2026 London Stock Exchange Group plc. All rights reserved.'
    attribution = {'page': 11, 'source_quote': quote, 'page_text': quote}
    auditor = Agent(verdict(visible_attribution={'publisher':'LSEG','radon_url':'https://radon.run'}))
    result = gc.generate({'title':TEXT}, {1:TEXT}, tmp_path, producer=Agent(PROPOSAL), reviewer=auditor,
                         renderer=render, publisher='LSEG', publisher_evidence=attribution,
                         publisher_images=[('Original publisher page 11', proof)])
    assert result['status'] == 'passed'
    prompt, images = auditor.calls[0]
    assert quote in prompt and '"page": 11' in prompt
    assert images[0][1] == tmp_path / 'chart-1.png'
    assert images[1] == ('Original publisher page 11', proof)


def test_unresolved_publisher_requires_honest_source_pdf_credit(tmp_path):
    auditor = Agent(verdict(visible_attribution={'publisher':'Source PDF','radon_url':'https://radon.run'}))
    result = gc.generate({'title':TEXT}, {1:TEXT}, tmp_path, producer=Agent(PROPOSAL), reviewer=auditor,
                         renderer=render, publisher='unknown')
    assert result['status'] == 'passed'


@pytest.mark.parametrize('field', ['interpretable', 'appropriate_chart_type', 'faithful_to_source',
                                 'labels_units_periods_correct', 'honest_axes', 'readable', 'no_invented_data',
                                 'compatible_measurements', 'estimation_status_correct'])
def test_any_review_failure_prevents_attachment(tmp_path, field):
    result, _, _ = generate(tmp_path, reviewed=verdict(**{field: False}))
    assert result['status'] == 'rejected' and result['charts'] == []
    assert result['attempts'][0]['review'][field] is False


def test_true_flags_without_correct_pixel_transcription_fail(tmp_path):
    wrong = copy.deepcopy(PLAN['bars']); wrong[0]['value'] = 4.35
    result, _, _ = generate(tmp_path, reviewed=verdict(visible_data=wrong))
    assert result['status'] == 'rejected' and result['charts'] == []


def test_fabricated_source_quote_or_value_never_reaches_renderer(tmp_path):
    proposal = copy.deepcopy(PROPOSAL)
    proposal['charts'][0]['plan']['bars'][0]['value'] = 6.15
    result, _, reviewer = generate(tmp_path, proposal=proposal)
    assert result['charts'] == [] and result['status'] == 'rejected'
    assert reviewer.calls == []


def test_uncited_quote_fails(tmp_path):
    proposal = copy.deepcopy(PROPOSAL)
    proposal['charts'][0]['evidence'][0]['page'] = 2
    result, _, _ = generate(tmp_path, proposal=proposal)
    assert result['status'] == 'rejected' and not result['charts']


def test_truncated_bar_axis_fails_before_render(tmp_path):
    proposal = copy.deepcopy(PROPOSAL); proposal['charts'][0]['plan']['axis'] = [4, 5]
    result, _, reviewer = generate(tmp_path, proposal=proposal)
    assert result['status'] == 'rejected' and not result['charts'] and not reviewer.calls


def test_no_comparable_data_is_explicit_and_never_reviewed(tmp_path):
    result, _, reviewer = generate(tmp_path, proposal={'charts': [], 'reason': 'No comparable measurements'})
    assert result['status'] == 'no_comparable_data' and not result['charts'] and not reviewer.calls


def test_renderer_failure_is_audited_and_no_png_attached(tmp_path):
    def broken(*args, **kwargs):
        raise RuntimeError('private renderer error')
    result, _, reviewer = generate(tmp_path, renderer=broken)
    assert result['status'] == 'unavailable' and not result['charts'] and not reviewer.calls
    assert 'private renderer error' not in str(result)


def test_partial_success_preserves_only_passed_png(tmp_path):
    proposal = copy.deepcopy(PROPOSAL)
    proposal['charts'].append(copy.deepcopy(proposal['charts'][0]))
    proposal['charts'][1]['plan']['bars'][0]['value'] = 8
    result, _, _ = generate(tmp_path, proposal=proposal)
    assert result['status'] == 'passed' and len(result['charts']) == 1 and len(result['attempts']) == 2


def test_optional_image_reviewer_outage_retains_text_without_approved_png(tmp_path):
    from research.model import ModelError
    class Outage(Agent):
        def ask(self, *args, **kwargs):
            raise ModelError('Model ladder exhausted: unavailable')
    result = gc.generate({'title': TEXT, 'content': TEXT}, {1: TEXT}, tmp_path,
                         producer=Agent(PROPOSAL), reviewer=Outage(None), renderer=render)
    assert result['status'] == 'unavailable' and result['charts'] == []
    assert result['attempts'][0]['status'] == 'review_unavailable'


def test_word_percent_is_a_valid_unit():
    assert gc._number_in_quote(4.35, '%', 'Japan expectations were 4.35 percent in September.')


def test_canonical_currency_units_are_checked_without_crashing():
    assert gc._number_in_quote(118.3, 'USD billion', 'The surplus was US$118.3bn.')
    assert not gc._number_in_quote(118.3, 'EUR billion', 'The surplus was US$118.3bn.')
    assert not gc._number_in_quote(118.3, 'USD million', 'The surplus was US$118.3bn.')


def test_single_agent_cannot_approve_its_own_chart(tmp_path):
    agent = Agent(PROPOSAL)
    with pytest.raises(ValueError, match='separate'):
        gc.generate({'title': TEXT}, {1: TEXT}, tmp_path, producer=agent, reviewer=agent, renderer=render)


def test_multiline_exact_pdf_quote_is_accepted(tmp_path):
    proposal = copy.deepcopy(PROPOSAL)
    for item in proposal['charts'][0]['evidence']:
        item['source_quote'] = TEXT.replace('from ', 'from\n')
    result, _, _ = generate(tmp_path, proposal=proposal)
    assert result['status'] == 'passed'


def test_unadvertised_render_reference_and_fabricated_annotation_rejected(tmp_path):
    proposal = copy.deepcopy(PROPOSAL)
    proposal['charts'][0]['plan']['reference'] = {'value': 2, 'label': 'Official target'}
    assert generate(tmp_path, proposal=proposal)[0]['status'] == 'rejected'
    proposal = copy.deepcopy(PROPOSAL)
    proposal['charts'][0]['plan']['note'] = 'Up 0.20 percentage points.'
    assert generate(tmp_path, proposal=proposal)[0]['status'] == 'rejected'


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(kind='pie'),
    lambda p: p.update(title=''),
    lambda p: p.update(bars=[]),
    lambda p: p['bars'][1].update(id='aug'),
    lambda p: p['bars'][1].update(label=''),
    lambda p: p['bars'][1].update(value=float('nan')),
    lambda p: p.update(axis=[5, 0]),
    lambda p: p.update(axis=[0, 4]),
    lambda p: p.update(ticks=[0]),
    lambda p: p.update(ticks=[0, 6]),
    lambda p: p.update(ticks=[0, 2, 1]),
    lambda p: p.update(unit=''),
    lambda p: p.update(note='x'*301),
    lambda p: p.update(note='x'*101),
    lambda p: p.update(note='x'*60, sourceNote='y'*60),
])
def test_structural_distortions_and_unbounded_plans_fail_before_render(tmp_path, mutate):
    proposal = copy.deepcopy(PROPOSAL); mutate(proposal['charts'][0]['plan'])
    result, _, reviewer = generate(tmp_path, proposal=proposal)
    assert result['status'] == 'rejected' and not reviewer.calls


@pytest.mark.parametrize('evidence', [None, [], ['wrong'], [{'id':'aug'}, {'id':'aug'}]])
def test_malformed_source_mapping_cannot_approve(tmp_path, evidence):
    proposal = copy.deepcopy(PROPOSAL); proposal['charts'][0]['evidence'] = evidence
    assert generate(tmp_path, proposal=proposal)[0]['status'] == 'rejected'


@pytest.mark.parametrize('reviewed', [None, {}, {'reason':'Looks fine'}, verdict(visible_data=[]),
                                     verdict(visible_data=[{'id':'aug','label':'August','value':True}, PLAN['bars'][1]]),
                                     verdict(visible_data=[PLAN['bars'][0],PLAN['bars'][0]])])
def test_incomplete_or_nonnumeric_visual_transcriptions_fail(tmp_path, reviewed):
    # None means malformed reply here, not generate helper's default verdict.
    producer, auditor = Agent(PROPOSAL), Agent(reviewed)
    result = gc.generate({'title':TEXT}, {1:TEXT}, tmp_path, producer=producer, reviewer=auditor, renderer=render)
    assert result['status'] == 'rejected' and not result['charts']


def test_failed_planning_provider_does_not_withhold_verified_text(tmp_path):
    from research.model import ModelError
    class Outage(Agent):
        def ask_text(self, *args):
            raise ModelError('Provider unavailable; private detail')
    result = gc.generate({'title':TEXT}, {1:TEXT}, tmp_path, producer=Outage(None), reviewer=Agent(None), renderer=render)
    assert result['status'] == 'unavailable' and result['charts'] == []
    assert 'private detail' not in str(result)


@pytest.mark.parametrize('proposal', [None, {}, {'charts':'wrong'}, {'charts':[{}]*4}])
def test_oversized_or_malformed_producer_reply_cannot_render(tmp_path, proposal):
    result = gc.generate({'title':TEXT}, {1:TEXT}, tmp_path, producer=Agent(proposal), reviewer=Agent(None), renderer=render)
    assert result['status'] == 'rejected' and result['attempts'] == [{'status':'invalid_proposal'}]


def test_percentage_direction_is_not_reversed():
    assert gc._number_in_quote(-13.27, '%', 'T-Mobile US slid 13.27% to end at $148.58.')
    assert not gc._number_in_quote(13.27, '%', 'T-Mobile US slid 13.27% to end at $148.58.')
    assert gc._number_in_quote(4.35, '%', 'Expected inflation fell to 4.35%.')


def test_actual_service_plans_and_evidence_survive_strict_validator():
    # Representative fixtures span positive temporal levels and signed peers.
    assert gc.valid_plan(PLAN)
    line = {'kind':'line', 'title':'Index levels', 'unit':'index', 'axis':[0,5], 'ticks':[0,1,2,3,4,5],
            'points':[{'id':str(i),'label':label,'value':value,'timestamp':stamp}
                      for i,label,value,stamp in [(1,'January 2026',2,1767225600000),(2,'February 2026',3,1769904000000),(3,'April 2026',4,1775001600000)]]}
    assert gc.valid_plan(line)
    for dates in ([1,1,2],[3,2,1],[1,True,3]):
        bad=copy.deepcopy(line)
        for row,date in zip(bad['points'], dates): row['timestamp']=date
        assert not gc.valid_plan(bad)
    bad=copy.deepcopy(line);bad['points']=bad['points'][:2];assert not gc.valid_plan(bad)
    ranges={'kind':'range','title':'Reported yields','unit':'%','axis':[0,6],'ticks':[0,2,4,6],
            'marks':[{'id':'a','label':'A','low':3,'high':4,'estimated':False},{'id':'b','label':'B','low':4,'high':5,'estimated':False}]}
    assert gc.valid_plan(ranges)
    bad=copy.deepcopy(ranges);bad['marks'][0]['estimated']=True;assert not gc.valid_plan(bad)
    scatter={'kind':'scatter','title':'Duration and yield','xUnit':'years','yUnit':'%','xAxis':[0,6],'yAxis':[0,6],
             'xTicks':[0,2,4,6],'yTicks':[0,2,4,6],'points':[{'id':'a','label':'A','x':2,'y':3},{'id':'b','label':'B','x':3,'y':4}]}
    assert gc.valid_plan(scatter)
    assert gc.valid_evidence(scatter,[{'id':'a','page':1,'source_quote':'A has 2 years duration and 3% yield.'},
                                     {'id':'b','page':1,'source_quote':'B has 3 years duration and 4% yield.'}],
                             {1:'A has 2 years duration and 3% yield. B has 3 years duration and 4% yield.'})


def test_positioning_currency_and_count_units_are_explicit():
    assert gc._number_in_quote(-0.7, 'z', 'OATs were sold -0.7z.')
    assert not gc._number_in_quote(0.7, 'z', 'OATs were sold -0.7z.')
    assert gc._number_in_quote(45, '$bn', 'US equities purchases were $45bn.')
    assert gc._number_in_quote(12, 'contracts', 'B had 12 contracts.')
    assert not gc._number_in_quote(12, 'contracts', 'B had 12 shares.')
    assert not gc._number_in_quote(12, 'FAKE dollars', 'B had 12 dollars.')


def test_quotes_must_be_literal_and_visible_ids_unique(tmp_path):
    proposal = copy.deepcopy(PROPOSAL)
    proposal['charts'][0]['evidence'][0]['source_quote'] = 'August expectations were 4.15% but this quote is invented.'
    assert generate(tmp_path, proposal=proposal)[0]['status'] == 'rejected'
    proposal = copy.deepcopy(PROPOSAL)
    proposal['charts'][0]['evidence'][0]['id'] = 'nonexistent'
    assert generate(tmp_path, proposal=proposal)[0]['status'] == 'rejected'
    reviewed = verdict(visible_data=['Looks fine',PLAN['bars'][1]])
    assert generate(tmp_path, reviewed=reviewed)[0]['status'] == 'rejected'


def test_line_dates_must_match_the_displayed_periods():
    plan = {'kind':'line','title':'Index levels','unit':'index','axis':[0,5],'ticks':[0,1,2,3,4,5],
            'points':[{'id':'a','label':'January 2026','value':2,'timestamp':1767225600000},
                      {'id':'b','label':'February 2026','value':3,'timestamp':1769904000000},
                      {'id':'c','label':'April 2026','value':4,'timestamp':1775001600000}]}
    assert gc.valid_plan(plan)
    plan['points'][1]['timestamp'] = 1772323200000  # March position with a February label distorts spacing.
    assert not gc.valid_plan(plan)


@pytest.mark.parametrize('label,expected', [('2026-01-01',1767225600000),('Q2 2026',1775001600000),
                                          ('2026',1767225600000),('Unknown period',None),
                                          ('2026-02-30',None),('January 0000',None)])
def test_supported_line_periods_have_exact_utc_coordinates(label, expected):
    assert gc._period_start(label) == expected


@pytest.mark.parametrize('attribution', [None, {'publisher':'Goldman Sachs','radon_url':'radon.run'},
                                        {'publisher':'unknown','radon_url':'https://radon.run'},
                                        {'publisher':'LSEG','radon_url':'https://radon.run'}])
def test_visible_source_and_exact_radon_url_must_match_rendered_credits(tmp_path, attribution):
    reviewed=verdict()
    reviewed['visible_attribution']=attribution
    assert generate(tmp_path,reviewed=reviewed)[0]['status']=='rejected'


BOUNDED_REVENUE_QUOTE = (
    "After guiding for FY27 AI revenue well above $100bn in March, the company's "
    "most recent guidance from September was for $115bn."
)


def test_revenue_guidance_floor_is_not_an_exact_observation_but_later_guidance_is():
    assert not gc._number_in_quote(100, 'USD billion', BOUNDED_REVENUE_QUOTE)
    assert gc._number_in_quote(115, 'USD billion', BOUNDED_REVENUE_QUOTE)


def test_floor_label_and_note_cannot_turn_a_bound_into_a_source_exact_point():
    plan = {'kind': 'bar', 'title': 'AI revenue guidance', 'metric': 'Revenue guidance',
            'unit': 'USD billion', 'axis': [0, 150], 'ticks': [0, 50, 100, 150],
            'bars': [{'id': 'prior', 'label': 'Prior guidance floor', 'value': 100},
                     {'id': 'latest', 'label': 'Latest guidance', 'value': 115}],
            'note': 'The prior bar shows a lower bound, not an exact measured amount.'}
    evidence = [{'id': row['id'], 'page': 1, 'source_quote': BOUNDED_REVENUE_QUOTE}
                for row in plan['bars']]
    assert gc.valid_plan(plan)
    assert not gc.valid_evidence(plan, evidence, {1: BOUNDED_REVENUE_QUOTE})


@pytest.mark.parametrize('quote,value', [
    ('The deficit is approximately 5.4%.', 5.4),
    ('The deficit is roughly 5.4%.', 5.4),
    ('The deficit is about 5.4%.', 5.4),
    ('The deficit is around 5.4%.', 5.4),
    ('The deficit is ~5.4%.', 5.4),
    ('The yield is above 4.75%.', 4.75),
    ('The yield has topped 4.75% in recent weeks.', 4.75),
    ('The yield is at least 4.75%.', 4.75),
    ('The yield is more than 4.75%.', 4.75),
    ('The yield is greater than 4.75%.', 4.75),
    ('The yield is >4.75%.', 4.75),
    ('The yield is below 4.75%.', 4.75),
    ('The yield is at most 4.75%.', 4.75),
    ('The yield is less than 4.75%.', 4.75),
    ('The yield is no more than 4.75%.', 4.75),
    ('The yield is <4.75%.', 4.75),
])
def test_approximate_or_bounded_percentages_are_not_exact_points(quote, value):
    assert not gc._number_in_quote(value, '%', quote)


def test_qualifier_on_another_observation_does_not_disqualify_an_exact_forecast():
    quote = 'Earlier estimates were above 4.75%, but the current forecast is 5.4%.'
    assert not gc._number_in_quote(4.75, '%', quote)
    assert gc._number_in_quote(5.4, '%', quote)
    assert gc._number_in_quote(5.4, '%', 'The bank forecasts a deficit of 5.4%.')


@pytest.mark.parametrize('gate', ['compatible_measurements', 'estimation_status_correct'])
def test_pixel_review_requires_explicit_comparison_and_estimation_status_gates(gate):
    reviewed = verdict(compatible_measurements=True, estimation_status_correct=True)
    reviewed.pop(gate)
    assert not gc.review_passed(reviewed, PLAN, 'Goldman Sachs')


def test_legacy_seven_true_image_gates_are_insufficient():
    reviewed = verdict()
    reviewed.pop('compatible_measurements')
    reviewed.pop('estimation_status_correct')
    assert not gc.review_passed(reviewed, PLAN, 'Goldman Sachs')


@pytest.mark.parametrize('plan', [
    {'kind': 'bar', 'title': 'IPP valuation multiples', 'metric': 'Target EV/EBITDA',
     'unit': 'x', 'axis': [0, 25], 'ticks': [0, 5, 10, 15, 20, 25],
     'bars': [{'id': 'ceg', 'label': 'CEG (P/E basis)', 'value': 21},
              {'id': 'vst', 'label': 'VST (EV/EBITDA basis)', 'value': 10}],
     'note': 'The different denominators are identified in the labels.'},
    {'kind': 'bar', 'title': 'China forecasts', 'metric': 'Year-over-year growth',
     'unit': '%', 'axis': [0, 30], 'ticks': [0, 10, 20, 30],
     'bars': [{'id': 'exports', 'label': 'Exports', 'value': 24.8},
              {'id': 'cpi', 'label': 'CPI inflation', 'value': 1}],
     'note': 'The different metrics are distinguished by color and explanatory copy.'},
])
def test_same_unit_labels_notes_and_colors_do_not_make_measurements_compatible(plan):
    reviewed = verdict(visible_data=plan['bars'], compatible_measurements=False,
                       estimation_status_correct=True)
    assert gc.valid_plan(plan)
    assert not gc.review_passed(reviewed, plan, 'Goldman Sachs')


def test_source_values_still_fail_if_image_misstates_estimation_status():
    reviewed = verdict(compatible_measurements=True, estimation_status_correct=False)
    assert not gc.review_passed(reviewed, PLAN, 'Goldman Sachs')


@pytest.mark.parametrize('value', [76, 88])
def test_range_endpoints_cannot_be_republished_as_exact_points(value):
    quote = 'Spread share was 76-88% during the roll.'
    assert not gc._number_in_quote(value, '%', quote)
    assert gc._number_in_quote(value, '%', quote, allow_range=True)


def test_explicit_closed_ranges_remain_source_exact_range_marks():
    plan = {'kind': 'range', 'title': 'Reported yield ranges', 'unit': '%',
            'axis': [0, 6], 'ticks': [0, 2, 4, 6],
            'marks': [{'id': 'a', 'label': 'A', 'low': 3, 'high': 4, 'estimated': False},
                      {'id': 'b', 'label': 'B', 'low': 4, 'high': 5, 'estimated': False}]}
    quotes = ['A yields ranged from 3-4%.', 'B yields ranged from 4-5%.']
    evidence = [{'id': row['id'], 'page': 1, 'source_quote': quote}
                for row, quote in zip(plan['marks'], quotes)]
    assert gc.valid_plan(plan)
    assert gc.valid_evidence(plan, evidence, {1: ' '.join(quotes)})


def test_currency_unit_requires_currency_in_each_source_quote():
    assert gc._number_in_quote(1, 'USD billion', 'Revenue was $1bn.')
    assert not gc._number_in_quote(1, 'USD billion', 'Revenue was 1bn.')
    assert not gc._number_in_quote(1, 'USD billion', 'Revenue was €1bn.')


@pytest.mark.parametrize(('unit', 'quote'), [
    ('USD billion', 'Revenue was USD 1 billion.'),
    ('USD billion', 'Revenue (billion): USD 1.'),
    ('$bn', 'Revenue was $1bn.'),
    ('EUR million', 'Revenue was €1mn.'),
    ('£tn', 'Revenue was £1tn.'),
])
def test_explicit_currency_occurrences_and_unit_headers_remain_supported(unit, quote):
    assert gc._number_in_quote(1, unit, quote)


@pytest.mark.parametrize(('unit', 'quote'), [
    ('$bn', 'Revenue was 1bn.'),
    ('$bn', 'Revenue was €1bn.'),
    ('USD billion', 'Expenses were $2bn; revenue was 1bn.'),
    ('USD billion', 'Expenses were $2bn; revenue was €1bn.'),
])
def test_currency_cannot_be_borrowed_from_another_numeric_occurrence(unit, quote):
    assert not gc._number_in_quote(1, unit, quote)


def test_range_endpoints_must_be_linked_as_one_source_measurement():
    quote = ('Asia yields were 3%; Europe yields were 4%; '
             'Africa yields were 2%; North America yields were 5%.')
    plan = {'kind': 'range', 'title': 'Reported yields by region', 'metric': 'Yield', 'unit': '%',
            'axis': [0, 6], 'ticks': [0, 2, 4, 6],
            'marks': [{'id': 'asia', 'label': 'Asia yields', 'low': 3, 'high': 4, 'estimated': False},
                      {'id': 'africa', 'label': 'Africa yields', 'low': 2, 'high': 5, 'estimated': False}]}
    evidence = [{'id': row['id'], 'page': 1, 'source_quote': quote} for row in plan['marks']]
    assert gc.valid_plan(plan)
    assert not gc.valid_evidence(plan, evidence, {1: quote})


@pytest.mark.parametrize('quote', [
    'Yield was 3-4%.',
    'Yield was 3%-4%.',
    'Yield was 3 to 4 percent.',
    'Yield (%) was 3-4.',
])
def test_one_explicit_closed_range_and_unit_header_remain_supported(quote):
    assert gc._range_in_quote(3, 4, '%', quote)


@pytest.mark.parametrize(('low', 'high', 'quote'), [
    (3, 3, 'Yield was 3-4%.'),
    (3, 5, 'Yield was 3-4%; a separate yield was 5%.'),
    (3, 4, 'Yield was 3-5%; a separate yield was 4%.'),
    (3, 4, 'Spread was 3-4bp; separate yields were 3% and 4%.'),
    (3, 4, 'Yield was about 3-4%.'),
    (3, 4, 'Yield was above 3-4%.'),
])
def test_closed_range_pair_cannot_borrow_values_or_units(low, high, quote):
    assert not gc._range_in_quote(low, high, '%', quote)


def test_explicit_degenerate_closed_range_remains_exact():
    assert gc._range_in_quote(3, 3, '%', 'Yield was 3-3%.')


def test_closed_monetary_range_requires_matching_currency_on_the_range():
    quote = 'Revenue was €3-4bn; expenses were $3bn and $4bn.'
    assert gc._range_in_quote(3, 4, 'EUR billion', quote)
    assert not gc._range_in_quote(3, 4, 'USD billion', quote)



def test_wider_than_spread_bound_is_not_an_exact_observed_spread():
    quote = 'At wider than 150bps. An earlier closing spread was 130bp.'
    assert not gc._number_in_quote(150, 'bp', quote)
    assert gc._number_in_quote(130, 'bp', quote)


@pytest.mark.parametrize('quote', [
    'The spread surpassed 130bp.',
    'The spread surpassed the 130bp mark.',
    'The spread passed 130bp.',
    'The spread has passed the 130bp mark.',
    'The spread exceeded the 130bp mark.',
])
def test_crossing_a_spread_mark_does_not_report_an_exact_observation(quote):
    assert not gc._number_in_quote(130, 'bp', quote)


@pytest.mark.parametrize('qualifier', ['or more', 'or less'])
def test_suffixed_percentage_bounds_preserve_a_separate_exact_observation(qualifier):
    quote = f'The deficit may be 5.4% {qualifier}. The earlier reported deficit was 5.0%.'
    assert not gc._number_in_quote(5.4, '%', quote)
    assert gc._number_in_quote(5.0, '%', quote)


def test_plan_instruction_keeps_notes_brief_and_credit_free():
    assert 'note? (one short clause, at most 100 characters' in gc.PLAN_INSTRUCTION
    assert 'no source credit' in gc.PLAN_INSTRUCTION

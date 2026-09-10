"""Extraction formatting and range tokenization must not invent numeric conflicts."""
import pytest
from research.pipeline import date_evidence_passed, numeric_evidence_passed, required_numeric_quotes


def candidate(content):
    return {'title': 'Research finding', 'content': content, 'figures': [], 'pages': [1]}


def checks(item, source):
    return {'numeric_checks': [{'proposal_quote': quote, 'source_quote': source,
                              'page': 1, 'supported': True}
                             for quote in required_numeric_quotes(item)]}


@pytest.mark.parametrize('text', ['ECB rates 2-3%', 'Rates 2–3%', 'Window 2011-2026'])
def test_required_range_quotes_preserve_positive_endpoint(text):
    item = candidate(text)
    assert all(not quote.startswith('-') for quote in required_numeric_quotes(item))
    assert numeric_evidence_passed(item, checks(item, text), {1: text})


@pytest.mark.parametrize('marker', ['**', '__'])
def test_visible_quote_matches_extracted_strong_emphasis(marker):
    item = candidate('Gross exposure rose +1.3z in the past week')
    quote = item['content']
    source = f'Gross exposure rose {marker}+1.3z{marker} in the past week'
    assert numeric_evidence_passed(item, checks(item, quote), {1: source})


def test_date_quote_matches_extracted_strong_emphasis():
    item = {'document_date': '2026-09-08', 'pages': [1]}
    result = {'date_evidence': {'page': 1, 'date_text': '8 September 2026',
              'source_quote': 'Report date: 8 September 2026',
              'role': 'report', 'role_verified': True}}
    assert date_evidence_passed(item, result, {1: 'Report date: **8 September 2026**'})


@pytest.mark.parametrize(('proposal', 'quote', 'source'), [
    ('3%', '3%', '**-3%**'), ('3%', '3%', '**13%**'),
    ('3%', '3%', '**.3%**'), ('3%', '3%', '**$3%**'),
    ('3%', '3%', '**3bps**'), ('3%', '3%', '**+3%**'),
    ('2 3%', '2 3%', '2 * 3%'), ('23%', '23%', '2**3%**'),
    ('3%', '3%', '3**%'), ('3%', '3%', r'\**3\**%'),
])
def test_formatting_cannot_erase_sign_unit_numeric_boundary_or_operator(proposal, quote, source):
    item = candidate(proposal)
    assert not numeric_evidence_passed(item, checks(item, quote), {1: source})


def test_negative_range_endpoint_keeps_its_sign():
    item = candidate('Rates -2--3%')
    assert required_numeric_quotes(item) == ['-2', '-3%']
    assert numeric_evidence_passed(item, checks(item, item['content']), {1: item['content']})


@pytest.mark.parametrize('source', [
    'Copyright © **8 September 2026**',
    'Period 1-**8 September 2026**',
    'Report date: **8 September 2026** to 9 September 2026',
])
def test_date_formatting_preserves_disqualifying_context(source):
    item = {'document_date': '2026-09-08', 'pages': [1]}
    result = {'date_evidence': {'page': 1, 'date_text': '8 September 2026',
              'source_quote': '8 September 2026', 'role': 'report', 'role_verified': True}}
    assert not date_evidence_passed(item, result, {1: source})


@pytest.mark.parametrize('date_page_text, expected_posts', [
    ('Report date: **8 September 2026**', 1),
    ('Copyright © **8 September 2026**', 0),
    ('Report date: **7 September 2026**', 0),
])
def test_selected_date_page_reaches_independent_verifier(tmp_path, date_page_text, expected_posts):
    """A selector's explicit date citation supplies context, never an approval."""
    import json
    from types import SimpleNamespace
    from research.pipeline import Pipeline
    evidence = {'page': 1, 'date_text': '8 September 2026',
                'source_quote': 'Report date: 8 September 2026', 'role': 'report'}
    item = candidate('Gross exposure rose +1.3z in the past week') | {
        'publisher': 'Research Bank', 'claim_key': 'gross-exposure',
        'document_date': '2026-09-08', 'date_evidence': evidence,
        'pages': [2], 'tags': ['POSITIONING'], 'text_only': True}
    result = dict.fromkeys(('supported', 'material_new_evidence', 'dates_verified',
                           'charts_complete', 'not_market_ear', 'no_unresolved_conflicts'), True)
    result.update(reason='Independent verification', date_evidence=evidence | {'role_verified': True},
                  numeric_checks=[{'page': 2, 'proposal_quote': '+1.3z',
                                   'source_quote': item['content'], 'supported': True}])
    calls = []
    responses = [{'candidates': [item]}, result]
    def ask(prompt, images):
        calls.append((prompt, images))
        return responses.pop(0)
    def extract(pdf, out):
        (out / '1.md').write_text(date_page_text)
        (out / '2.md').write_text('Gross exposure rose **+1.3z** in the past week')
        return {'page_count': 2, 'source_sha256': 'a' * 64,
                'pages': [{'page_number': p, 'markdown_file': f'{p}.md'} for p in (1, 2)]}
    def render(pdf, out, pages, **kwargs):
        out.mkdir(parents=True)
        return [{'page_number': p, 'image_file': f'{p}.png', 'width': 800, 'height': 1100} for p in pages]
    work = {'key': 'case', 'folder_date': '2026-09-08',
            'metadata': {'name': 'report.pdf', 'id': 'id:one', 'rev': 'r1', 'content_hash': 'b' * 64}}
    pipe = Pipeline(tmp_path, SimpleNamespace(model='test', ask=ask),
                    SimpleNamespace(store_asset=lambda _: '/source.pdf'), render, extract)
    posts = pipe.process(work, tmp_path / 'report.pdf', [])
    assert json.dumps(date_page_text) in calls[-1][0]
    assert 'Original PDF page 1' in [name for name, _ in calls[-1][1]]
    assert len(posts) == expected_posts
    if posts:
        assert posts[0]['source']['pages'] == [2, 1]
    audit = json.loads((tmp_path / 'evidence/case/review.json').read_text())['audit'][-1]
    assert audit['date_evidence_passed'] == bool(expected_posts)


@pytest.mark.parametrize(('proposal', 'proposal_quote', 'source', 'source_quote'), [
    ('Rates 2-3%', '-3%', 'Prior 2, current -3%', 'Prior 2, current -3%'),
    ('Rates -3%', '-3%', 'Rates 2-3%', '-3%'),
])
def test_quote_cannot_change_range_separator_into_negative_value(proposal, proposal_quote, source, source_quote):
    result = {'numeric_checks': [{'proposal_quote': proposal_quote, 'page': 1,
                                 'source_quote': source_quote, 'supported': True}]}
    if '2-3%' in proposal:
        result['numeric_checks'].append({'proposal_quote': '2', 'page': 1,
                                         'source_quote': source, 'supported': True})
    assert not numeric_evidence_passed(candidate(proposal), result, {1: source})

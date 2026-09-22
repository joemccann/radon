"""ZH / intermediary market recaps must select, not empty-hold."""
import json
from pathlib import Path
from types import SimpleNamespace

from research.pipeline import (
    DATE_EVIDENCE_ROLES,
    SELECT_SCHEMA,
    VERIFY_INSTRUCTION,
    date_evidence_passed,
    Pipeline,
    validate_candidate,
)

POLICY = Path(__file__).resolve().parents[2] / 'scripts' / 'research' / 'policy.md'

ZH_SOURCE = (
    'Tyler Durden September 11, 2026. Zero Hedge wrap of desk notes. '
    'Goldman Sachs rates desk printed 18k steepener contracts. '
    'Separately Citadel on crude inventories and JPM on yen vol.'
)

ZH_CANDIDATE = {
    'title': 'Goldman desk printed 18k steepener contracts',
    'content': 'Goldman Sachs rates desk printed 18k steepener contracts in the session.',
    'publisher': 'Goldman Sachs',
    'claim_key': 'gs-steepener-demand',
    'document_date': '2026-09-11',
    'date_evidence': {
        'page': 1,
        'source_quote': 'Tyler Durden September 11, 2026',
        'date_text': 'September 11, 2026',
        'role': 'byline',
    },
    'pages': [1],
    'tags': ['RATES'],
    'text_only': True,
    'figures': [],
}


def _contracts():
    return POLICY.read_text(), SELECT_SCHEMA, VERIFY_INSTRUCTION


def test_policy_and_prompts_select_zh_intermediary_market_recaps():
    policy, select, verify = _contracts()
    for text in (policy, select):
        lowered = text.lower()
        assert 'zero hedge' in lowered or 'tyler durden' in lowered
        assert 'do not return empty candidates solely' in lowered or 'do not empty-hold' in lowered
        assert 'original bank pdf' in lowered or 'missing original' in lowered
        assert 'best allowable attribution' in lowered
        assert 'byline' in lowered
        assert 'Never mention ZeroHedge' in text
        assert 'hold those candidates' not in text
        assert 'hold the candidate instead' not in text
        assert 'Do not produce an omnibus report recap' not in text
        assert 'Do not produce omnibus report recaps' not in text
        assert 'political' in lowered
        assert 'measured finding' in lowered
    assert 'role:report|publication|byline' in select
    assert 'role:report|publication|byline' in verify
    assert 'solely because' in verify.lower()
    assert 'Reject omnibus or multi-thesis summaries that combine independent instruments' not in verify
    assert 'byline' in DATE_EVIDENCE_ROLES


def test_aggregator_byline_date_is_valid_publication_evidence():
    item = {'document_date': '2026-09-11', 'pages': [1]}
    result = {
        'date_evidence': {
            'page': 1,
            'date_text': 'September 11, 2026',
            'source_quote': 'Tyler Durden September 11, 2026',
            'role': 'byline',
            'role_verified': True,
        }
    }
    assert date_evidence_passed(item, result, {1: ZH_SOURCE})


def test_zh_goldman_desk_recap_validates_without_original_bank_pdf():
    assert validate_candidate(dict(ZH_CANDIDATE), 1, '2026-09-11')['publisher'] == 'Goldman Sachs'


def test_zh_goldman_desk_recap_publishes_instead_of_provenance_hold(tmp_path):
    evidence = dict(ZH_CANDIDATE['date_evidence'], role_verified=True)
    checks = dict.fromkeys(
        ('supported', 'material_new_evidence', 'dates_verified',
         'charts_complete', 'not_market_ear', 'no_unresolved_conflicts'),
        True,
    )
    checks.update(
        reason='Measured Goldman desk print',
        date_evidence=evidence,
        numeric_checks=[{
            'page': 1,
            'proposal_quote': '18k',
            'source_quote': 'Goldman Sachs rates desk printed 18k steepener contracts',
            'supported': True,
        }],
    )
    responses = [{'candidates': [dict(ZH_CANDIDATE)], 'reason': 'ZH wrap with desk print'}, checks]

    def extract(pdf, out):
        (out / '1.md').write_text(ZH_SOURCE)
        return {'page_count': 1, 'source_sha256': 'a' * 64,
                'pages': [{'page_number': 1, 'markdown_file': '1.md'}]}

    def render(pdf, out, pages, **kwargs):
        out.mkdir(parents=True, exist_ok=True)
        return [{'page_number': p, 'image_file': f'{p}.png', 'width': 800, 'height': 1100} for p in pages]

    work = {'key': 'zh-wrap', 'folder_date': '2026-09-11',
            'metadata': {'name': 'zero-hedge-wrap.pdf', 'id': 'id:zh',
                         'rev': 'r1', 'content_hash': 'b' * 64}}
    pipe = Pipeline(tmp_path, SimpleNamespace(model='test', ask=lambda *a: responses.pop(0)),
                    SimpleNamespace(store_asset=lambda _: '/source.pdf'), render, extract)
    posts = pipe.process(work, tmp_path / 'wrap.pdf', [])
    assert posts, 'ZH Goldman-desk recap must produce candidates, not an empty provenance hold'
    assert posts[0]['source']['publisher'] == 'Goldman Sachs'
    audit = json.loads((tmp_path / 'evidence/zh-wrap/review.json').read_text())['audit']
    assert not any(row.get('held') in ('invalid candidate', 'intermediary', 'omnibus', 'provenance')
                   for row in audit)
    assert audit[-1]['date_evidence_passed'] is True


def test_zh_name_in_publisher_is_not_a_hold():
    item = dict(ZH_CANDIDATE)
    item['publisher'] = 'Goldman Sachs via ZERO HEDGE'
    assert validate_candidate(item, 1, '2026-09-11')['publisher'] == 'Goldman Sachs via ZERO HEDGE'

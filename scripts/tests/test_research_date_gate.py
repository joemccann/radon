"""Publication dates require whole-date evidence, never a folder or copyright year."""
import pytest
from research.pipeline import date_evidence_passed


def verdict(text='6 September 2026',quote=None,**changes):
    evidence={'page':1,'date_text':text,'source_quote':quote or 'Report date: '+text,'role':'report','role_verified':True}
    evidence.update(changes)
    return {'dates_verified':True,'date_evidence':evidence}


def item():
    return {'document_date':'2026-09-06','pages':[1,4]}


@pytest.mark.parametrize('text',['6 September 2026','September 6, 2026','06 Sep 2026','2026-09-06','September 6 2026','Sep 6 2026'])
def test_explicit_full_report_date_passes(text):
    checks=verdict(text)
    assert date_evidence_passed(item(),checks,{1:checks['date_evidence']['source_quote']})


def test_newsquawk_folder_date_and_copyright_cannot_override_missing_source_date():
    source={1:'Week In Focus: 7-11 September 2026',4:'Copyright © 2026 Newsquawk Voice Limited.'}
    assert not date_evidence_passed(item(),{'dates_verified':True},source)
    assert not date_evidence_passed(item(),verdict('6 September 2026',source[4],page=4),source)


@pytest.mark.parametrize('text',['2026','September 2026','09/06/2026','6 September','7-11 September 2026','2026-02-30'])
def test_partial_ambiguous_range_or_invalid_date_fails(text):
    checks=verdict(text)
    assert not date_evidence_passed(item(),checks,{1:checks['date_evidence']['source_quote']})


def test_wrong_fabricated_or_fragmented_dates_fail():
    assert not date_evidence_passed(item(),verdict(),{1:'Report date: 7 September 2026'})
    assert not date_evidence_passed(item(),verdict('7 September 2026'),{1:'Report date: 7 September 2026'})
    changed=item()|{'document_date':'2026-09-11'}
    assert not date_evidence_passed(changed,verdict('11 September 2026','Week In Focus: 7-11 September 2026'),{1:'Week In Focus: 7-11 September 2026'})


@pytest.mark.parametrize('changes',[{'role':'event'},{'role_verified':False},{'page':True},{'page':2},{'source_quote':''},{'date_text':None}])
def test_role_and_evidence_shape_are_required(changes):
    assert not date_evidence_passed(item(),verdict(**changes),{1:'Report date: 6 September 2026'})


def test_range_boundaries_and_copyright_label_cannot_be_hidden_by_short_quote():
    for original in ['Period 1-6 September 2026', 'Report interval 6 September 2026 to 8 September 2026',
                     'Copyright © 6 September 2026', '16 September 2026']:
        assert not date_evidence_passed(item(),verdict(quote='6 September 2026'),{1:original})


def test_pipeline_holds_newsquawk_even_when_model_approves_all_gates(tmp_path):
    import json
    from types import SimpleNamespace
    from research.pipeline import Pipeline
    candidate={'title':'Canada labour weakens','content':'Employment declined 41.7k.',
        'publisher':'Newsquawk','claim_key':'canada-employment','document_date':'2026-09-06',
        'pages':[1,4],'tags':['MACRO'],'text_only':True,'figures':[]}
    checks=dict.fromkeys(('supported','material_new_evidence','dates_verified','charts_complete','not_market_ear','no_unresolved_conflicts'),True)
    checks.update(reason='Copyright verifies folder date',numeric_checks=[{'proposal_quote':'41.7k','page':4,'source_quote':'Employment declined 41.7k.','supported':True}])
    responses=[{'candidates':[candidate]},checks]
    def extract(pdf,out):
        for page in range(1,5):
            (out/f'{page}.md').write_text('Week In Focus: 7-11 September 2026' if page==1 else 'Employment declined 41.7k. Copyright © 2026 Newsquawk Voice Limited.')
        return {'page_count':4,'source_sha256':'a'*64,'pages':[{'page_number':p,'markdown_file':f'{p}.md'} for p in range(1,5)]}
    def render(pdf,out,pages,**kwargs):
        out.mkdir(parents=True)
        return [{'page_number':p,'image_file':f'{p}.png','width':800,'height':1100} for p in pages]
    pipe=Pipeline(tmp_path,SimpleNamespace(model='test',ask=lambda *a:responses.pop(0)),SimpleNamespace(store_asset=lambda _:pytest.fail('Date not verified')),render,extract)
    work={'key':'case','folder_date':'2026-09-06','metadata':{'name':'preview.pdf','id':'id:one'}}
    assert pipe.process(work,tmp_path/'input.pdf',[])==[]
    audit=json.loads((tmp_path/'evidence/case/review.json').read_text())['audit'][-1]
    assert audit['numeric_evidence_passed'] is True and audit['date_evidence_passed'] is False


def test_unrelated_date_occurrence_cannot_rescue_quoted_coverage_period():
    source='Week In Focus: 7-11 September 2026. '+('Other text. '*20)+'Report date: 11 September 2026'
    assert not date_evidence_passed(item()|{'document_date':'2026-09-11'},
        verdict('11 September 2026','Week In Focus: 7-11 September 2026'),{1:source})


@pytest.mark.parametrize('endpoint',['September 11, 2026','Sep 11, 2026','Sep. 11, 2026'])
@pytest.mark.parametrize('separator',['–','—','-','to'])
def test_named_month_range_endpoint_cannot_be_hidden(endpoint,separator):
    text=f'September 6, 2026 {separator} {endpoint}'
    assert not date_evidence_passed(item(),verdict('September 6, 2026','September 6, 2026'),{1:text})

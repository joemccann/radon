"""Crop-only evidence must pass independently of a full-page claim review."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from research.pipeline import Pipeline, crop_inspection_passed


def inspection(index=0, **changes):
    return {"index":index,"complete":True,"chart_titles":["Euro inflation swap"],"axis_labels":["Jul-26","3.0"],
            "legend_labels":[],"source_labels":["Source: Deutsche Bank"],"missing_or_clipped":[],
            "unrelated_prose":False,"reason":"All labels visible"}|changes


@pytest.mark.parametrize("changes",[{"chart_titles":[]},{"axis_labels":[]},{"source_labels":[]},
    {"missing_or_clipped":["title clipped at top"]},{"unrelated_prose":True},{"complete":"true"}])
def test_generic_complete_boolean_cannot_override_visible_crop_failures(changes):
    assert crop_inspection_passed({"figures":[inspection(**changes)]},[0])==set()


def setup(tmp_path,responses):
    calls=[];renders=[]
    reviewer=SimpleNamespace(ask=lambda prompt,images:calls.append((prompt,images)) or responses.pop(0))
    def render(pdf,out,pages,dpi,crop=None):
        renders.append(crop);out.mkdir(parents=True,exist_ok=True);(out/"chart.png").write_bytes(b"chart")
        return [{"image_file":"chart.png","width":1200,"height":500,"page_number":pages[0]}]
    pipe=Pipeline(tmp_path,reviewer,None,renderer=render,anchor_reader=lambda *a:[])
    candidate={"title":"ECB", "content":"Rates", "publisher":"DB", "claim_key":"ecb", "document_date":"2026-09-07",
               "pages":[4],"tags":["RATES"],"figures":[{"page":4,"crop":[.03,.42,.97,.82],"caption":"ECB chart"}]}
    original=tmp_path/"original.png";original.write_bytes(b"original")
    return pipe,candidate,{4:original},{4:(800,1100)},calls,renders


def test_clipped_title_corrects_once_and_rechecks_only_final_crop(tmp_path):
    responses=[{"figures":[inspection(complete=False,missing_or_clipped=["title"],unrelated_prose=True)]},
               {"corrections":[{"index":0,"crop":[.03,.34,.97,.64]}]}, {"figures":[inspection()]}]
    pipe,candidate,pages,sizes,calls,renders=setup(tmp_path,responses);audit=[]
    figures=pipe.prepare_figures(tmp_path/"source.pdf",tmp_path/"charts",candidate,pages,sizes,audit)
    assert figures is not None and candidate["figures"][0]["crop"]==[.03,.34,.97,.64]
    assert len(calls)==3 and renders==[[.03,.42,.97,.82],[.03,.34,.97,.64]]
    assert all(path!=pages[4] for _,path in calls[0][1]+calls[2][1])
    assert calls[1][1][0][1]==pages[4]
    assert "800" in calls[1][0] and "1100" in calls[1][0]
    assert len(audit)==3


def test_failed_corrected_chart_is_rejected_without_unbounded_retry(tmp_path):
    failed={"figures":[inspection(complete=False,chart_titles=[])]}
    pipe,candidate,pages,sizes,calls,renders=setup(tmp_path,[failed,{"corrections":[{"index":0,"crop":[0,.2,1,.7]}]},failed])
    assert pipe.prepare_figures(tmp_path/"source.pdf",tmp_path/"charts",candidate,pages,sizes,[]) is None
    assert len(calls)==3 and len(renders)==2


def test_good_chart_needs_only_one_extra_inspection(tmp_path):
    pipe,candidate,pages,sizes,calls,renders=setup(tmp_path,[{"figures":[inspection()]}])
    assert pipe.prepare_figures(tmp_path/"source.pdf",tmp_path/"charts",candidate,pages,sizes,[]) is not None
    assert len(calls)==1


def test_invalid_correction_never_renders_out_of_bounds(tmp_path):
    pipe,candidate,pages,sizes,calls,renders=setup(tmp_path,[{"figures":[]},{"corrections":[{"index":0,"crop":[-1,0,1,1]}]}])
    assert pipe.prepare_figures(tmp_path/"source.pdf",tmp_path/"charts",candidate,pages,sizes,[]) is None
    assert len(renders)==1


@pytest.mark.parametrize("value", [None,{}, {"figures":None}, {"figures":[{}]}, {"figures":[inspection(index=True)]},
    {"figures":[inspection(index=2)]}, {"figures":[inspection(chart_titles=[None])]},
    {"figures":[inspection(reason="")]}, {"figures":[inspection(legend_labels=None)]}])
def test_crop_schema_fails_closed(value):
    assert crop_inspection_passed(value,[0])==set()


@pytest.mark.parametrize("correction", [{}, {"corrections":[{}]}, {"corrections":[{"index":1,"crop":[0,.2,1,.7]}]},
    {"corrections":[{"index":0,"crop":None}]}, {"corrections":[{"index":0,"crop":[0,0,1,1]}]}])
def test_correction_must_be_valid_bounded_chart_not_fullpage(tmp_path,correction):
    pipe,candidate,pages,sizes,calls,renders=setup(tmp_path,[{"figures":[]},correction])
    assert pipe.prepare_figures(tmp_path/"source.pdf",tmp_path/"charts",candidate,pages,sizes,[]) is None
    assert len(renders)==1


def test_text_only_has_no_chart_inspection(tmp_path):
    pipe,candidate,pages,sizes,calls,renders=setup(tmp_path,[])
    candidate["figures"]=[]
    assert pipe.prepare_figures(tmp_path/"source.pdf",tmp_path/"charts",candidate,pages,sizes,[])==[]
    assert not calls and not renders


def test_failed_native_anchor_process_is_safe_error(tmp_path,monkeypatch):
    from research.pipeline import EvidenceError
    monkeypatch.setattr("research.pipeline.subprocess.run",lambda *a,**k:SimpleNamespace(returncode=-11))
    with pytest.raises(EvidenceError,match="localization"):Pipeline(tmp_path,None,None).anchors_isolated(tmp_path/"source.pdf",[1])


def test_crop_failure_prevents_generic_claim_review_and_publication(tmp_path):
    failed={"figures":[inspection(complete=False,chart_titles=[])]}
    pipe,candidate,pages,sizes,calls,renders=setup(tmp_path,[{"candidates":[]},failed,{"corrections":[{"index":0,"crop":[0,.2,1,.7]}]},failed])
    # The selector is replaced by a known proposal to reproduce the previously
    # accepted ECB crop without letting a generic full-page boolean bypass it.
    response=[{"candidates":[candidate]},failed,{"corrections":[{"index":0,"crop":[0,.2,1,.7]}]},failed]
    pipe.reviewer=SimpleNamespace(model="test",ask=lambda prompt,images:calls.append((prompt,images)) or response.pop(0))
    pipe.publisher=SimpleNamespace(store_asset=lambda path:pytest.fail("rejected chart reached publication"))
    def extract(pdf,out):
        (out/"page.md").write_text("Source")
        return {"page_count":4,"source_sha256":"a"*64,"pages":[{"page_number":4,"markdown_file":"page.md"}]}
    pipe.extractor=extract
    # Supply all requested page numbers, unlike the simple single-crop fixture.
    original_render=pipe.render
    def render(pdf,out,numbers,**kwargs):
        if kwargs.get("crop") is None:
            out.mkdir(parents=True,exist_ok=True);(out/"original.png").write_bytes(b"original")
            return [{"page_number":n,"image_file":"original.png","width":800,"height":1100} for n in numbers]
        return original_render(pdf,out,numbers,**kwargs)
    pipe.render=render
    work={"key":"failed-crop","folder_date":"2026-09-07","metadata":{"id":"id:one","name":"source.pdf"}}
    assert pipe.process(work,tmp_path/"source.pdf",[])==[]
    assert len(calls)==4 and not any("COMPARISON FEED" in prompt for prompt,_ in calls)


def test_standalone_crop_review_rejects_insufficient_initial_budget(tmp_path):
    from research.pipeline import DocumentDeadlineExceeded, REVIEWER_CALL_TIMEOUT_SECS
    pipe,candidate,pages,sizes,calls,_=setup(tmp_path,[])
    pipe.clock=lambda:0
    pipe.document_budget_secs=REVIEWER_CALL_TIMEOUT_SECS-1
    with pytest.raises(DocumentDeadlineExceeded,match="exhausted"):
        pipe.prepare_figures(tmp_path/"source.pdf",tmp_path/"charts",candidate,pages,sizes,[])
    assert calls==[]


def test_standalone_crop_correction_does_not_renew_its_budget(tmp_path):
    from research.pipeline import DocumentDeadlineExceeded, REVIEWER_CALL_TIMEOUT_SECS
    pipe,candidate,pages,sizes,calls,_=setup(tmp_path,[])
    now=[0]
    pipe.clock=lambda:now[0]
    pipe.document_budget_secs=2*REVIEWER_CALL_TIMEOUT_SECS
    def review(prompt,images):
        calls.append(prompt)
        now[0]+=REVIEWER_CALL_TIMEOUT_SECS+1
        return {"figures":[inspection(complete=False)]}
    pipe.reviewer=SimpleNamespace(ask=review)
    with pytest.raises(DocumentDeadlineExceeded,match="exhausted"):
        pipe.prepare_figures(tmp_path/"source.pdf",tmp_path/"charts",candidate,pages,sizes,[])
    assert len(calls)==1

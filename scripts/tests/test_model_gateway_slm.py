"""SLM mode selection and comparison telemetry remain centralized."""
import pytest
from model_gateway.contracts import LadderResult, ModelLadderExhausted


def run(monkeypatch,mode,*,sample=False,fail_slm=False):
    from model_gateway import slm
    monkeypatch.setenv('RADON_SLM_TAGGER_MODE',mode)
    monkeypatch.setattr(slm,'should_sample_ladder',lambda *a:sample)
    calls=[]
    def complete(providers, **kwargs):
        calls.append(providers)
        if fail_slm and providers==['slm-tagger']: raise ModelLadderExhausted('unavailable')
        name='slm-tagger' if providers and providers[0]=='slm-tagger' else 'anthropic'
        return LadderResult({'tags':['A','B','C']},'source-tags',name,'model',(name+':ok',))
    result,row=slm.complete({'instruction':'facts','post_id':'post-123'},complete)
    return result,row,calls


def test_shadow_records_both_but_publishes_ladder(monkeypatch):
    result,row,calls=run(monkeypatch,'shadow')
    assert calls==[['slm-tagger'],None]
    assert result.provider=='anthropic'
    assert row['post_id']=='post-123' and row['tags_slm']==row['tags_ladder']==['A','B','C']
    assert row['exact3']==1 and row['jaccard']==1


def test_primary_keeps_exact_provider_policy(monkeypatch):
    result,row,calls=run(monkeypatch,'primary')
    assert calls==[['slm-tagger','nvidia','cerebras']]
    assert result.provider=='slm-tagger' and row['ladder_sampled']==0


def test_prefer_sample_keeps_separate_ladder_comparison(monkeypatch):
    result,row,calls=run(monkeypatch,'prefer',sample=True)
    assert calls[0][0]=='slm-tagger' and calls[1] is None
    assert result.provider=='slm-tagger' and row['ladder_sampled']==1


def test_shadow_failure_does_not_lose_verified_fallback(monkeypatch):
    result,row,calls=run(monkeypatch,'shadow',fail_slm=True)
    assert result.provider=='anthropic' and row['slm_status']=='unavailable'
    assert row['tags_slm'] is None

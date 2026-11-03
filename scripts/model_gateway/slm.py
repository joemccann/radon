"""Service-owned SLM routing; callers persist bounded comparison telemetry."""
from __future__ import annotations
import os
from newsfeed.slm.modes import normalise_mode, providers_for_mode
from newsfeed.slm.shadow import should_sample_ladder
from .contracts import ModelLadderExhausted


def _status(result):
    for attempt in result.attempted:
        if attempt.startswith('slm-tagger:'):
            return attempt.split(':',1)[1]
    return 'ok' if result.provider=='slm-tagger' else 'unavailable'


def complete(payload, invoke):
    """Return primary completion plus optional shadow row, never a DB side effect."""
    mode=normalise_mode(os.environ.get('RADON_SLM_TAGGER_MODE'))
    if mode=='off':
        return invoke(None),None
    slm=None; ladder=None; sampled=False; status='unavailable'
    if mode=='shadow':
        try:
            slm=invoke(['slm-tagger'],auxiliary=True); status=_status(slm)
        except ModelLadderExhausted:
            pass
        result=ladder=invoke(None)
    else:
        result=invoke(list(providers_for_mode(mode)))
        status=_status(result)
        slm=result if result.provider=='slm-tagger' else None
        ladder=None if slm else result
        if should_sample_ladder(payload.get('post_id'),payload['instruction']):
            sampled=True
            try:
                ladder=invoke(None,auxiliary=True)
            except ModelLadderExhausted:
                pass
    slm_tags=slm.data.get('tags') if slm and isinstance(slm.data,dict) and status=='ok' else None
    ladder_tags=ladder.data.get('tags') if ladder and isinstance(ladder.data,dict) else None
    exact=jaccard=None
    if slm_tags and ladder_tags:
        left,right=set(slm_tags),set(ladder_tags)
        exact=int(left==right and len(left)==3)
        jaccard=len(left&right)/len(left|right) if left|right else 0
    return result,{'post_id':payload.get('post_id') or payload['instruction'],'model_version':'1.0.0',
        'mode':mode,'tags_slm':slm_tags,'tags_slm_raw':slm.text if slm else None,
        'tags_ladder':ladder_tags,'ladder_provider':ladder.provider if ladder else None,
        'ladder_sampled':int(sampled),'slm_status':status,'exact3':exact,'jaccard':jaccard}

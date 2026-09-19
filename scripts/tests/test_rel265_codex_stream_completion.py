"""REL-265: partial, valid JSON is not a successfully completed review."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_model_ladder import _StreamingResponse, _openai_obj_ok
from clients import model_ladder as ml
from research.model import Reviewer, ModelError

@pytest.fixture
def codex_env(tmp_path):
    auth_home = tmp_path / 'codex'
    auth_home.mkdir()
    (auth_home / 'auth.json').write_text(json.dumps({'auth_mode': 'chatgpt', 'tokens': {'access_token': 'FAKE-codex-grant'}}))
    return {'HOME': str(tmp_path), 'CODEX_HOME': str(auth_home), 'RADON_LADDER_NO_AUTH_FILES': '0'}

DELTA = 'data: ' + json.dumps({'type': 'response.output_text.delta', 'delta': '{"ok":true}'}) + '\n\n'
COMPLETED = 'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
BAD_TERMINALS = [
    '',
    'data: {"type":"response.failed","error":{"message":"FAKE-sensitive"}}\n\n',
    'data: {"type":"error","message":"FAKE-sensitive"}\n\n',
    'data: {"type":"response.incomplete"}\n\n',
    'data: {"type":"response.completed"}\n\n',
    'data: {"type":"response.completed","response":[]}\n\n',
    'data: {"type":"response.completed","response":{"status":"failed"}}\n\n',
    'data: {"type":"response.completed",\n\n',
    COMPLETED + 'data: {"type":"response.failed"}\n\n',
]


@pytest.mark.parametrize('terminal', BAD_TERMINALS)
@pytest.mark.parametrize('public_path', ['chat', 'reviewer'])
def test_bad_terminal_never_accepts_partial_json(terminal, public_path, codex_env):
    responses = []
    def post(*args, **kwargs):
        response = _StreamingResponse({}, chunks=[(DELTA + terminal).encode()])
        responses.append(response)
        return response
    with pytest.raises((ml.ModelLadderExhausted, ModelError)) as error:
        if public_path == 'chat':
            ml.complete_text_json('review', env=codex_env, post=post)
        else:
            Reviewer(env=codex_env, session=SimpleNamespace(post=post)).ask('review')
    assert 'FAKE-sensitive' not in str(error.value)
    assert responses and all(response.closed for response in responses)


def test_completed_review_is_accepted_and_closed(codex_env):
    response = _StreamingResponse({}, chunks=[(DELTA + COMPLETED).encode()])
    result = Reviewer(env=codex_env, session=SimpleNamespace(post=lambda *a, **k: response)).ask('review')
    assert result == {'ok': True}
    assert response.closed


def test_failed_codex_stream_preserves_next_provider_fallback(codex_env):
    responses = []
    def post(url, **kwargs):
        if 'chatgpt.com' not in url:
            return _openai_obj_ok({'fallback': True})
        response = _StreamingResponse({}, chunks=[DELTA.encode()])
        responses.append(response)
        return response
    result = ml.complete_text_json('review', env={**codex_env, 'NVIDIA_API_KEY': 'FAKE-nvidia'}, post=post)
    assert result.provider == 'nvidia'
    assert result.data == {'fallback': True}
    assert responses and all(response.closed for response in responses)

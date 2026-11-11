"""Research media uses the real API auth perimeter and private response headers."""
import hashlib
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def client(monkeypatch, tmp_path):
    from scripts.api import server
    monkeypatch.setenv("RADON_RESEARCH_DIR", str(tmp_path))
    monkeypatch.setattr(server, "is_trusted_local_request", lambda req: True)
    return TestClient(server.app)


def test_private_asset_headers_and_bytes(client, tmp_path):
    data = b"\x89PNG\r\n\x1a\n chart"
    name = hashlib.sha256(data).hexdigest() + ".png"
    (tmp_path / "assets").mkdir(); (tmp_path / "assets" / name).write_bytes(data)
    response = client.get("/newsfeed/research/files/" + name)
    assert response.status_code == 200
    assert response.content == data
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-type"] == "image/png"


def test_untrusted_request_requires_auth(client, monkeypatch):
    from scripts.api import server
    monkeypatch.setattr(server, "is_trusted_local_request", lambda req: False)
    monkeypatch.setattr(server, "verify_api_key", lambda req: None)
    monkeypatch.delenv("CLERK_JWKS_URL", raising=False)
    monkeypatch.delenv("RADON_AUTH_DISABLED", raising=False)
    response = client.get("/newsfeed/research/files/" + "a" * 64 + ".png")
    assert response.status_code == 503


@pytest.mark.parametrize("name", ["a" * 64 + ".png", "bad.png", "%2e%2e%2fsecret", "a" * 64 + ".svg"])
def test_missing_invalid_and_traversal_return_no_bytes(client, name):
    assert client.get("/newsfeed/research/files/" + name).status_code == 404


def test_configured_auth_rejects_missing_bearer(client, monkeypatch):
    from scripts.api import server, auth
    monkeypatch.setattr(server, "is_trusted_local_request", lambda req: False)
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda req: False)
    monkeypatch.setattr(server, "verify_api_key", lambda req: None)
    monkeypatch.setenv("CLERK_JWKS_URL", "https://clerk.example/.well-known/jwks.json")
    assert client.get("/newsfeed/research/files/" + "a" * 64 + ".png").status_code == 401


def test_authenticated_manifest_and_literal_retrieval(client, tmp_path):
    import json
    from research.assets import store_asset
    import hashlib
    # One served manifest page; the retired PDF pipeline was its only writer.
    digest, text = 'a'*64, 'Capex guidance -12.5%.'
    passage = {'id': hashlib.sha256(f'{digest}:1:1:1:{text}'.encode()).hexdigest(), 'kind': 'passage',
               'line_start': 1, 'line_end': 1, 'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest()}
    value = {'schema_version': 1, 'source_sha256': digest, 'source_url': '/api/newsfeed/research/files/' + digest + '.pdf',
             'trust': 'Untrusted extracted source data.', 'pages': [{'page_number': 1, 'needs_ocr': False, 'passages': [passage]}]}
    path = tmp_path/'manifest.json';path.write_text(json.dumps(value))
    asset = store_asset(path).rsplit('/',1)[-1]
    full = client.get('/newsfeed/research/evidence/'+asset)
    assert full.status_code == 200 and full.json() == value
    assert full.headers['cache-control'] == 'private, no-store'
    found = client.get('/newsfeed/research/evidence/'+asset,params={'query':'capex'})
    assert found.json()['results'][0]['text'] == 'Capex guidance -12.5%.'
    assert found.json()['results'][0]['source_sha256'] == 'a'*64
    assert client.get('/newsfeed/research/evidence/'+asset,params={'query':'unknown'}).json()['results'] == []
    assert client.get('/newsfeed/research/evidence/'+asset,params={'query':'!?'}).status_code == 400
    assert client.get('/newsfeed/research/evidence/'+asset,params={'query':'x'*201}).status_code == 422
    binary = client.get('/newsfeed/research/files/'+asset)
    assert binary.headers['content-type'] == 'application/json'


def test_manifest_missing_bearer_fails_closed(client, monkeypatch):
    from scripts.api import server, auth
    monkeypatch.setattr(server, 'is_trusted_local_request', lambda req:False)
    monkeypatch.setattr(auth, 'is_trusted_local_request', lambda req:False)
    monkeypatch.setattr(server, 'verify_api_key', lambda req:None)
    monkeypatch.setenv('CLERK_JWKS_URL','https://clerk.example/.well-known/jwks.json')
    assert client.get('/newsfeed/research/evidence/'+'a'*64+'.json').status_code == 401

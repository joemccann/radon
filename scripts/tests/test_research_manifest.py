"""Original-source manifest adversarial tests, independent of native PDF wheels."""
import copy
import hashlib
import json

import pytest

from research.manifest import search_manifest, validate_manifest
from research.assets import read_asset, store_asset
from research_manifest_fixtures import build_manifest


def manifest(_tmp_path=None):
    return build_manifest('a' * 64, [
        ('Guidance fell -12.5%.\n\n| Metric | USD |\n| --- | ---: |\n| Capex | 20 |\n', False),
        ('Guidance rose 99%.', True),
    ])


def test_exact_signed_passages_tables_and_original_pages(tmp_path):
    result = manifest(tmp_path)
    assert validate_manifest(result) == result
    assert result['pages'][0]['passages'][0]['text'] == 'Guidance fell -12.5%.'
    table = search_manifest(result, 'CAPEX 20')[0]
    assert table['kind'] == 'table' and table['page_number'] == 1
    assert table['line_start'] == 3 and table['line_end'] == 5
    assert table['text'].endswith('| Capex | 20 |')
    assert 'source_path' not in json.dumps(result)
    assert manifest(tmp_path) == result
    assert search_manifest(result, 'guidance 99') == []  # OCR is not evidence
    assert search_manifest(result, 'unknown') == []


@pytest.mark.parametrize('query', ['', ' ', 'a' * 201, '!?'])
def test_invalid_queries_are_rejected(tmp_path, query):
    with pytest.raises(ValueError): search_manifest(manifest(tmp_path), query)


@pytest.mark.parametrize('limit', [0, 51, True, 1.2])
def test_retrieval_bounds(tmp_path, limit):
    with pytest.raises(ValueError): search_manifest(manifest(tmp_path), 'guidance', limit)


def test_source_page_and_passage_changes_invalidate_ids(tmp_path):
    result = manifest(tmp_path)
    modified = copy.deepcopy(result)
    modified['pages'][0]['passages'][0]['text'] = 'Guidance rose 12.5%.'
    with pytest.raises(ValueError, match='digest'): validate_manifest(modified)
    modified = copy.deepcopy(result)
    modified['source_sha256'] = 'b' * 64
    with pytest.raises(ValueError, match='source URL'): validate_manifest(modified)
    # Keep the URL consistent so the next assertion reaches source-bound IDs.
    modified['source_url'] = '/api/newsfeed/research/files/' + modified['source_sha256'] + '.pdf'
    with pytest.raises(ValueError, match='digest'): validate_manifest(modified)


def test_manifest_published_content_addressed_and_tampering_refused(tmp_path, monkeypatch):
    monkeypatch.setenv('RADON_RESEARCH_DIR', str(tmp_path/'private'))
    value = manifest(tmp_path)
    path = tmp_path/'manifest.json'
    path.write_text(json.dumps(value))
    url = store_asset(path)
    name = url.rsplit('/', 1)[-1]
    assert name == hashlib.sha256(path.read_bytes()).hexdigest() + '.json'
    assert json.loads(read_asset(name)) == value
    (tmp_path/'private'/'assets'/name).write_bytes(b'{}')
    with pytest.raises(ValueError): read_asset(name)

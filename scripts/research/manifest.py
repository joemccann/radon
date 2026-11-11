"""Agent-readable original-page passages and Markdown tables, without model rewriting.

IDs bind source PDF, original page, line range and exact extracted text. This is
extraction provenance, not a claim that extraction/OCR or a financial fact is correct.
"""
from __future__ import annotations

import hashlib
import re

MAX_TEXT_CHARS = 4_000_000


def search_manifest(manifest: dict, query: str, limit: int = 20) -> list[dict]:
    """Bounded literal-token retrieval. OCR-required pages never substantiate hits."""
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
        raise ValueError('Query must contain 1..200 characters')
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('Limit must be 1..50')
    tokens = set(re.findall(r'\w+', query.casefold()))
    if not tokens:
        raise ValueError('Query must contain words or numbers')
    results = []
    for page in manifest['pages']:
        if page['needs_ocr']:
            continue
        for passage in page['passages']:
            words = set(re.findall(r'\w+', passage['text'].casefold()))
            if tokens <= words:
                results.append({'source_sha256': manifest['source_sha256'],
                    'page_number': page['page_number'], **passage})
                if len(results) == limit:
                    return results
    return results


def validate_manifest(value: object) -> dict:
    """Validate served manifests, including passage hashes, before retrieval."""
    if not isinstance(value, dict) or type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        raise ValueError('Invalid manifest version')
    digest = value.get('source_sha256')
    if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('Invalid manifest source hash')
    if value.get('source_url') != '/api/newsfeed/research/files/' + digest + '.pdf':
        raise ValueError('Invalid manifest source URL')
    pages = value.get('pages')
    if not isinstance(pages, list) or not 1 <= len(pages) <= 100 or not isinstance(value.get('trust'), str):
        raise ValueError('Invalid manifest pages')
    chars = 0
    for number, page in enumerate(pages, 1):
        if not isinstance(page, dict) or type(page.get('page_number')) is not int or page['page_number'] != number or type(page.get('needs_ocr')) is not bool:
            raise ValueError('Invalid manifest page')
        if not isinstance(page.get('passages'), list):
            raise ValueError('Invalid manifest passages')
        last_end = 0
        for passage in page['passages']:
            if not isinstance(passage, dict) or passage.get('kind') not in ('passage', 'table') or not isinstance(passage.get('text'), str):
                raise ValueError('Invalid manifest passage')
            start, end, text = passage.get('line_start'), passage.get('line_end'), passage['text']
            if type(start) is not int or type(end) is not int or not last_end < start <= end or len(text.splitlines()) != end - start + 1:
                raise ValueError('Invalid manifest line range')
            identity = f'{digest}:{number}:{start}:{end}:{text}'
            if passage.get('id') != hashlib.sha256(identity.encode()).hexdigest() or passage.get('text_sha256') != hashlib.sha256(text.encode()).hexdigest():
                raise ValueError('Manifest passage digest mismatch')
            last_end = end
            chars += len(text)
            if chars > MAX_TEXT_CHARS:
                raise ValueError('Manifest exceeds text budget')
    return value

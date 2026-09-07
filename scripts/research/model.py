"""Bounded, tool-free multimodal review using the existing Anthropic provider."""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path
import requests


class ModelError(RuntimeError):
    pass


class Reviewer:
    def __init__(self, api_key=None, model=None, session=None):
        self.api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
        self.model = model or os.environ.get('RADON_RESEARCH_MODEL', 'claude-sonnet-4-6')
        self.session = session or requests.Session()
        if not self.api_key:
            raise ModelError('ANTHROPIC_API_KEY is required for research evidence review')

    def ask(self, instruction, images=()):
        content = []
        for label, path in images:
            raw = Path(path).read_bytes()
            if len(raw) > 5_000_000:
                raise ModelError('Source image exceeds review byte limit')
            content.extend([{'type': 'text', 'text': label}, {'type': 'image', 'source': {
                'type': 'base64', 'media_type': 'image/png',
                'data': base64.b64encode(raw).decode()}}])
        content.append({'type': 'text', 'text': instruction})
        payload = {'model': self.model, 'max_tokens': 6000,
                   'system': Path(__file__).with_name('policy.md').read_text(),
                   'messages': [{'role': 'user', 'content': content}]}
        try:
            response = self.session.post('https://api.anthropic.com/v1/messages',
                headers={'x-api-key': self.api_key, 'anthropic-version': '2023-06-01'},
                json=payload, timeout=(10, 120), stream=True)
            try:
                if response.status_code != 200:
                    raise ModelError(f'Research reviewer HTTP {response.status_code}')
                raw = bytearray()
                for chunk in response.iter_content(65536):
                    raw.extend(chunk)
                    if len(raw) > 2_000_000:
                        raise ModelError('Research reviewer response exceeds limit')
                result = json.loads(raw)
            finally:
                response.close()
            if not isinstance(result, dict) or not isinstance(result.get('content'), list) or any(not isinstance(x, dict) for x in result['content']):
                raise ValueError('Invalid response envelope')
            if result.get('stop_reason') != 'end_turn':
                raise ModelError('Research reviewer did not finish a complete response')
            text = ''.join(x['text'] for x in result.get('content', []) if x.get('type') == 'text').strip()
            if text.startswith('```json') and text.endswith('```'):
                text = text[7:-3].strip()
            value = json.loads(text)
            if not isinstance(value, dict):
                raise ValueError('object required')
            return value
        except ModelError:
            raise
        except (requests.RequestException, ValueError, KeyError, TypeError):
            raise ModelError('Research reviewer unavailable or malformed response') from None

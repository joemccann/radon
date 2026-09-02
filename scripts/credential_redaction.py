#!/usr/bin/env python3
"""Redact credential-shaped fragments from validator messages (REL-190)."""

from __future__ import annotations

import re
from typing import Any

_SECRET_SCRUB_PATTERNS = [
    (re.compile(r"libsql://[^\s'\"]+", re.IGNORECASE), "[redacted-db-url]"),
    (re.compile(r"https://[a-z0-9.-]+\.turso\.io[^\s'\"]*", re.IGNORECASE), "[redacted-db-url]"),
    (
        re.compile(r"(auth[_-]?token|authorization|bearer)(\s*[=:]\s*)\S+", re.IGNORECASE),
        r"\1\2[redacted]",
    ),
    (re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]*"), "[redacted-jwt]"),
    (re.compile(r"\bU\d{6,}\b"), "[redacted-account]"),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{6,}"), "[redacted-key]"),
    (re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{6,}\b"), "[redacted-key]"),
    (re.compile(r"([?&](?:t|token|api[_-]?key)=)[^\s&'\"]+", re.IGNORECASE), r"\1[redacted]"),
]


def scrub_credential_text(value: Any) -> Any:
    if isinstance(value, str):
        for pattern, repl in _SECRET_SCRUB_PATTERNS:
            value = pattern.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {k: scrub_credential_text(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_credential_text(v) for v in value]
    return value

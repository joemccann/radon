#!/usr/bin/env python3
"""Redact credential-shaped fragments from validator messages (REL-190)."""

from __future__ import annotations

import re
from typing import Any

# `_pass` / `_pwd` need the underscore so prose ("tests pass: 3") survives.
_SECRET_KEY = (
    r"(?:api[_-]?key|access[_-]?key|secret[_-]?key|access[_-]?token"
    r"|client[_-]?secret|password|passwd|secret|token|_pass|_pwd"
    r"|sess(?:ion)?[-_]?id|_session|csrf|xsrf)"
)

_SPECIFIC_SCRUB_PATTERNS = [
    # PEM / OpenSSH / PGP private-key blocks span lines; a block cut off
    # before its END line is redacted to the end of the text.
    (
        re.compile(
            r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"
            r"(?:.*?-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----|.*)",
            re.S,
        ),
        "[redacted-private-key]",
    ),
    (re.compile(r"libsql://[^\s'\"]+", re.IGNORECASE), "[redacted-db-url]"),
    (re.compile(r"https://[a-z0-9.-]+\.turso\.io[^\s'\"]*", re.IGNORECASE), "[redacted-db-url]"),
    # "Bearer <token>" is the literal HTTP header value shape (space, not an
    # "=" / ":" assignment) and must run before the assignment pattern below,
    # which would otherwise treat the word "Bearer" itself as the value and
    # leave the real token in "authorization: bearer <token>" untouched.
    (re.compile(r"\bbearer\s+\S+", re.IGNORECASE), "bearer [redacted]"),
    (
        re.compile(
            r"(auth[_-]?token|authorization|bearer)([\"']?\s*[=:]\s*[\"']?)"
            r"(?:(?:basic|bearer|digest|token)\s+)?\S+",
            re.IGNORECASE,
        ),
        r"\1\2[redacted]",
    ),
    (re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]*"), "[redacted-jwt]"),
    (re.compile(r"\b(?:D?U|F)\d{6,}\b"), "[redacted-account]"),
    (re.compile(r"(://[^\s/:@]+:)[^\s/@]+@"), r"\1[redacted]@"),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{6,}"), "[redacted-key]"),
    (re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{6,}\b"), "[redacted-key]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[redacted-key]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{16,}\b"), "[redacted-key]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[redacted-key]"),
    (re.compile(r"\bxai-[A-Za-z0-9_-]{16,}\b"), "[redacted-key]"),
    (re.compile(r"\bnvapi-[A-Za-z0-9_-]{16,}"), "[redacted-key]"),
    (re.compile(r"(?<![A-Za-z0-9])csk-[A-Za-z0-9_-]{16,}"), "[redacted-key]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted-key]"),
    (re.compile(r"([?&](?:t|token|api[_-]?key)=)[^\s&'\"]+", re.IGNORECASE), r"\1[redacted]"),
]

# Generic `password = ...` / `token: ...` shapes. They scrub well but match
# ordinary code (`token = uuid4().hex`), so detection uses only the
# specific shapes above.
_GENERIC_ASSIGNMENT_PATTERNS = [
    # A cookie header carries several opaque values; redact through end of line.
    (re.compile(r"(\b(?:set-)?cookie[ \t]*:[ \t]*)[^\r\n]*", re.IGNORECASE), r"\1[redacted-secret]"),
    # Generic key-value assignment (env files, config dumps, JSON), last so a
    # value the specific shapes above already tagged keeps its tag. A quoted
    # value is redacted through its closing quote (multi-word passwords).
    (
        re.compile(
            rf"([\"']?{_SECRET_KEY}[\"']?\s*[:=]\s*)([\"'])(?!\[redacted)[^\"'\n]*\2",
            re.IGNORECASE,
        ),
        r"\1\2[redacted-secret]\2",
    ),
    (
        re.compile(
            rf"([\"']?{_SECRET_KEY}[\"']?\s*[:=]\s*[\"']?)(?!\[redacted)[^\"'\s,;&]+",
            re.IGNORECASE,
        ),
        r"\1[redacted-secret]",
    ),
]

_SECRET_SCRUB_PATTERNS = _SPECIFIC_SCRUB_PATTERNS + _GENERIC_ASSIGNMENT_PATTERNS

# Detection-grade generic shape: a credential-named key assigned an opaque
# LITERAL (env line, export, JSON/YAML, quoted code). Opaque means 16+ chars of
# key alphabet with letters and digits, no dots, parens or `$` (so expressions
# and references stay clean) and no placeholder word.
_SECRET_LITERAL_ASSIGNMENT = re.compile(
    rf"[\"']?[A-Za-z0-9_-]*{_SECRET_KEY}[A-Za-z0-9_-]*[\"']?\s*[:=]\s*[\"']?"
    r"(?P<value>[A-Za-z0-9_+/=~-]{16,})(?![A-Za-z0-9_+/=~.(\[$-])",
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(
    r"test|fake|dummy|example|sample|placeholder|changeme|redacted|your|x{4}",
    re.IGNORECASE,
)


def find_secret_assignments(text: str) -> list[str]:
    """``["[redacted-secret]"]`` when ``text`` assigns a literal opaque value
    to a credential-named key, else ``[]``. Never returns the value.
    """
    for match in _SECRET_LITERAL_ASSIGNMENT.finditer(text or ""):
        value = match.group("value")
        if (
            re.search(r"[A-Za-z]", value)
            and re.search(r"\d", value)
            and not _PLACEHOLDER.search(value)
        ):
            return ["[redacted-secret]"]
    return []


def find_credential_shapes(text: str) -> list[str]:
    """Labels of specific credential shapes present in ``text``.

    Returns the redaction tag (for example ``[redacted-key]``), never the
    matched value, so the result is safe to log.
    """
    found: list[str] = []
    for pattern, repl in _SPECIFIC_SCRUB_PATTERNS:
        if pattern.search(text or ""):
            label = re.sub(r"\\\d", "", repl).strip()
            if label not in found:
                found.append(label)
    return found


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

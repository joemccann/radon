#!/usr/bin/env python3
"""Redact credential-shaped fragments from validator messages (REL-190)."""

from __future__ import annotations

import re
from typing import Any

# `_pass` / `_pwd` need the underscore so prose ("tests pass: 3") survives.
_SECRET_KEY = (
    r"(?:api[_-]?key|access[_-]?key|secret[_-]?key|access[_-]?token"
    r"|client[_-]?secret|password|passwd|secret|token|_pass|_pwd"
    # `cookie` belongs in the KEY alternation, not only in the line-oriented
    # header rule below: a serialized header (`{"Cookie": "..."}`) has no colon
    # directly after the name, so the header rule missed it and no generic
    # assignment rule carried the name at all. Prose needs no guard here; every
    # use of this alternation requires an assignment separator.
    r"|cookie"
    r"|sess(?:ion)?[-_]?id|_session|csrf|xsrf)"
)

# A bounded suffix run. find_secret_assignments has accepted one all along
# (`SECRET_KEY_PROD`), but the generic REDACTION patterns required the
# credential word to be followed immediately by quoting or the delimiter, so
# an ordinary suffixed name was detected and then published. Bounded, because
# this module is scanned over text an outside contributor shapes.
_KEY_SUFFIX = r"[A-Za-z0-9_-]{0,64}"

# Execution identifiers: private, but not credential-shaped. Defined here, the
# canonical scrubber, so the publication gate (scripts/ir_push_gate.py) and the
# public issue sanitizer (scripts/nightly_issue_format.py) cannot drift apart
# again. Shapes only; no real id is recorded anywhere.
EXECUTION_ID_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    (
        "ib_exec_id",
        re.compile(
            r"(?<![0-9A-Za-z.])[0-9a-f]{8}\.[0-9a-f]{8}\.\d{2}\.\d{2}(?![0-9A-Za-z])",
            re.IGNORECASE,
        ),
    ),
    # GitHub Actions run/job ids (11 digits) are exempt only inside a full CI
    # URL; callers strip those before matching.
    ("flex_exec_id", re.compile(r"(?<![0-9A-Za-z.])\d{10,}(?![0-9A-Za-z])")),
)

def build_triple_quoted_assignment_re(key_pattern: str) -> "re.Pattern[str]":
    """An assignment whose value is TRIPLE-delimited and spans lines.

    `TOKEN = \"\"\"` then the value on the following lines (Python, TOML). Every
    other quoted-value rule in this module and in the public sanitizer handles
    ONE delimiter character, so a triple delimiter was matched as an EMPTY
    two-quote value: the assignment was rewritten, the real credential on the
    following lines was published untouched, and an opaque password carries no
    provider prefix for the literal rules to catch. The block-scalar rule
    covers YAML `|` and `>` only.

    Shared with the public-text sanitizer (``scripts/nightly_issue_format.py``)
    the same way the block-scalar structure is: only the STRUCTURE is shared,
    each caller keeps its own key alphabet and its own redaction label. That
    module already imports this one and this one imports nothing but the
    standard library, so there is no cycle and no dependency weight.

    Group 1 is the key and separator, so a caller can keep the header and
    replace the whole value. The delimiter is captured and back-referenced, so
    the value runs to its REAL close: a lone `'''` inside a `\"\"\"` value does
    not end it. The body is lazy, which costs one scan to the close per
    credential word and adds no new match start.

    An UNTERMINATED assignment runs to the end of the input, the same way
    a private-key block cut off before its END line does. A log or report
    excerpt is routinely truncated mid-value, and requiring the real close
    left the body in place while the single-line rule rewrote the opening
    delimiter pair as an empty value, so the credential survived both.
    """
    return re.compile(
        rf"([\"']?{key_pattern}[\"']?[ \t]*[:=][ \t]*)(\"\"\"|''')(?s:.*?)(?:\2|\Z)",
        re.IGNORECASE,
    )


def build_block_scalar_re(key_pattern: str) -> "re.Pattern[str]":
    """A YAML block-scalar header whose value lives on the FOLLOWING lines.

    `key: |` then an indented block. Shared with the public-text sanitizer
    (``scripts/nightly_issue_format.py``), which keeps its own key alphabet:
    only the STRUCTURE is shared, because that is the part that drifted. A
    report excerpt usually arrives commented, bulleted or quoted and may carry
    a trailing comment after the indicator, and either one used to stop the
    rule from matching while a line-oriented assignment rule rewrote the
    header and left the whole body in place. Continuation lines must be
    indented past the header, so a sibling key at the same indentation ends
    the block and survives.

    Groups: `indent`, `lead` (comment or bullet prefix) and `key`, so a caller
    can preserve the header and replace only the value.
    """
    # The lead is ONE bounded run of a single character class. A nested
    # `(?:[#>*+-]+[ \t]*)*` is ambiguous about where each repetition ends, so
    # a long marker run backtracks catastrophically: measured 0.03s, 0.51s and
    # 8.3s for 16, 20 and 24 repetitions of `#- `, which is the same
    # availability failure this module was just hardened against.
    return re.compile(
        rf"^(?P<indent>[ \t]*)(?P<lead>(?:[#>*+\-][#>*+\- \t]{{0,23}})?)"
        rf"(?P<key>[\"']?{key_pattern}[\"']?[ \t]*:[ \t]*)"
        r"[|>][0-9+-]*[ \t]*(?:\#[^\r\n]*)?\r?\n"
        r"(?:[ \t]*\r?\n|(?P=indent)[ \t]+[^\r\n]*\r?\n?)+",
        re.I | re.M,
    )


# The key alternation carries no prefix or suffix run of its own, and the
# block rule is line-anchored, so an ordinary `db_password` or `UW_TOKEN`
# would not match it at all. Bounded runs: the anchor already limits this to
# one start per line, and a bound keeps it that way.
_SECRET_BLOCK_SCALAR_RE = build_block_scalar_re(
    rf"[A-Za-z0-9_-]{{0,64}}{_SECRET_KEY}[A-Za-z0-9_-]{{0,64}}"
)

_BLOCK_SCALAR_LABEL = "[redacted-secret]"

# No leading identifier run, for the same reason the literal-assignment
# scanner below has none: the search is unanchored, so a prefixed name still
# matches from its credential word.
_TRIPLE_QUOTED_ASSIGNMENT_RE = build_triple_quoted_assignment_re(
    rf"{_SECRET_KEY}{_KEY_SUFFIX}"
)


def _redact_triple_quoted(text: str) -> str:
    """Drop triple-delimited values BEFORE any single-line rule runs.

    Ahead of the specific shapes too: `authorization = \"\"\"` would otherwise
    have its own rule treat the opening delimiter as the whole value.
    """
    return _TRIPLE_QUOTED_ASSIGNMENT_RE.sub(r"\1[redacted-secret]", text)


def _redact_block_scalar(text: str) -> str:
    """Remove block-scalar bodies BEFORE any line-oriented rule runs.

    Kept out of ``_SPECIFIC_SCRUB_PATTERNS`` deliberately: every entry there
    must carry a STRING replacement, because ``find_credential_shapes``
    derives its label from it with ``re.sub``, which raises on a callable.
    """
    return _SECRET_BLOCK_SCALAR_RE.sub(
        lambda m: f"{m.group('indent')}{m.group('lead')}{m.group('key')}"
        f"{_BLOCK_SCALAR_LABEL}\n",
        text,
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
    # The prefix must start a token. Without that anchor the rule matched from
    # any position, so a run of overlapping prefixes gave one start per three
    # characters and each one scanned the whole run: quadratic on text an
    # outside contributor shapes, which the publication gate scans.
    (
        re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]*"),
        "[redacted-jwt]",
    ),
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
    # value is redacted through its real closing quote (multi-word
    # passwords). Each quote style is handled separately and a backslash
    # escape consumes the next character: a shared `[^"']*` body ended the
    # match at the FIRST opposite or escaped quote inside the value and
    # published the tail.
    (
        re.compile(
            rf"([\"']?{_SECRET_KEY}{_KEY_SUFFIX}[\"']?\s*[:=]\s*)(\")(?!\[redacted)(?:\\.|[^\"\\\n])*\"",
            re.IGNORECASE,
        ),
        r"\1\2[redacted-secret]\2",
    ),
    (
        re.compile(
            rf"([\"']?{_SECRET_KEY}{_KEY_SUFFIX}[\"']?\s*[:=]\s*)(')(?!\[redacted)(?:\\.|[^'\\\n])*'",
            re.IGNORECASE,
        ),
        r"\1\2[redacted-secret]\2",
    ),
    (
        re.compile(
            rf"([\"']?{_SECRET_KEY}{_KEY_SUFFIX}[\"']?\s*[:=]\s*[\"']?)(?!\[redacted)[^\"'\s,;&]+",
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
#
# The key name is matched from the credential word onward: no leading
# identifier run. An unbounded leading run overlapped the credential word's own
# alphabet, so every offset of a long identifier run was retried at every
# prefix length and the scan cost grew quadratically -- and the push gate runs
# this scanner over text an outside contributor shapes. The search is
# unanchored, so a prefixed name (`ANTHROPIC_API_KEY`) still matches from its
# credential word; the trailing run is bounded well above any real suffix
# (`_PROD`). Detection is unchanged.
_SECRET_LITERAL_ASSIGNMENT = re.compile(
    rf"[\"']?{_SECRET_KEY}[A-Za-z0-9_-]{{0,64}}[\"']?\s*[:=]\s*[\"']?"
    # The value run is MAXIMAL (the first lookahead rejects its own alphabet,
    # so only one length is ever tried) and BOUNDED. An unbounded run with a
    # lookahead that rejected punctuation made every candidate rescan the
    # whole suffix: measured 0.0040s, 0.0156s, 0.0632s and 0.2527s for 250,
    # 500, 1000 and 2000 adjacent assignment-like fragments. The second
    # lookahead keeps the original semantic rejections, so a code expression
    # or a variable reference is still not a literal.
    r"(?P<value>[A-Za-z0-9_+/=~-]{16,1024})"
    r"(?![A-Za-z0-9_+/=~-])(?![.(\[$])",
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
    if _SECRET_BLOCK_SCALAR_RE.search(text or ""):
        found.append(_BLOCK_SCALAR_LABEL)
    for pattern, repl in _SPECIFIC_SCRUB_PATTERNS:
        if pattern.search(text or ""):
            label = re.sub(r"\\\d", "", repl).strip()
            if label not in found:
                found.append(label)
    return found


def scrub_credential_text(value: Any) -> Any:
    if isinstance(value, str):
        value = _redact_block_scalar(value)
        value = _redact_triple_quoted(value)
        for pattern, repl in _SECRET_SCRUB_PATTERNS:
            value = pattern.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {k: scrub_credential_text(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_credential_text(v) for v in value]
    return value

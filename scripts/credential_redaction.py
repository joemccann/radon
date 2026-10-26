#!/usr/bin/env python3
"""Redact credential-shaped fragments from validator messages (REL-190)."""

from __future__ import annotations

import re
from typing import Any, Optional

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
    # DS-2026-10-25-12: the header rules below leave a QUOTED value to the
    # tokenizer, which follows it across lines, so the names are keys here.
    r"|authorization|bearer"
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
        rf"([\"']?{key_pattern}[\"']?[ \t]*[:=][ \t]*)(\"\"\"|''')"
        # A backslash escape consumes the next character, so an escaped first
        # quote of the closer does not end the value and leave the rest
        # published (DS-2026-10-22-11). Each position takes exactly one branch
        # - an escape pair, or a character that cannot start the closer - so
        # the body stays linear and unambiguous.
        r"(?s:(?:\\.|(?!\2).)*)(?:\2|\Z)",
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
    #
    # A quoted (`> `) or commented (`# `) excerpt repeats its lead on EVERY
    # line, so a continuation line may repeat the header's lead before its
    # indentation, and a blank line may carry the bare marker run (`>`).
    # The marker run ends on a marker character and the padding is spaces
    # only, so the split between them is unambiguous and both stay bounded.
    return re.compile(
        rf"^(?P<indent>[ \t]*)"
        rf"(?P<lead>(?:(?P<mark>[#>*+\-](?:[#>*+\- \t]{{0,22}}[#>*+\-])?)[ \t]{{0,23}})?)"
        rf"(?P<key>[\"']?{key_pattern}[\"']?[ \t]*:[ \t]*)"
        # A YAML tag or anchor may precede the indicator (DS-2026-10-25-09).
        r"(?:[!&][^\s]{0,64}[ \t]+){0,2}"
        r"[|>][0-9+-]*[ \t]*(?:\#[^\r\n]*)?\r?\n"
        r"(?:[ \t]*\r?\n|(?P=indent)(?P=mark)[ \t]*\r?\n"
        r"|(?P=indent)(?:(?P=lead))?[ \t]+[^\r\n]*\r?\n?)+",
        re.I | re.M,
    )


# The hook's line alphabet also names `credential` and a bare `[-_]key`.
# Those stay off the single-line rules (`author` must survive). Multiline
# values are the gap: a block or triple-quoted body is not on the header
# line the hook rewrites. `[_-]key` matches `signing_key` and `webhook-key`.
# `authorization` is here for the same reason: its single-line rule below read
# a triple delimiter as the whole value and left the body in place.
_MULTILINE_KEY = rf"(?:{_SECRET_KEY}|authorization|credential|[_-]key)"

# The key alternation carries no prefix or suffix run of its own, and the
# block rule is line-anchored, so an ordinary `db_password` or `UW_TOKEN`
# would not match it at all. Bounded runs: the anchor already limits this to
# one start per line, and a bound keeps it that way.
_SECRET_BLOCK_SCALAR_RE = build_block_scalar_re(
    rf"[A-Za-z0-9_-]{{0,64}}{_MULTILINE_KEY}[A-Za-z0-9_-]{{0,64}}"
)

_BLOCK_SCALAR_LABEL = "[redacted-secret]"

# No leading identifier run, for the same reason the literal-assignment
# scanner below has none: the search is unanchored, so a prefixed name still
# matches from its credential word.
_TRIPLE_QUOTED_ASSIGNMENT_RE = build_triple_quoted_assignment_re(
    rf"{_MULTILINE_KEY}{_KEY_SUFFIX}"
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
    # The token stops at a quote: eating a closing quote misaligned the
    # tokenizer, so `"Bearer " "<token>"` kept the token.
    (re.compile(r"\bbearer\s+[^\s\"']+", re.IGNORECASE), "bearer [redacted]"),
    (
        re.compile(
            r"(auth[_-]?token|authorization|bearer)([\"']?\s*[=:]\s*)"
            # The whole rest of the line: a fixed scheme list redacted
            # `ApiKey` and kept the credential after it, and one token kept
            # a Digest header's later fields (DS-2026-10-25-08). A quoted
            # value may continue on later lines; the tokenizer owns it.
            r"(?![\"'\s])[^\r\n]+",
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
    # The username run is optional: password-only userinfo (`://:secret@`)
    # is valid and used to escape this rule entirely (DS-2026-10-22-13).
    (re.compile(r"(://[^\s/:@]*:)[^\s/@]+@"), r"\1[redacted]@"),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{6,}"), "[redacted-key]"),
    (re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{6,}\b"), "[redacted-key]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[redacted-key]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{16,}\b"), "[redacted-key]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[redacted-key]"),
    (re.compile(r"\bxai-[A-Za-z0-9_-]{16,}\b"), "[redacted-key]"),
    (re.compile(r"\bnvapi-[A-Za-z0-9_-]{16,}"), "[redacted-key]"),
    (re.compile(r"(?<![A-Za-z0-9])csk-[A-Za-z0-9_-]{16,}"), "[redacted-key]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted-key]"),
    # Every credential-named parameter, prefixed names included, not three:
    # the assignment pass skips a value that starts with this label, so a
    # later `&client_secret=` or `&refresh_token=` in the same query was never
    # inspected (DS-2026-10-25-10).
    (
        re.compile(
            rf"([?&](?:t|[A-Za-z0-9_.-]{{0,32}}?{_SECRET_KEY}{_KEY_SUFFIX})=)[^\s&'\"]+",
            re.IGNORECASE,
        ),
        r"\1[redacted]",
    ),
]

# Generic `password = ...` / `token: ...` shapes. They scrub well but match
# ordinary code (`token = uuid4().hex`), so detection uses only the
# specific shapes above.
# ── One value tokenizer, shared by redaction and detection ───────────────
#
# DS-2026-10-22-10 .. -15: the value of a credential assignment used to be
# matched by a family of single-purpose regexes, each modelling part of a
# quoted literal. Every one of them had an edge the others did not cover, and
# each gap published a credential or its tail: a value spanning more than one
# newline kept its continuation lines, an escaped quote before a triple-quote
# closer ended the value early, a Python `r`/`b`/`u`/`f` prefix hid the value
# from every quoted rule, and punctuation truncated an unquoted value. The
# value is now tokenized ONCE, by a hand-written scanner, and both the
# scrubber and the detector read the same span.
#
# The scanner is linear by construction: it visits each character of a value
# at most once and never backtracks, which matters because the publication
# gate runs over text an outside contributor shapes.

# Only a prefix that is actually followed by a quote is a string prefix; `rb`
# at the start of an unquoted value is part of the value.
_STRING_PREFIX = re.compile(r"[rRbBuUfF]{1,2}(?=[\"'])")

# One line break plus indentation may sit between the key and its value: a
# YAML `password:` with the value on the next line kept it (DS-2026-10-24-04).
# A YAML comment may end that header line, and whole comment lines may sit
# before the value (DS-2026-10-25-11); a trailing comment needs the whitespace
# YAML requires before `#`, so an env value starting `#` stays the value.
_ASSIGNMENT_HEAD = re.compile(
    rf"[\"']?{_SECRET_KEY}{_KEY_SUFFIX}[\"']?[ \t]*[:=][ \t]*"
    r"(?:(?:(?<=[ \t])\#[^\r\n]*)?\r?\n[ \t]*(?:\#[^\r\n]*\r?\n[ \t]*)*)?",
    re.IGNORECASE,
)


def _scan_value(text: str, pos: int) -> tuple[int, int, str]:
    """``(body_start, body_end, delimiter)`` of the value starting at ``pos``.

    The delimiter is ``""`` for an unquoted value, which runs to the next
    whitespace: `,`, `;` and `&` used to end it, so the tail of an env-style
    value survived (DS-2026-10-22-14), while whitespace keeps the rest of a
    prose sentence that merely quotes a key. A quoted value
    runs to its REAL close, honouring a backslash escape and a doubled quote,
    and may span lines. A single-delimited value with no close falls back to
    the end of the line rather than swallowing the rest of the report; a
    triple-delimited one runs to the end of the input, which is what the
    triple-quote rule above already documents for a truncated excerpt.
    """
    length = len(text)
    index = pos
    prefix = _STRING_PREFIX.match(text, index)
    if prefix is not None:
        index = prefix.end()

    if index < length and text[index] in "\"'":
        body, end, delimiter = _scan_quoted(text, index)
        # Adjacent literals on the line are one Python constant: `"" "<v>"`
        # kept its second literal (rescan of DS-2026-10-25-15). A loop, not
        # recursion, so a long run of literals cannot exhaust the stack.
        while delimiter:
            following = end + len(delimiter)
            while following < length and text[following] in " \t":
                following += 1
            prefix = _STRING_PREFIX.match(text, following)
            if prefix is not None:
                following = prefix.end()
            if following >= length or text[following] not in "\"'":
                break
            _, end, delimiter = _scan_quoted(text, following)
        return body, end, delimiter

    cursor = index
    while cursor < length and not text[cursor].isspace():
        cursor += 1
    return index, cursor, ""


def _scan_quoted(text: str, index: int) -> tuple[int, int, str]:
    """``_scan_value`` for the one quoted literal opening at ``index``."""
    length = len(text)
    quote = text[index]
    delimiter = quote * 3 if text.startswith(quote * 3, index) else quote
    body = index + len(delimiter)
    close = _find_close(text, body, delimiter, backslash_escapes=True)
    if len(delimiter) == 1:
        # DS-2026-10-24-08: a YAML or SQL single-quoted scalar has no
        # backslash escape, so `\''` there is a literal backslash then an
        # embedded quote, not an escaped quote then the close. Which
        # grammar applies is unknowable here; the later close redacts
        # the whole value under either.
        literal = _find_close(text, body, delimiter, backslash_escapes=False)
        if close is None:
            # No close under the escaping grammar: the value runs at
            # least to the end of the line, so an earlier literal close
            # (an escaped quote in a truncated excerpt) must not end it.
            eol = _end_of_line(text, body)
            if literal is None or literal < eol:
                return body, eol, ""
            close = literal
        elif literal is not None and literal > close:
            close = literal
    if close is not None:
        return body, close, delimiter
    if len(delimiter) == 1:
        return body, _end_of_line(text, body), ""
    return body, length, delimiter


def _find_close(text: str, body: int, delimiter: str, *, backslash_escapes: bool) -> Optional[int]:
    """Index of the delimiter that closes a quoted value opened at ``body``."""
    quote = delimiter[0]
    cursor = body
    length = len(text)
    while cursor < length:
        if backslash_escapes and text[cursor] == "\\":
            cursor += 2
            continue
        if len(delimiter) == 1 and text.startswith(quote * 2, cursor):
            # A doubled quote escapes itself (SQL, TOML literal).
            cursor += 2
            continue
        if text.startswith(delimiter, cursor):
            return cursor
        cursor += 1
    return None


_LINE_BREAK = re.compile(r"[\r\n]")


def _end_of_line(text: str, pos: int) -> int:
    # One search for the nearest break: finding `\r` and `\n` separately
    # scanned to the end of an LF-only text on every call, quadratic over
    # its lines.
    newline = _LINE_BREAK.search(text, pos)
    return len(text) if newline is None else newline.start()


def _continuation_end(text: str, line_start: int, eol: int) -> int:
    """End of the lines after ``eol`` indented deeper than the key line.

    DS-2026-10-25-13 / -15: a YAML plain scalar continues on more-indented
    lines, and a wrapper on the key line (a Python paren, a YAML tag or
    anchor) puts the value on the next one. Both kept those lines. Stops at
    the first non-blank line indented no deeper than the key's; blank lines
    between deeper ones belong to the value.
    """
    indent = line_start
    while indent < len(text) and text[indent] in " \t":
        indent += 1
    indent -= line_start
    end = cursor = eol
    while cursor < len(text):
        start = cursor + (2 if text.startswith("\r\n", cursor) else 1)
        first = start
        while first < len(text) and text[first] in " \t":
            first += 1
        line_end = _end_of_line(text, first)
        if line_end == first:
            # Blank: the next non-blank line decides.
            cursor = line_end
            continue
        if first - start <= indent:
            break
        end = cursor = line_end
    return end


class _LineStarts:
    """Start of the line holding each position, for positions in order.

    Searches back only to the previous position, so the total is linear.
    """

    def __init__(self, text: str) -> None:
        self._text = text
        self._seen = 0
        self._start = 0

    def __call__(self, pos: int) -> int:
        newline = self._text.rfind("\n", self._seen, pos)
        if newline != -1:
            self._start = newline + 1
        self._seen = max(self._seen, pos)
        return self._start


def _iter_assignments(text: str):
    """``(head_start, head_end, body_start, body_end, delimiter)`` per credential assignment.

    Non-overlapping: the scan resumes past each value, so a credential inside
    an already-consumed value is not re-reported.
    """
    cursor = 0
    while cursor < len(text):
        head = _ASSIGNMENT_HEAD.search(text, cursor)
        if head is None:
            return
        body_start, body_end, delimiter = _scan_value(text, head.end())
        yield head.start(), head.end(), body_start, body_end, delimiter
        cursor = max(body_end + len(delimiter), head.end() + 1)


# The idempotency opt-out tests the value in FULL. `startswith` let a value
# whose PREFIX is a label publish its remainder verbatim -- the same value-tail
# survival the tokenizer closed, in the one branch that declines to redact.
_REDACTION_LABEL = re.compile(r"\[redacted(?:-[a-z-]+)?\]")
_VALUE_CHAR = re.compile(r"[A-Za-z0-9_+/=~-]")
# The query-string rule ends a value at the next `&name=` parameter; that
# parameter is structure, not a continuation of the credential.
_QUERY_PARAMETER = re.compile(r"&[A-Za-z_][A-Za-z0-9_.-]*=")


_PARAMETER_NAME_CHAR = re.compile(r"[A-Za-z0-9_.-]")


def _is_query_parameter(text: str, head_start: int) -> bool:
    """True when the credential key opens a URL query parameter (``?k=``/``&k=``).

    The key match may start inside a prefixed name (``client_api_key``), so
    the name run before it is skipped, bounded like every name run here.
    """
    cursor = head_start
    while cursor > 0 and head_start - cursor < 64 and _PARAMETER_NAME_CHAR.match(text[cursor - 1]):
        cursor -= 1
    return cursor > 0 and text[cursor - 1] in "?&"


def _is_already_redacted(body: str, *, url_query: bool = False) -> bool:
    """True when the value IS redaction output rather than a surviving tail.

    A rule that ran earlier may redact only part of a value and leave
    STRUCTURE behind: a URL query redactor rewrites `api_key=<secret>` and the
    following `&symbol=SPY` is the next parameter, not the credential. That
    tail must survive, so the opt-out cannot simply be "the value is exactly a
    label".

    What must NOT survive is a tail that CONTINUES the value. Two tells, both
    already defined in this module: a first character from the value alphabet
    (`[redacted]<secret>`), or an opaque literal anywhere in the remainder (a
    tail reached past punctuation).
    """
    rest = body.strip()
    label = _REDACTION_LABEL.match(rest)
    if label is None:
        return False
    rest = rest[label.end() :]
    if not rest:
        return True
    if _QUERY_PARAMETER.match(rest):
        # URL structure only when the key is itself a query parameter:
        # `PASSWORD=[redacted-key]&note=<v>` is one value, tail included.
        return url_query
    if _VALUE_CHAR.match(rest):
        return False
    return not _quoted_value_is_secret(rest)


def _redact_assignments(text: str) -> str:
    """Replace every credential assignment's value with the redaction label.

    Runs after the specific shapes, so a value one of them already tagged
    keeps its own tag.
    """
    out: list[str] = []
    last = 0
    eol = -1
    line_start = _LineStarts(text)
    for head_start, head_end, body_start, body_end, delimiter in _iter_assignments(text):
        if head_end < last:
            continue
        # Decided on the value token itself: the line's later text is other
        # assignments or prose, and re-reading it per value is quadratic. A
        # label with more words after it on the line is a partial redaction
        # (a provider rule took the first word of a passphrase): redact on.
        if _is_already_redacted(
            text[body_start:body_end],
            url_query=not delimiter and _is_query_parameter(text, head_start),
        ):
            after = body_end + len(delimiter)
            while after < len(text) and text[after] in " \t":
                after += 1
            if delimiter or after >= len(text) or text[after] in "\r\n":
                continue
        if not delimiter:
            # DS-2026-10-25-04: an unquoted value is redacted to the end of
            # its line. Stopping at whitespace published every word after
            # the first of a passphrase; detection keeps the narrower span.
            # The line end is reused while it lies ahead, keeping this linear.
            if eol < body_start:
                eol = _continuation_end(
                    text, line_start(head_start), _end_of_line(text, body_start)
                )
            body_end = eol
        body = text[body_start:body_end]
        if not delimiter and not body:
            # An EMPTY quoted value still gets the label (the key is the tell);
            # an absent unquoted value has nothing to replace.
            continue
        out.append(text[last:head_end])
        if delimiter:
            out.append(f"{delimiter}[redacted-secret]{delimiter}")
        else:
            out.append("[redacted-secret]")
        last = body_end + len(delimiter)
    out.append(text[last:])
    return "".join(out)


_QUOTED_VALUE_TOKEN = re.compile(r"[A-Za-z0-9_+/=~-]{16,}")


def _quoted_value_is_secret(body: str) -> bool:
    """True when a QUOTED credential value carries an opaque literal.

    Quoted only: an unquoted value runs to the end of the line, so feeding it
    here would report ordinary code (`token = uuid4().hex`) as a credential.
    The conservative literal scanner below still owns the unquoted case.
    """
    return any(_is_opaque(m.group(0)) for m in _QUOTED_VALUE_TOKEN.finditer(body))


_GENERIC_ASSIGNMENT_PATTERNS = [
    # A cookie header carries several opaque values; redact through end of line.
    # `=` too: an unquoted `COOKIE=a=b; sid=...` env line, same as detection.
    # A quoted value may continue on later lines; the tokenizer owns it.
    (re.compile(r"(\b(?:set-)?cookie[\"']?[ \t]*[:=][ \t]*)(?![\"' \t])[^\r\n]*", re.IGNORECASE), r"\1[redacted-secret]"),
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
    rf"[\"']?{_SECRET_KEY}[A-Za-z0-9_-]{{0,64}}[\"']?\s*[:=]\s*"
    # A quoted value may open on the key line and start on the next one.
    r"(?:[\"'][ \t]*(?:\r?\n[ \t]*)?)?"
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
_LONG_LITERAL = re.compile(r"[A-Za-z0-9_+/=~-]+")
_PLACEHOLDER = re.compile(
    r"test|fake|dummy|example|sample|placeholder|changeme|redacted|your|x{4}",
    re.IGNORECASE,
)


# A cookie header carries several `name=value` pairs. The assignment rule
# above reads only the first pair as the value, so a short first cookie hid an
# opaque session cookie after it; check every pair on the header line.
_COOKIE_HEADER = re.compile(
    # The pair run carries quote characters: a quoted pair value
    # (`sid="..."`) stopped the run dead and was never inspected
    # (DS-2026-10-22-15). Each pair's value is split on quotes below, so a
    # serialized header (`{"Cookie": "sid=...", "other": "..."}`) still
    # yields the cookie value as its own fragment.
    r"\b(?:set-)?cookie[\"']?[ \t]*[:=][ \t]*[\"']?(?P<pairs>[^\r\n]*)",
    re.IGNORECASE,
)
_COOKIE_VALUE = re.compile(r"[A-Za-z0-9_+/=~-]{16,}")
_QUOTE_SPLIT = re.compile(r"[\"']")


def _is_opaque(value: str) -> bool:
    return bool(
        re.search(r"[A-Za-z]", value)
        and re.search(r"\d", value)
        and not _PLACEHOLDER.search(value)
    )


_WRAPPER_PAREN = re.compile(r"\(+(?=(?:[rRbBuUfF]{1,2})?[\"'])")
_NODE_PROPERTY = re.compile(r"[!&][^\s]*")


def _wrapped_value_is_secret(text: str, body_start: int, body_end: int) -> bool:
    """A literal behind a same-line wrapper: ``("<v>")`` or a YAML tag or
    anchor then the value. The tokenizer reads the wrapper as an unquoted
    value, which detection does not inspect for an opaque literal.
    """
    paren = _WRAPPER_PAREN.match(text, body_start, body_end)
    if paren is not None:
        return _quoted_value_is_secret(text[paren.end() : body_end])
    if _NODE_PROPERTY.fullmatch(text, body_start, body_end) is None:
        return False
    following = body_end
    while following < len(text) and text[following] in " \t":
        following += 1
    if following >= len(text) or text[following] in "\r\n":
        return False
    value_start, value_end, delimiter = _scan_value(text, following)
    value = text[value_start:value_end]
    if delimiter or (value_start > following):
        return _quoted_value_is_secret(value)
    return len(value) >= 16 and bool(_LONG_LITERAL.fullmatch(value)) and _is_opaque(value)


def find_secret_assignments(text: str) -> list[str]:
    """``["[redacted-secret]"]`` when ``text`` assigns a literal opaque value
    to a credential-named key, else ``[]``. Never returns the value.
    """
    for match in _SECRET_LITERAL_ASSIGNMENT.finditer(text or ""):
        if _is_opaque(match.group("value")):
            return ["[redacted-secret]"]
    # The same tokenizer the scrubber uses, so the two cannot drift: the
    # literal scanner above permits one opening quote and no string prefix,
    # which is how a multi-line, triple-delimited or prefixed value was
    # redacted but published as clean (DS-2026-10-22-10/-11/-12).
    text = text or ""
    line_start = _LineStarts(text)
    continued_to = -1
    for head_start, _head_end, body_start, body_end, delimiter in _iter_assignments(text):
        body = text[body_start:body_end]
        # DS-2026-10-25-14: an unclosed quoted value falls back to the line
        # end with no delimiter; its opening quote still marks it quoted.
        quoted = delimiter or (body_start > 0 and text[body_start - 1] in "\"'")
        if quoted and _quoted_value_is_secret(body):
            return ["[redacted-secret]"]
        if not delimiter and _wrapped_value_is_secret(text, body_start, body_end):
            return ["[redacted-secret]"]
        if not delimiter and head_start >= continued_to:
            # A key inside an earlier continuation was already covered by it.
            eol = _end_of_line(text, body_start)
            continued_to = _continuation_end(text, line_start(head_start), eol)
            if _quoted_value_is_secret(text[eol:continued_to]):
                return ["[redacted-secret]"]
        # The literal scanner bounds its value run at 1024, so a longer
        # unquoted literal (a session token) matched neither rule.
        if not delimiter and len(body) > 1024 and _LONG_LITERAL.fullmatch(body) and _is_opaque(body):
            return ["[redacted-secret]"]
    for match in _COOKIE_HEADER.finditer(text or ""):
        for pair in match.group("pairs").split(";"):
            for value in _QUOTE_SPLIT.split(pair.partition("=")[2]):
                value = value.strip()
                if _COOKIE_VALUE.fullmatch(value) and _is_opaque(value):
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
    # Detection has to cover every shape redaction covers, or the two drift
    # and the publication gate approves what the scrubber would have hidden.
    # The triple-delimited value was taught to the scrubber without being
    # taught here: `find_secret_assignments` cannot stand in for it, because
    # its value pattern permits a single opening quote only. The rule is
    # keyed on the credential-word alternation, so an ordinary
    # `HELP = """..."""` docstring does not match it.
    if _TRIPLE_QUOTED_ASSIGNMENT_RE.search(text or ""):
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
        return _redact_assignments(value)
    if isinstance(value, dict):
        return {k: scrub_credential_text(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_credential_text(v) for v in value]
    return value

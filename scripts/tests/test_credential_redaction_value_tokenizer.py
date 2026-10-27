"""DS-2026-10-22-10 .. -15: one value tokenizer for redaction and detection.

Each of these six shapes let a credential value, or its tail, survive the
canonical scrubber, and the publication gate and the public issue sanitizer
both read their verdict from it. They are one root cause: the value of a
credential assignment was matched by a family of single-purpose regexes, each
of which modelled part of a quoted literal, rather than tokenized once.

  -10  a quoted value spanning more than one newline kept its continuation
  -11  an escaped quote before a triple-quote closer ended the value early
  -12  a Python r/b/u/f prefix hid the quoted value from every quoted rule
  -13  password-only URL userinfo (empty username) escaped redaction
  -14  punctuation truncated an unquoted env-style value, leaving the tail
  -15  a quoted cookie-pair value was never inspected by the pair detector
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from credential_redaction import (  # noqa: E402
    find_credential_shapes,
    find_secret_assignments,
    scrub_credential_text,
)

# Synthetic throughout: an opaque run of the credential alphabet, never a real
# credential. Split so this file itself carries no assignment-shaped literal.
OPAQUE = "Zq7" + "mK2pL9xR4vT8wY" + "3nB6"
TAIL = "Hd5" + "jF1sQ0zC7bN4mX" + "8rV2"


def _clean(scrubbed: str) -> bool:
    return OPAQUE not in scrubbed and TAIL not in scrubbed


class TestRedaction:
    def test_10_multi_line_quoted_value_loses_every_line(self):
        text = f'password = "{OPAQUE}\nsecond line\n{TAIL}"\nnext_key = 1\n'
        out = scrub_credential_text(text)
        assert _clean(out), out
        assert "second line" not in out
        assert "next_key = 1" in out

    def test_11_escaped_quote_does_not_end_a_triple_quoted_value(self):
        text = f'password = """{OPAQUE}\\"""{TAIL}"""\n'
        assert _clean(scrub_credential_text(text))

    @pytest.mark.parametrize("prefix", ["r", "b", "u", "f", "R", "rb", "br", "fr"])
    def test_12_string_prefixes_do_not_hide_the_value(self, prefix):
        for quote in ('"', "'"):
            text = f"api_key = {prefix}{quote}{OPAQUE}{quote}\n"
            assert _clean(scrub_credential_text(text)), text
        text = f'api_key = {prefix}"""{OPAQUE}"""\n'
        assert _clean(scrub_credential_text(text)), text

    @pytest.mark.parametrize("label", ["[redacted]", "[redacted-secret]", "[redacted-key]"])
    @pytest.mark.parametrize("quote", ['"', "'", ""])
    def test_a_label_prefixed_value_is_still_redacted(self, label, quote):
        """The idempotency skip must test the WHOLE value, not its prefix.

        The skip exists so an already-tagged assignment is not re-wrapped. It
        asked whether the value STARTS WITH a redaction label, so a value whose
        prefix is the label published its remainder verbatim: the same
        value-tail survival this file's six shapes closed, in the one branch
        that opts out of redaction.
        """
        text = f"password = {quote}{label}{OPAQUE}{quote}\n"
        assert _clean(scrub_credential_text(text)), text

    @pytest.mark.parametrize("label", ["[redacted]", "[redacted-secret]", "[redacted-key]"])
    def test_an_exactly_labelled_value_is_left_alone(self, label):
        """Idempotency: scrubbing already-scrubbed text changes nothing."""
        for text in (
            f'password = "{label}"\n',
            f"password = {label}\n",
            f"password = {label}  \n",
        ):
            assert scrub_credential_text(text) == text, text

    def test_a_structural_tail_after_a_label_survives(self):
        """An earlier rule redacts the value; the NEXT query parameter is not it."""
        text = f"https://provider.invalid/v1?api_key={OPAQUE}&symbol=SPY\n"
        out = scrub_credential_text(text)
        assert _clean(out), out
        assert "symbol=SPY" in out, out

    def test_an_opaque_tail_past_punctuation_is_redacted(self):
        """Structure may follow a label; a credential hiding behind it may not."""
        text = f"api_key = [redacted]!{TAIL}\n"
        assert _clean(scrub_credential_text(text)), text

    def test_13_password_only_url_userinfo_is_redacted(self):
        text = f"https://:{OPAQUE}@db.internal/path\n"
        out = scrub_credential_text(text)
        assert _clean(out), out
        assert "[redacted]@" in out

    @pytest.mark.parametrize("sep", [",", ";", "&"])
    def test_14_punctuation_does_not_truncate_an_unquoted_value(self, sep):
        text = f"PASSWORD={OPAQUE}{sep}{TAIL}\nOTHER=keepme\n"
        out = scrub_credential_text(text)
        assert _clean(out), out
        assert "OTHER=keepme" in out

    @pytest.mark.parametrize("newline", ["\n", "\r\n"])
    def test_a_value_on_the_next_line_is_redacted(self, newline):
        # DS-2026-10-24-04: the head allowed spaces and tabs only after `:`.
        for value in (OPAQUE, f'"{OPAQUE}"'):
            text = f"password:{newline}    {value}{newline}other: keepme{newline}"
            out = scrub_credential_text(text)
            assert _clean(out), out
            assert "other: keepme" in out

    def test_the_tail_after_a_partial_specific_redaction_is_redacted(self):
        # DS-2026-10-24-04: a value that merely STARTED with a marker was skipped.
        text = "api_key=" + "sk-ant-" + f"{OPAQUE}.{TAIL}\n"
        out = scrub_credential_text(text)
        assert _clean(out), out

    def test_a_query_tail_after_a_marker_is_not_a_secret_tail(self):
        out = scrub_credential_text(f"https://h.invalid/v1?api_key={OPAQUE}&symbol=SPY&{TAIL}\n")
        assert OPAQUE not in out and "symbol=SPY" in out, out
        out = scrub_credential_text("token=" + "sk-ant-" + f"{OPAQUE}&{TAIL}\n")
        assert _clean(out), out

    def test_an_exact_marker_keeps_its_own_tag(self):
        out = scrub_credential_text("api_key=" + "sk-ant-" + f"{OPAQUE}\n")
        assert out == "api_key=[redacted-key]\n", out

    def test_a_single_quoted_value_still_keeps_its_delimiters(self):
        out = scrub_credential_text(f'api_key = "{OPAQUE}"\n')
        assert _clean(out)
        assert '"[redacted-secret]"' in out

    def test_unrelated_text_is_untouched(self):
        # `token = uuid4().hex` is deliberately NOT here: the generic
        # assignment rules over-redact ordinary code by design, which is why
        # detection uses the specific shapes only.
        for text in [
            "tests pass: 3\n",
            "the author is listed below\n",
            'HELP = """usage text"""\n',
        ]:
            assert scrub_credential_text(text) == text, text


class TestSingleQuotedScalarGrammar:
    def test_a_yaml_backslash_before_a_doubled_quote_keeps_no_suffix(self):
        # YAML single-quoted: `\` is literal and `''` is an embedded quote.
        text = f"password: '{OPAQUE}\\''{TAIL}'\nother: keepme\n"
        out = scrub_credential_text(text)
        assert _clean(out), out
        assert "other: keepme" in out

    def test_a_python_escaped_quote_still_keeps_no_suffix(self):
        text = f"password = '{OPAQUE}\\'{TAIL}'\nother = 1\n"
        out = scrub_credential_text(text)
        assert _clean(out), out
        assert "other = 1" in out


class TestLongUnquotedLiteralIsDetected:
    def test_a_literal_past_the_scanner_bound_is_detected(self):
        long_value = (OPAQUE + TAIL) * 40  # 1,440 chars of key alphabet
        assert find_secret_assignments(f"AWS_SESSION_TOKEN={long_value}\n")

    def test_a_long_expression_is_not_a_literal(self):
        assert not find_secret_assignments("token = " + "a1." * 400 + "hex\n")


class TestDetection:
    """Detection must cover every shape redaction covers (publication gate)."""

    @pytest.mark.parametrize(
        "text",
        [
            'password = "{v}\nsecond line\n{t}"\n',
            'password = """{v}\\"""{t}"""\n',
            'api_key = r"{v}"\n',
            "api_key = f'{v}'\n",
            'PASSWORD={v},{t}\n',
        ],
    )
    def test_each_shape_is_detected(self, text):
        payload = text.format(v=OPAQUE, t=TAIL)
        assert (
            find_secret_assignments(payload) or find_credential_shapes(payload)
        ), payload

    def test_15_quoted_cookie_pair_value_is_detected(self):
        assert find_secret_assignments(f'Cookie: sid="{OPAQUE}"\n')

    def test_13_password_only_userinfo_is_detected(self):
        assert find_credential_shapes(f"https://:{OPAQUE}@db.internal/path\n")

    @pytest.mark.parametrize(
        "text",
        [
            "tests pass: 3\n",
            "token = uuid4().hex\n",
            "api_key = os.environ['API_KEY']\n",
            'password = "your-password-here"\n',
            'HELP = """usage text"""\n',
        ],
    )
    def test_no_false_positive(self, text):
        assert not find_secret_assignments(text), text


def test_tokenizer_stays_linear_on_adversarial_text():
    """The push gate scans text an outside contributor shapes (availability)."""
    import time

    def elapsed(n):
        payload = ("password = \"" + "a" * 40 + "\n") * n
        start = time.perf_counter()
        scrub_credential_text(payload)
        find_secret_assignments(payload)
        return time.perf_counter() - start

    small, large = elapsed(200), elapsed(1600)
    assert large < max(small * 40, 2.0), (small, large)


class TestUnquotedValueRunsToEndOfLine:
    """DS-2026-10-25-04: an unquoted value ended at the first whitespace, so
    every word after the first of a multi-word passphrase was published."""

    def test_a_multi_word_unquoted_value_keeps_no_word(self):
        scrubbed = scrub_credential_text(f"password: {OPAQUE} {TAIL} again\nnext line\n")
        assert _clean(scrubbed), scrubbed
        assert "again" not in scrubbed
        assert "next line" in scrubbed

    def test_later_assignments_on_the_line_stay_redacted_once(self):
        scrubbed = scrub_credential_text(f"password={OPAQUE} token={TAIL}\n")
        assert _clean(scrubbed), scrubbed
        assert scrubbed.count("[redacted-secret]") == 1


def test_an_authorization_header_with_any_scheme_keeps_no_credential():
    """DS-2026-10-25-08: only four scheme words were recognised."""
    for scheme in ("ApiKey", "Negotiate", "SSWS"):
        scrubbed = scrub_credential_text(f"Authorization: {scheme} {OPAQUE}\n")
        assert OPAQUE not in scrubbed, scrubbed


@pytest.mark.parametrize("props", ["!!str ", "&creds ", "!!str &creds "])
def test_a_yaml_tag_or_anchor_before_a_block_scalar_keeps_no_body(props):
    """DS-2026-10-25-09: node properties before `|` hid the block body."""
    text = f"password: {props}|\n  {OPAQUE}\n  {TAIL}\nnext: ok\n"
    scrubbed = scrub_credential_text(text)
    assert _clean(scrubbed), scrubbed
    assert "next: ok" in scrubbed
    assert find_credential_shapes(text)


def test_a_redacted_query_parameter_does_not_shield_the_next():
    """DS-2026-10-25-10."""
    scrubbed = scrub_credential_text(f"https://h/x?api_key={TAIL}&client_secret={OPAQUE}&symbol=SPY")
    assert _clean(scrubbed), scrubbed
    assert scrubbed.endswith("&symbol=SPY")


def test_a_yaml_comment_after_the_key_keeps_no_next_line_value():
    """DS-2026-10-25-11."""
    scrubbed = scrub_credential_text(f"password: # db credential\n  {OPAQUE}\nnext: 1\n")
    assert _clean(scrubbed), scrubbed
    assert "next: 1" in scrubbed


def test_an_env_value_starting_with_a_hash_is_still_the_value():
    scrubbed = scrub_credential_text(f"PASSWORD=#{OPAQUE}\nNEXT=1\n")
    assert _clean(scrubbed), scrubbed
    assert "NEXT=1" in scrubbed


def test_a_prefixed_query_credential_is_redacted():
    """DS-2026-10-25-10: `refresh_token` after a redacted `access_token`."""
    scrubbed = scrub_credential_text(f"https://h/cb?access_token={TAIL}&refresh_token={OPAQUE}&limit=5")
    assert _clean(scrubbed), scrubbed
    assert scrubbed.endswith("&limit=5")


def test_a_digest_authorization_keeps_no_field():
    """DS-2026-10-25-08: only the first field after the scheme was redacted."""
    scrubbed = scrub_credential_text(
        f'Authorization: Digest username="u", nonce="{TAIL}", response="{OPAQUE}"\nnext\n'
    )
    assert _clean(scrubbed), scrubbed
    assert "next" in scrubbed


def test_comment_lines_between_key_and_value_keep_no_value():
    """DS-2026-10-25-11."""
    scrubbed = scrub_credential_text(f"password:\n  # rotated monthly\n  # owner: ops\n  {OPAQUE}\nnext: 1\n")
    assert _clean(scrubbed), scrubbed
    assert "next: 1" in scrubbed


def test_many_skipped_values_on_one_line_stay_linear():
    import time

    def elapsed(n):
        payload = "x?api_key=1 " * n
        start = time.perf_counter()
        scrub_credential_text(payload)
        return time.perf_counter() - start

    small, large = elapsed(500), elapsed(8000)
    assert large < max(small * 40, 2.0), (small, large)


def test_a_truncated_value_after_an_escaped_quote_keeps_no_tail():
    """An escaped quote is not the close of a value that never closes."""
    scrubbed = scrub_credential_text(f'password="ab\\"{OPAQUE}\nnext\n')
    assert _clean(scrubbed), scrubbed
    assert "next" in scrubbed


def test_many_assignment_lines_stay_linear():
    import time

    def elapsed(n):
        payload = "password=a\n" * n
        start = time.perf_counter()
        scrub_credential_text(payload)
        return time.perf_counter() - start

    # 16x the lines; a per-line scan to the end of the text is quadratic.
    small, large = min(elapsed(8000) for _ in range(3)), elapsed(128000)
    assert large < small * 32, (small, large)


def test_words_after_a_partially_redacted_first_word_are_redacted():
    scrubbed = scrub_credential_text(f"password: [redacted] {OPAQUE} {TAIL}\nnext\n")
    assert _clean(scrubbed), scrubbed
    assert "next" in scrubbed


def test_any_number_of_comment_lines_before_the_value():
    comments = "".join(f"  # note {i}\n" for i in range(12))
    scrubbed = scrub_credential_text(f"password:\n{comments}  {OPAQUE}\nnext: 1\n")
    assert _clean(scrubbed), scrubbed
    assert "next: 1" in scrubbed


# DS-2026-10-25-12 .. -15: value shapes the tokenizer did not open or follow.
_CONTINUED_VALUES = {
    # -12 a quoted header value continued on the next line
    "authorization-quoted-multiline": f'authorization: "Basic {OPAQUE}\n  {TAIL}"\n',
    "cookie-quoted-multiline": f'Cookie: "a=1;\n  sid={OPAQUE}{TAIL}"\n',
    # -13 a YAML plain scalar continued on more-indented lines
    "yaml-plain-continuation": f"db:\n  password: first words\n    {OPAQUE}\n    {TAIL}\nnext: 1\n",
    # -15 a wrapper on the key line, the value on the next line
    "python-paren": f'PASSWORD = (\n    "{OPAQUE}"\n    "{TAIL}"\n)\n',
    "yaml-tag": f"password: !!str\n  {OPAQUE}{TAIL}\n",
    "yaml-anchor": f"password: &db_pw\n  {OPAQUE}{TAIL}\n",
}


@pytest.mark.parametrize("text", _CONTINUED_VALUES.values(), ids=_CONTINUED_VALUES.keys())
def test_continued_value_is_scrubbed_and_detected(text):
    assert _clean(scrub_credential_text(text))
    assert find_secret_assignments(text) == ["[redacted-secret]"]


def test_yaml_plain_continuation_stops_at_the_next_key():
    text = f"password: first words\n  {OPAQUE}\nmode: live\n"
    scrubbed = scrub_credential_text(text)
    assert _clean(scrubbed)
    assert scrubbed.endswith("mode: live\n")


def test_unclosed_prefixed_string_is_detected():
    # -14 a truncated excerpt: the opening quote is the tell
    assert find_secret_assignments(f'password = r"{OPAQUE}') == ["[redacted-secret]"]


@pytest.mark.parametrize("text", [
    "password: !!str\n  changeme-placeholder\n",
    "token = (\n    compute_token()\n)\n",
    "password: short\nmode: live\n",
])
def test_openers_do_not_report_non_literals(text):
    assert find_secret_assignments(text) == []


# Rescan of the continuation fix: adjacent literals, a blank line inside a
# continued value, a label prefix on a quoted value, same-line wrappers.
_RESCAN_SHAPES = {
    "adjacent-literals": f'PASSWORD = "" "{OPAQUE}{TAIL}"\n',
    "adjacent-literals-prefixed": f"PASSWORD = 'a' r'{OPAQUE}{TAIL}'\n",
    "blank-line-in-paren": f'PASSWORD = (\n\n    "{OPAQUE}{TAIL}"\n)\n',
    "blank-line-in-yaml": f"password: first\n\n  {OPAQUE}{TAIL}\n",
    "label-then-query-suffix": f'password = "[redacted]&note={OPAQUE}{TAIL}"\n',
    "same-line-paren": f'PASSWORD = ("{OPAQUE}{TAIL}")\n',
    "same-line-yaml-tag": f"password: !!str {OPAQUE}{TAIL}\n",
    "same-line-yaml-anchor": f"password: &pw {OPAQUE}{TAIL}\n",
}


@pytest.mark.parametrize("text", _RESCAN_SHAPES.values(), ids=_RESCAN_SHAPES.keys())
def test_rescan_shape_is_scrubbed_and_detected(text):
    assert _clean(scrub_credential_text(text))
    assert find_secret_assignments(text) == ["[redacted-secret]"]


def test_blank_line_then_shallower_line_ends_the_continuation():
    text = f"password: first\n\nmode: {OPAQUE}\n"
    assert scrub_credential_text(text).endswith(f"\n\nmode: {OPAQUE}\n")


def test_url_query_label_suffix_still_survives():
    text = "url = https://h.test/q?api_key=k1&symbol=SPY\n"
    assert "&symbol=SPY" in scrub_credential_text(text)


@pytest.mark.parametrize("text", [
    "token = (compute_token_1234567890abc())\n",
    "password: !!str changeme-placeholder-1\n",
])
def test_same_line_wrappers_do_not_report_expressions(text):
    assert find_secret_assignments(text) == []


@pytest.mark.parametrize("unit", [
    'password = "" ',
    "password: x\n\n\n",
    "password: x\n\n  y\n",
    "password: !t x\n",
    'password = "" \\\n',
    "password=[redacted]\n\n\n",
    "TOKEN=\\\n",
], ids=["adjacent-literals", "blank-runs", "blank-then-deeper", "node-property",
        "backslash-continuations", "redacted-then-blank-runs", "shell-continuations"])
def test_continuation_and_adjacency_stay_linear(unit):
    import time

    def elapsed(n):
        payload = unit * n
        start = time.perf_counter()
        scrub_credential_text(payload)
        find_secret_assignments(payload)
        return time.perf_counter() - start

    small, large = elapsed(500), elapsed(4000)
    assert large < max(small * 40, 2.0), (small, large)


@pytest.mark.parametrize("text", [
    f'AUTHORIZATION = "Bearer " "{OPAQUE}{TAIL}"\n',
    "PASSWORD=" + "sk-ant-" + f"abc123&note={OPAQUE}{TAIL}\n",
], ids=["bearer-adjacent-literal", "label-query-suffix-without-url"])
def test_rescan3_shape_is_scrubbed(text):
    assert _clean(scrub_credential_text(text))


def test_prefixed_url_parameter_keeps_its_query_structure():
    text = f"https://h.invalid/v1?client_api_key={OPAQUE}&symbol=SPY\n"
    out = scrub_credential_text(text)
    assert OPAQUE not in out and "&symbol=SPY" in out, out


_RESCAN4_SHAPES = {
    "template-literal": f"const password = `{OPAQUE}{TAIL}`;\n",
    "backslash-continuation": f'PASSWORD = "" \\\n    "{OPAQUE}{TAIL}"\n',
    "redacted-header-then-continuation": f"authorization: Basic\n    {OPAQUE}{TAIL}\n",
}


@pytest.mark.parametrize("text", _RESCAN4_SHAPES.values(), ids=_RESCAN4_SHAPES.keys())
def test_rescan4_shape_is_scrubbed_and_detected(text):
    assert _clean(scrub_credential_text(text))
    if "authorization" not in text:
        assert find_secret_assignments(text) == ["[redacted-secret]"]



_RESCAN5_SHAPES = {
    "label-then-short-punctuated-tail": "password = '[redacted]!aB3cD4eF5gH6'\n",
    "unquoted-label-then-short-punctuated-tail": "password = [redacted]!aB3cD4eF5gH6\n",
    "shell-backslash-continuation": f"UW_TOKEN=\\\n{OPAQUE}{TAIL}\n",
    "subscript-target": f"os.environ['UW_TOKEN'] = '{OPAQUE}{TAIL}'\n",
}


@pytest.mark.parametrize("text", _RESCAN5_SHAPES.values(), ids=_RESCAN5_SHAPES.keys())
def test_rescan5_shape_is_scrubbed(text):
    out = scrub_credential_text(text)
    assert _clean(out) and "aB3cD4eF5gH6" not in out, out


@pytest.mark.parametrize("text", [
    f"UW_TOKEN=\\\n{OPAQUE}{TAIL}\n",
    f"os.environ['UW_TOKEN'] = '{OPAQUE}{TAIL}'\n",
], ids=["shell-backslash-continuation", "subscript-target"])
def test_rescan5_shape_is_detected(text):
    assert find_secret_assignments(text) == ["[redacted-secret]"]


# DS-2026-10-26-16 .. -20, DS-2026-10-27-01: shell and annotation shapes.
_RESCAN6_SCRUB_SHAPES = {
    "ansi-c-quoting": f"export UW_TOKEN=$'head\\\\n\n{OPAQUE}{TAIL}'\nnext=1\n",
    "heredoc-substitution": f"UW_TOKEN=$(cat <<EOF\n{OPAQUE}{TAIL}\nEOF\n)\nnext=1\n",
    "dash-heredoc-quoted-word": f"UW_TOKEN=$(cat <<-'END'\n\t{OPAQUE}{TAIL}\n\tEND\n)\n",
    "later-line-literal-close": f'password = "head\n{OPAQUE}\\" {TAIL}\n',
    "shell-word-concatenation": f"UW_TOKEN='abc'{OPAQUE}{TAIL}\n",
    "shell-word-concatenation-requoted": f'UW_TOKEN="a"{OPAQUE}"b"{TAIL} more\n',
    "prefixed-triple-quote": f'signing_key = r"""\n{OPAQUE}{TAIL}\n"""\n',
    "bytes-triple-quote": f"credential = b'''\n{OPAQUE}{TAIL}\n'''\n",
}


@pytest.mark.parametrize("text", _RESCAN6_SCRUB_SHAPES.values(), ids=_RESCAN6_SCRUB_SHAPES.keys())
def test_rescan6_shape_is_scrubbed(text):
    out = scrub_credential_text(text)
    assert _clean(out), out


_RESCAN6_DETECT_SHAPES = {
    "ansi-c-quoting": f"export UW_TOKEN=$'head\\\\n\n{OPAQUE}{TAIL}'\n",
    "heredoc-substitution": f"UW_TOKEN=$(cat <<EOF\n{OPAQUE}{TAIL}\nEOF\n)\n",
    "shell-word-concatenation": f"UW_TOKEN='abc'{OPAQUE}{TAIL}\n",
    "placeholder-substring": f'password = "{OPAQUE}testy"\n',
    "annotated-assignment": f'UW_TOKEN: str = "{OPAQUE}{TAIL}"\n',
    "annotated-subscript-type": f'password: Final[str] = "{OPAQUE}{TAIL}"\n',
}


@pytest.mark.parametrize("text", _RESCAN6_DETECT_SHAPES.values(), ids=_RESCAN6_DETECT_SHAPES.keys())
def test_rescan6_shape_is_detected(text):
    assert find_secret_assignments(text) or find_credential_shapes(text), text


@pytest.mark.parametrize("text", [
    'password = "your-api-key-here"\n',
    'UW_TOKEN = "fake-token-0123456789"\n',
    'API_TOKEN=test_token_1234567890\n',
    'secret: "changeme-placeholder-1"\n',
])
def test_whole_value_placeholders_stay_clean(text):
    assert find_secret_assignments(text) == []


def test_shell_shapes_keep_the_following_line():
    out = scrub_credential_text(f"UW_TOKEN=$(cat <<EOF\n{OPAQUE}\nEOF\n)\nSYMBOL=SPY\n")
    assert OPAQUE not in out and "SYMBOL=SPY" in out, out
    out = scrub_credential_text(f'{{"token": "{OPAQUE}", "symbol": "SPY"}}\n')
    assert OPAQUE not in out and '"symbol": "SPY"' in out, out


@pytest.mark.parametrize("unit", [
    "UW_TOKEN=$(cat <<EOF\n",
    "UW_TOKEN=$'a\n",
    "UW_TOKEN='a'b'c'd",
    "UW_TOKEN: str = 'a'\n",
], ids=["heredoc-unterminated", "ansi-c-unterminated", "word-concatenation", "annotation"])
def test_rescan6_shapes_stay_linear(unit):
    import time

    def elapsed(n):
        payload = unit * n
        start = time.perf_counter()
        scrub_credential_text(payload)
        find_secret_assignments(payload)
        return time.perf_counter() - start

    small, large = elapsed(500), elapsed(4000)
    assert large < max(small * 40, 2.0), (small, large)


_RESCAN7_SHAPES = {
    "explicit-concatenation": f'PASSWORD = "" + "{OPAQUE}{TAIL}"\n',
    "concatenation-without-spaces": f"PASSWORD='a'+'{OPAQUE}{TAIL}'\n",
    "padded-terminator-is-body": f"UW_TOKEN=$(cat <<EOF\nEOF \n{OPAQUE}{TAIL}\nEOF\n)\n",
    "dash-heredoc-space-indent-is-body": f"UW_TOKEN=$(cat <<-EOF\n  EOF\n{OPAQUE}{TAIL}\n\tEOF\n)\n",
}


@pytest.mark.parametrize("text", _RESCAN7_SHAPES.values(), ids=_RESCAN7_SHAPES.keys())
def test_rescan7_shape_is_scrubbed_and_detected(text):
    out = scrub_credential_text(text)
    assert _clean(out), out
    assert find_secret_assignments(text) == ["[redacted-secret]"]


def test_undelimited_placeholder_word_does_not_exempt_a_value():
    value = "aB3cD4eF" + "test" + "gH6iJ7kL"
    assert find_secret_assignments(f'password = "{value}"\n') == ["[redacted-secret]"]


def test_concatenation_keeps_the_following_statement():
    out = scrub_credential_text(f'PASSWORD = "" + "{OPAQUE}"\nSYMBOL = "SPY" + suffix\n')
    assert OPAQUE not in out and 'SYMBOL = "SPY" + suffix' in out, out


_RESCAN8_SHAPES = {
    "concatenated-template-literal": f"const password = '' + `{OPAQUE}{TAIL}`;\n",
    "adjacent-segment-then-template": f"const password = ''x`{OPAQUE}{TAIL}`;\n",
    "concatenated-triple-quotes": f'PASSWORD = """a""" + """\n{OPAQUE}{TAIL}\n"""\n',
    "adjacent-triple-quotes": f'PASSWORD = """a""" """\n{OPAQUE}{TAIL}\n"""\n',
}


@pytest.mark.parametrize("text", _RESCAN8_SHAPES.values(), ids=_RESCAN8_SHAPES.keys())
def test_rescan8_shape_is_scrubbed_and_detected(text):
    out = scrub_credential_text(text)
    assert _clean(out), out
    assert find_secret_assignments(text) or find_credential_shapes(text), text


_RESCAN9_SHAPES = {
    "prefixed-auth-token-continuation": f'AUTH_TOKEN = b"head\\\n{OPAQUE}{TAIL}"\n',
    "prefixed-cookie-continuation": f'COOKIE = r"sid=\\\n{OPAQUE}{TAIL}"\n',
    "shell-word-period": f"PASSWORD='x'.{OPAQUE}{TAIL}\n",
    "shell-word-colon": f"PASSWORD='x':{OPAQUE}{TAIL}\n",
}


@pytest.mark.parametrize("text", _RESCAN9_SHAPES.values(), ids=_RESCAN9_SHAPES.keys())
def test_rescan9_shape_is_scrubbed_and_detected(text):
    out = scrub_credential_text(text)
    assert _clean(out), out
    assert find_secret_assignments(text) == ["[redacted-secret]"]

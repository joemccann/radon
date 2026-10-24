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

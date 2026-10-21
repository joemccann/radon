"""scrub_credential_text must catch a bare HTTP "Bearer <token>" value, not
only "authorization: bearer <token>" (DS-2026-09-20-06)."""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from credential_redaction import scrub_credential_text  # noqa: E402


def test_bare_bearer_header_value_is_redacted():
    body = scrub_credential_text("curl -H 'Bearer abcdefghijklmnop9999' https://x")
    assert "abcdefghijklmnop9999" not in body
    assert "[redacted]" in body


def test_bearer_without_leading_authorization_keyword_is_redacted():
    body = scrub_credential_text("token was Bearer abcdefghijklmnop9999 sent")
    assert "abcdefghijklmnop9999" not in body


def test_authorization_bearer_assignment_still_redacted():
    body = scrub_credential_text("Authorization: Bearer opaque-value-123")
    assert "opaque-value-123" not in body


def test_bare_nvidia_and_cerebras_keys_are_redacted():
    nvidia = "nvapi-" + "A1b2C3d4E5f6G7h8I9j0"
    cerebras = "csk-" + "a1b2c3d4e5f6g7h8i9j0"
    body = scrub_credential_text(f"keys {nvidia} and {cerebras} pasted")
    assert nvidia not in body
    assert cerebras not in body
    assert "keys " in body and " pasted" in body


def test_generic_secret_assignments_are_redacted():
    # Env-file / config-dump shapes reach the embed + validator egress paths,
    # not only distill (DS-2026-09-27-02).
    token = "Zq9" + "xY7wV5uT3sR1"
    password = "Pw0" + "rdV4lue88"
    secret = "Cs1" + "ecretV4lue"
    body = scrub_credential_text(
        f"UW_TOKEN={token}\nDB_PASSWORD: '{password}'\n\"client_secret\": \"{secret}\""
    )
    assert token not in body
    assert password not in body
    assert secret not in body
    assert "UW_TOKEN=" in body and "DB_PASSWORD:" in body


def test_generic_assignment_leaves_plain_prose_untouched():
    text = "the token budget ran out; password reset scheduled"
    assert scrub_credential_text(text) == text


def test_authorization_scheme_and_compound_secret_keys_are_redacted():
    basic = "dXNlcjpw" + "YXNzd29yZA=="
    opaque = "tok" + "V4lue7788"
    aws = "wJalr" + "XUtnFEMI9x"
    django = "dj4n" + "goS3cret"
    body = scrub_credential_text(
        f"Authorization: Basic {basic}\n"
        f'{{"authorization": "Token {opaque}"}}\n'
        f"AWS_SECRET_ACCESS_KEY={aws}\nSECRET_KEY={django}"
    )
    for value in (basic, opaque, aws, django):
        assert value not in body


def test_paper_account_ids_and_url_userinfo_are_redacted():
    paper = "D" + "U7654321"
    secret = "pa" + "ss4Word9"
    body = scrub_credential_text(
        f"account {paper} rejected; connect https://svc:{secret}@db.example.test/x"
    )
    assert paper not in body
    assert secret not in body
    assert "https://svc:[redacted]@db.example.test/x" in body
    assert scrub_credential_text("see https://example.test/a@b") == "see https://example.test/a@b"


def test_pass_suffixed_keys_and_quoted_multiword_values_are_redacted():
    word = "hunt" + "er2x"
    phrase = "correct horse " + "battery staple"
    body = scrub_credential_text(
        f"MENTHORQ_PASS={word}\nDB_PWD: {word}\n"
        f'password: "{phrase}"\n{{"client_secret": \'{phrase}\'}}'
    )
    assert word not in body
    assert "horse" not in body and "staple" not in body
    assert "MENTHORQ_PASS=" in body
    prose = "3 tests pass: all green; bypass: none"
    assert scrub_credential_text(prose) == prose


def _pem_block(label: str) -> str:
    body = "MIIBVQIBADANBgkqhkiG9w0BAQEFAASCAT8wggE7AgEAAkEAqzNOTREAL" + "\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABqzNOTREAL"
    return f"-----BEGIN {label}-----\n{body}\n-----END {label}-----"


def test_private_key_blocks_are_redacted_and_detected():
    from credential_redaction import find_credential_shapes

    for label in ("PRIVATE KEY", "RSA PRIVATE KEY", "OPENSSH PRIVATE KEY", "ENCRYPTED PRIVATE KEY"):
        text = f"before\n{_pem_block(label)}\nafter"
        body = scrub_credential_text(text)
        assert "NOTREAL" not in body and "BEGIN" not in body, body
        assert body == "before\n[redacted-private-key]\nafter"
        assert find_credential_shapes(text) == ["[redacted-private-key]"]
    truncated = scrub_credential_text("x " + _pem_block("OPENSSH PRIVATE KEY").split("\n-----END")[0])
    assert "NOTREAL" not in truncated
    public = "-----BEGIN PUBLIC KEY-----\nMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE\n-----END PUBLIC KEY-----"
    assert scrub_credential_text(public) == public


def test_cookie_headers_and_session_keys_are_redacted():
    for text in (
        "Cookie: sessionid=qz1NOTREAL; other=qz2NOTREAL",
        "Set-Cookie: __session=qz3NOTREAL; Path=/",
        "SESSION_ID=qz4NOTREAL", "csrf=qz5NOTREAL", '{"xsrf": "qz6NOTREAL"}',
    ):
        body = scrub_credential_text(f"upstream sent {text}\nretry after review")
        assert "NOTREAL" not in body, body
        assert body.endswith("\nretry after review"), body
    prose = "the session ended cleanly: no cookie was read"
    assert scrub_credential_text(prose) == prose


def test_advisor_master_account_ids_are_redacted():
    body = scrub_credential_text("accounts F1234567 U7654321 DU1234567")
    assert body == "accounts [redacted-account] [redacted-account] [redacted-account]"


# Every shape find_secret_assignments detected before its key-name pattern was
# bounded, so the bound cannot silently narrow detection. Each case is
# ASSEMBLED AT RUNTIME from a key fragment, a separator and a value fragment:
# the repo contract is that no credential-shaped assignment is ever spelled
# as a source literal, even a synthetic one, so a later SHA cannot trip the
# secret scanner on this table.
_SECRET_ASSIGNMENT_PARTS = (
    ("API_KEY", "=", "a1b2c3d4e5f6g7h8"),
    ("MY_API_KEY", "=", "a1b2c3d4e5f6g7h8"),
    ("UW_TOKEN", "=", '"tok3n9abcdefghijklmn"'),
    ("export ANTHROPIC_API_KEY", "=", "sk9abcdefghijklmnop"),
    ('{"client_secret"', ": ", '"cs9abcdefghijklmnop"}'),
    ("password", ": ", "p4ssw0rdp4ssw0rdp4ss"),
    ("db_passwd", " = ", "'zq1xw2cv3bn4ml5kjh6'"),
    ("SESSION_ID", "=", "qz4abcdefghijklmn12"),
    ("csrf", " = ", "abc123def456ghi789j"),
    ("access-token", ": ", "Ab1Cd2Ef3Gh4Ij5Kl6M"),
    ("SECRET_KEY_PROD", "=", "0123456789abcdef"),
    ("x_pwd", "=", "abcdef0123456789xy"),
    ("accessKey", "=", "AKIA0123456789ABCDEF"),
)

_SECRET_ASSIGNMENT_CASES = tuple("".join(parts) for parts in _SECRET_ASSIGNMENT_PARTS)

_NOT_SECRET_ASSIGNMENT_PARTS = (
    ("token", " = ", "uuid4().hex"),
    ("API_KEY", "=", "$UW_TOKEN"),
    ("api_key", "=", "your-key-here-placeholder"),
    ("password", "=", "changeme123456789"),
    ("tests pass", ": ", "3 of 3 green"),
    ("token", "=", "short1"),
    ("api_key", "=", "allletterswithoutdigits"),
    ("api_key", "=", "1234567890123456"),
)

_NOT_SECRET_ASSIGNMENT_CASES = tuple("".join(parts) for parts in _NOT_SECRET_ASSIGNMENT_PARTS)


def test_secret_assignment_shapes_are_detected():
    from credential_redaction import find_secret_assignments

    for text in _SECRET_ASSIGNMENT_CASES:
        assert find_secret_assignments(text) == ["[redacted-secret]"], text
    for text in _NOT_SECRET_ASSIGNMENT_CASES:
        assert find_secret_assignments(text) == [], text


def test_secret_assignment_scan_stays_linear_on_long_identifier_runs():
    """The publication gate scans text an outside contributor shapes, so the
    scanner must stay linear. An unbounded key-name run made it quadratic: a
    long run of identifier characters stalled the gate for minutes."""
    import subprocess

    probe = (
        "import sys;"
        f"sys.path.insert(0, {str(_SCRIPTS_DIR)!r});"
        "from credential_redaction import find_secret_assignments as f;"
        "assert f('A' * 250_000 + '!') == []"
    )
    # A bounded scan needs ~0.3s here, interpreter startup included, so a 15s
    # ceiling is ~50x headroom and CI load cannot flake it. The unbounded
    # pattern needed minutes on this input.
    subprocess.run([sys.executable, "-c", probe], check=True, timeout=15)


# A value containing the OPPOSITE quote, or an escaped delimiter, used to end
# the match early and publish the tail. Every value below is invented.
_QUOTED_VALUE_PARTS = (
    ("API_KEY", "=", '"', "ab'cd9efgh12345", '"'),
    ("API_KEY", "=", "'", 'ab"cd9efgh12345', "'"),
    ("API_KEY", "=", '"', 'ab\\"cd9efgh12345', '"'),
    ("UW_TOKEN", ": ", '"', "zz'9ttt'uuu12345", '"'),
)


def test_quoted_values_are_redacted_through_their_real_closing_quote():
    for key, sep, open_q, value, close_q in _QUOTED_VALUE_PARTS:
        text = "config had " + key + sep + open_q + value + close_q + " set"
        out = scrub_credential_text(text)
        assert "efgh12345" not in out and "uuu12345" not in out, out
        assert out.startswith("config had ") and out.endswith(" set"), out


def test_jwt_scan_stays_linear_on_overlapping_prefixes():
    """The publication gate scans text an outside contributor shapes. The JWT
    rule matched from any position, so a run of overlapping prefixes gave one
    start per three characters, each scanning to the end of the run."""
    import subprocess

    probe = (
        "import sys;"
        f"sys.path.insert(0, {str(_SCRIPTS_DIR)!r});"
        "from credential_redaction import scrub_credential_text as s;"
        "t = 'eyJ' * 160_000;"
        "assert s(t) == t"
    )
    # Measured: linear needs well under a second here, while the position-free
    # rule needed 59s on this exact input (and 3.8s / 14.0s at a quarter and a
    # half of it, a clean 4x per doubling). A 20s ceiling is ~20x headroom for
    # the fix and a third of the regression's cost, so it can neither flake
    # nor pass a regression.
    subprocess.run([sys.executable, "-c", probe], check=True, timeout=20)


# A block scalar hides its value on the FOLLOWING lines, so a line-oriented
# rule publishes the whole body. Values below are invented.
def test_block_scalar_values_are_scrubbed():
    text = "password: |\n  NOTREALBLOCK1\n  NOTREALBLOCK2\nnext: fine\n"
    out = scrub_credential_text(text)
    assert "NOTREALBLOCK" not in out, out
    assert "next: fine" in out, out


def test_block_scalar_header_may_be_commented_or_carry_a_trailing_comment():
    for header in ("# password: |", "- password: |", "password: | # note"):
        text = header + "\n  NOTREALBLOCK3\ntail\n"
        out = scrub_credential_text(text)
        assert "NOTREALBLOCK3" not in out, out
        assert "tail" in out, out


def test_a_sibling_key_at_the_same_indent_is_not_eaten():
    text = "password: |\n  NOTREALBLOCK4\nkeep_me: ordinary prose\n"
    out = scrub_credential_text(text)
    assert "NOTREALBLOCK4" not in out, out
    assert "keep_me: ordinary prose" in out, out


def test_credential_shape_labels_survive_a_block_scalar():
    """find_credential_shapes derives its label from the replacement with
    re.sub, so a callable replacement in the specific-pattern list made it
    raise TypeError on any text containing a block scalar."""
    from credential_redaction import find_credential_shapes

    labels = find_credential_shapes("password: |\n  NOTREALLABEL1\ntail\n")
    assert labels == ["[redacted-secret]"], labels
    assert find_credential_shapes("nothing here") == []


def test_block_scalar_header_prefix_scan_is_linear():
    """The header prefix is one bounded run of one character class. A nested
    quantifier over an overlapping class backtracked catastrophically: 0.03s,
    0.51s and 8.3s at 16, 20 and 24 repetitions of a marker pair."""
    import subprocess

    probe = (
        "import sys;"
        f"sys.path.insert(0, {str(_SCRIPTS_DIR)!r});"
        "from credential_redaction import scrub_credential_text as s;"
        "t = '#- ' * 20_000 + 'x\\n';"
        "assert s(t) == t"
    )
    subprocess.run([sys.executable, "-c", probe], check=True, timeout=20)


def test_block_scalar_matches_a_prefixed_or_suffixed_credential_key():
    """`db_password` and `UW_TOKEN` are the ordinary spellings. The key
    alternation has no prefix or suffix run of its own, and the block rule is
    line-anchored, so without wrapping it matched neither and the generic
    assignment rule rewrote the header while the body survived."""
    for key in ("db_password", "UW_TOKEN", "ANTHROPIC_API_KEY", "x_pwd"):
        text = f"{key}: |\n  NOTREALPREFIXED1\ntail\n"
        out = scrub_credential_text(text)
        assert "NOTREALPREFIXED1" not in out, out
        assert "tail" in out, out


def test_secret_assignment_scan_is_linear_on_adjacent_fragments():
    """The value run used to be unbounded with a lookahead that rejected
    punctuation, so every candidate rescanned the whole suffix: 0.0040s,
    0.0156s, 0.0632s and 0.2527s for 250, 500, 1000 and 2000 adjacent
    assignment-like fragments, a clean 4x per doubling."""
    import subprocess

    probe = (
        "import sys;"
        f"sys.path.insert(0, {str(_SCRIPTS_DIR)!r});"
        "from credential_redaction import find_secret_assignments as f;"
        "f('api_key=a1' * 64_000 + '.')"
    )
    subprocess.run([sys.executable, "-c", probe], check=True, timeout=20)


def test_suffixed_credential_keys_are_scrubbed_not_only_detected():
    """Detection already accepted a suffix; redaction did not, so
    `SECRET_KEY_PROD=<value>` passed the scrubber the knowledge pipeline runs
    before sending document text to a model provider."""
    for key in ("SECRET_KEY_PROD", "DB_PASSWORD_BACKUP", "UW_TOKEN_STAGING", "api_key_v2"):
        text = f"the excerpt held {key}=NOTREALSUFFIXED1x9 and more"
        out = scrub_credential_text(text)
        assert "NOTREALSUFFIXED1x9" not in out, out
        assert out.endswith("and more"), out


# A serialized cookie header (JSON / dict dump) has no colon directly after the
# header name, so the line-oriented header rule never saw it and the generic
# assignment rules did not carry the header name at all: the value was both
# published and reported clean (DS-2026-10-21-03). Values are invented.
_SERIALIZED_COOKIE_CASES = (
    '{"Cookie": "sid=qz7NOTREALvalue1234567890"}',
    '{"set-cookie": "__session=qz8NOTREALvalue1234567890; Path=/"}',
    "cookie = 'sid=qz9NOTREALvalue1234567890'",
    "COOKIE_HEADER=sidqzANOTREALvalue1234567890",
)


def test_serialized_cookie_headers_are_redacted_and_detected():
    from credential_redaction import find_secret_assignments

    for text in _SERIALIZED_COOKIE_CASES:
        body = scrub_credential_text("upstream sent " + text + "\nretry after review")
        assert "NOTREAL" not in body, body
        assert body.endswith("\nretry after review"), body
        assert find_secret_assignments(text) == ["[redacted-secret]"], text


def test_cookie_prose_without_an_assignment_survives():
    for prose in (
        "the cookie banner was dismissed and the run continued",
        "set-cookie handling is covered by the middleware test",
    ):
        assert scrub_credential_text(prose) == prose, prose
        from credential_redaction import find_secret_assignments

        assert find_secret_assignments(prose) == [], prose

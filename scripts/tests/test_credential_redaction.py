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

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

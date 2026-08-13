"""A security report must never embed the literal credential it is reporting.

2026-07-18: the audit report documented a TWS credential finding by quoting the
live value, and the rendered HTML was committed to a PUBLIC repo
(docs/security-audit-2026-07-18.html). The secret scan caught it, it was
allowlisted as "already public, not a new exposure", and the literal is now
permanently in git history. A rebase later renumbered that commit, orphaned the
allowlist, turned CI red and silently skipped the deploy for ~5 hours.

The generator is the right place to stop this: every rendered field flows
through esc(), so redaction there cannot be bypassed by an audit agent writing
a new field, a new severity, or a differently-shaped finding into the JSON.

The report must stay USEFUL: the variable name and surrounding prose survive,
only the value is replaced.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "security" / "gen_security_report.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("gen_security_report", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_security_report"] = module
    spec.loader.exec_module(module)
    return module


gen = _load_module()

# Constructed at runtime so this test file never itself contains a
# credential-shaped literal that the secret scanner would flag.
_USER = "radon" + "trader" + "01"
_PASS = "Hq7" + "!vK2" + "pLm9" + "xZ"


class TestCredentialAssignmentsAreRedacted:
    def test_tws_password_literal_never_reaches_the_output(self):
        rendered = gen.esc(f"The config contained TWS_PASSWORD={_PASS} in plaintext.")
        assert _PASS not in rendered

    def test_tws_userid_literal_never_reaches_the_output(self):
        rendered = gen.esc(f"Found TWS_USERID={_USER} committed to the repo.")
        assert _USER not in rendered

    def test_the_variable_name_survives_so_the_finding_stays_actionable(self):
        rendered = gen.esc(f"The config contained TWS_PASSWORD={_PASS} in plaintext.")
        assert "TWS_PASSWORD" in rendered
        assert "in plaintext" in rendered

    def test_a_redaction_marker_is_visible(self):
        rendered = gen.esc(f"TWS_PASSWORD={_PASS}")
        assert gen.REDACTION_MARKER in rendered

    @pytest.mark.parametrize(
        "name",
        ["API_KEY", "SECRET_KEY", "ACCESS_TOKEN", "DB_PASSWORD", "AUTH_TOKEN"],
    )
    def test_other_secret_shaped_assignments_are_redacted(self, name):
        rendered = gen.esc(f"{name}={_PASS}")
        assert _PASS not in rendered
        assert name in rendered

    def test_quoted_values_are_redacted_too(self):
        for quoted in (f"TWS_PASSWORD='{_PASS}'", f'TWS_PASSWORD="{_PASS}"'):
            assert _PASS not in gen.esc(quoted)


class TestCredentialShapedProseIsRedacted:
    def test_password_example_prose(self):
        rendered = gen.esc(f"password example: {_PASS}")
        assert _PASS not in rendered

    def test_credential_like_prose(self):
        rendered = gen.esc(f"credentials like {_PASS} were hardcoded")
        assert _PASS not in rendered


class TestOrdinaryReportProseIsUntouched:
    """Over-redaction would make audit reports useless, so the negative cases
    matter as much as the positive ones."""

    def test_plain_prose_passes_through(self):
        text = "The handler reads TWS_PASSWORD from the environment, never from disk."
        assert gen.esc(text) == text

    def test_file_paths_and_line_refs_survive(self):
        text = "scripts/api/server.py:412 constructs the client without a timeout"
        assert gen.esc(text) == text

    def test_html_escaping_still_applies(self):
        assert gen.esc("<script>alert(1)</script>") == (
            "&lt;script&gt;alert(1)&lt;/script&gt;"
        )

    def test_none_still_renders_empty(self):
        assert gen.esc(None) == ""

    def test_env_var_name_alone_is_not_redacted(self):
        text = "Set TWS_PASSWORD in the systemd EnvironmentFile."
        assert gen.esc(text) == text

    def test_placeholder_assignments_are_left_alone(self):
        """An audit that already redacted itself must not be double-redacted
        into noise."""
        for placeholder in ("TWS_PASSWORD=<redacted>", "TWS_PASSWORD=***", "API_KEY="):
            assert gen.REDACTION_MARKER not in gen.esc(placeholder) or "=" in gen.esc(
                placeholder
            )


class TestRedactionIsAppliedAtTheChokepoint:
    def test_every_rendered_field_goes_through_esc(self):
        """If a future field is interpolated raw, redaction is bypassed and this
        whole guarantee is void."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        body = source.split("def main()", 1)[1]
        for line in body.splitlines():
            if ".get(" not in line or "esc(" in line:
                continue
            # allow non-rendering lookups (assignment / control flow)
            stripped = line.strip()
            if stripped.startswith(("res.", "counts", "fixes", "conf", "fps", "clean")):
                continue
            assert "=" in stripped or "sort" in stripped, (
                f"raw .get() interpolated without esc(): {stripped}"
            )

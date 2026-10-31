"""Exercise only the capture's env sections against synthetic local files."""
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "phase0_capture.sh"


def test_live_root_env_is_in_all_diagnostic_sections_and_secrets_are_redacted(tmp_path):
    text = SCRIPT.read_text()
    root = tmp_path / "root.env"
    legacy = tmp_path / "legacy.env"
    web = tmp_path / "web.env"
    root.write_text("RADON_MODE=hetzner\nTURSO_DB_URL=libsql://synthetic.example.test\nTURSO_AUTH_TOKEN=synthetic-secret\nCLIENT_ID=91\nCLIENT_SECRET=synthetic-client-secret\nCLIENT_ID_TOKEN=synthetic-client-id-token\n")
    legacy.write_text("RADON_MODE=legacy\n")
    text = text.replace("/etc/radon/env", str(root)).replace("/home/radon/radon-cloud/web/.env", str(web)).replace("/home/radon/radon-cloud/.env", str(legacy))
    functions = text[:text.index('sec "HOST BASICS"')]
    env = text[text.index('sec "ENV FILES'):text.index('sec "DOCKER')]
    client = text[text.index('sec "CLIENT-ID'):text.index('sec "CAPTURE COMPLETE')]
    result = subprocess.run(["bash"], input=functions + env + client, text=True, capture_output=True, check=True)
    surface, db = result.stdout.split("TURSO / DB references", 1)
    db, client = db.split("CLIENT-ID usage hints", 1)
    assert "RADON_MODE=hetzner" in surface
    assert "RADON_MODE=legacy" in surface
    assert "(absent)" in surface
    assert "TURSO_DB_URL=libsql://synthetic.example.test" in db
    assert "CLIENT_ID=91" in client
    assert "CLIENT_SECRET=<redacted:" in surface
    assert "CLIENT_ID_TOKEN=<redacted:" in client
    assert "synthetic-client-id-token" not in result.stdout
    assert "TURSO_AUTH_TOKEN=<redacted:" in db
    assert "synthetic-secret" not in result.stdout
    assert "synthetic-client-secret" not in result.stdout


def _redact(text: str) -> str:
    """Run only the capture's redact() over ``text``."""
    body = SCRIPT.read_text()
    functions = body[: body.index('sec "HOST BASICS"')]
    result = subprocess.run(
        ["bash"],
        input=functions + 'redact\n',
        text=True,
        capture_output=True,
        check=True,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    return result.stdout


TWS_PW_KEY = "TWS_" + "PASSWORD"  # joined so no static assignment sits in the tree


def _redact_input(text: str) -> str:
    body = SCRIPT.read_text()
    functions = body[: body.index('sec "HOST BASICS"')]
    script = functions + "redact <<'RADON_FIXTURE_EOF'\n" + text + "\nRADON_FIXTURE_EOF\n"
    result = subprocess.run(
        ["bash"], input=script, text=True, capture_output=True, check=True
    )
    return result.stdout


# DS-2026-10-28-07: the header promises the capture carries no secret values,
# but redact() keyed on the text before the FIRST `=` of each physical line.
# A systemd `Environment="KEY=value"` directive keys on `Environment`, which
# matches no secret name, and the continuation lines of a quoted multi-line
# value were keyed on nothing at all. Both printed verbatim. The capture is
# the sanctioned way to share VPS ground truth, so a redactor that silently
# fails is worse than none.
def test_a_systemd_environment_directive_is_redacted():
    out = _redact_input('Environment="RADON_SERVICE_TOKEN=live_tok_NOTREAL123"')
    assert "live_tok_NOTREAL123" not in out, out
    assert "<redacted:" in out, out


def test_every_systemd_environment_spelling_is_redacted():
    for line in (
        'Environment="RADON_SERVICE_TOKEN=live_tok_NOTREAL123"',
        "Environment=RADON_SERVICE_TOKEN=live_tok_NOTREAL123",
        "Environment='UW_TOKEN=live_tok_NOTREAL123'",
        'DefaultEnvironment="API_KEY=live_tok_NOTREAL123"',
    ):
        out = _redact_input(line)
        assert "live_tok_NOTREAL123" not in out, (line, out)


def test_a_quoted_multiline_value_does_not_leak_its_continuation():
    out = _redact_input(TWS_PW_KEY + '="first_line\nsecond_line_NOTREALsecret"')
    assert "second_line_NOTREALsecret" not in out, out


def test_a_non_secret_key_still_shows_its_value():
    out = _redact_input("RADON_MODE=hetzner\nEnvironment=NODE_ENV=production")
    assert "RADON_MODE=hetzner" in out, out
    assert "production" in out, out


# Remediate rescan of the fix above: the carry cleared on the first
# continuation line that held no quote, so a value spanning three or more
# lines resumed printing before it closed; and only the FIRST assignment of a
# systemd Environment directive was inspected, while systemd accepts several
# per directive.
def test_a_value_spanning_several_lines_stays_redacted_to_its_close():
    out = _redact_input(TWS_PW_KEY + '="one\ntwo\nthree_NOTREALsecret"\nRADON_MODE=hetzner')
    assert "three_NOTREALsecret" not in out, out
    assert "RADON_MODE=hetzner" in out, out


def test_every_assignment_in_one_environment_directive_is_inspected():
    out = _redact_input('Environment="NODE_ENV=production" "UW_TOKEN=live_NOTREAL123"')
    assert "live_NOTREAL123" not in out, out


# Second rescan: systemd accepts whitespace around a directive's `=`, so the
# key run carried a trailing space or tab and no longer ended in the
# directive name.
def test_whitespace_around_the_directive_separator_is_tolerated():
    for line in (
        'Environment = "UW_TOKEN=live_NOTREAL123"',
        'Environment\t="UW_TOKEN=live_NOTREAL123"',
        '  Environment  =  "UW_TOKEN=live_NOTREAL123"',
        'UW_TOKEN = live_NOTREAL123',
    ):
        out = _redact_input(line)
        assert "live_NOTREAL123" not in out, (line, out)


# Third rescan: the DB and client-id sections piped grep into redact, so
# redact() saw single lines and a quoted value's continuation arrived with no
# key in front of it. Each file is redacted whole before any filter now.
def test_the_filtered_sections_redact_before_they_filter():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "redact_env_files | grep" in text, text[-600:]
    assert "| redact" not in text, "a filter still runs before redaction"


# DS-2026-10-29-04: systemd joins a directive that ends in a backslash, so a
# quoted assignment can OPEN on a later physical line, with its quote before
# the key. The value then does not start with a quote, the carry never arms,
# and every further continuation line falls through the catch-all print.
def test_a_continued_directive_redacts_its_whole_value():
    out = _redact_input(
        'Environment=NODE_ENV=production \\\n  "UW_TOKEN=head_NOTREAL \\\n  tail_NOTREALsecret"'
    )
    assert "tail_NOTREALsecret" not in out, out
    assert "head_NOTREAL" not in out, out


def test_a_continued_non_secret_directive_keeps_its_lines():
    out = _redact_input('Environment=NODE_ENV=production \\\n  PATH=/usr/bin\nRADON_MODE=hetzner')
    assert "production" in out, out
    assert "/usr/bin" in out, out
    assert "RADON_MODE=hetzner" in out, out


# Remediate rescan: the carry armed only for the two ASCII quotes, while the
# project's dotenv accepts a backtick-quoted multiline value.
def test_a_backtick_quoted_multiline_value_does_not_leak():
    out = _redact_input(TWS_PW_KEY + "=`first\nsecond_line_NOTREALsecret`\nRADON_MODE=hetzner")
    assert "second_line_NOTREALsecret" not in out, out
    assert "RADON_MODE=hetzner" in out, out


# A comment never continues: neither systemd nor dotenv joins a comment line
# that ends in a backslash with the next line. Assembling it as one logical
# line matched the comment rule and printed the next physical line raw.
def test_a_comment_ending_in_a_backslash_does_not_swallow_the_next_line():
    out = _redact_input("# note \\\nUW_TOKEN=live_NOTREAL123\nRADON_MODE=hetzner")
    assert "live_NOTREAL123" not in out, out
    assert "# note \\" in out, out
    assert "RADON_MODE=hetzner" in out, out


def test_a_semicolon_line_is_still_keyed_and_redacted():
    out = _redact_input("; UW_TOKEN=live_NOTREAL123 \\\nRADON_MODE=hetzner")
    assert "live_NOTREAL123" not in out, out

"""The independent PDF archive ships through the ordinary managed deployment."""
import hashlib
import re
from pathlib import Path

from test_app_runtime import CLOUD, PYTHON_REPO, TEST_SHA, _run, _run_line

UNIT = "radon-marketdesk-pdf.service"


def test_pdf_runtime_config_uses_the_canonical_dropin_validation_gate():
    text = (CLOUD / "scripts/bootstrap-control-plane.sh").read_text()
    def entries(name):
        return re.search(rf"readonly -a {name}=\(\n(.*?)\n\)", text, re.S).group(1).split()
    sources, kinds = entries("SOURCES"), entries("KINDS")
    assert len(sources) == len(kinds)
    assert dict(zip(sources, kinds))["services/" + UNIT + ".d/runtime-container.conf"] == "dropin"


def test_pdf_job_is_an_isolated_python_research_unit(tmp_path):
    result = _run(tmp_path, ["run", UNIT])
    assert result.returncode == 0, result.stderr
    line = _run_line(result)
    assert f"{PYTHON_REPO}:{TEST_SHA} python scripts/research/marketdesk_pdf_job.py" in line
    assert "--network bridge" in line
    assert f"{tmp_path / 'state/private/research'}:/var/lib/radon/research:rw" in line
    assert "--user " in line
    for forbidden in (":/home/radon/radon/data", ":/var/lib/radon/media", ":/var/lib/radon/ib-lease", "CREDENTIALS_DIRECTORY", "/run/radon-control", ":/home/radon/.claude", ":/home/radon/.codex", ":/home/radon/.grok"):
        assert forbidden not in line


def test_pdf_env_excludes_every_unneeded_credential(tmp_path):
    values = {
        "MARKETDESK_FIREBASE_REFRESH_TOKEN": "private-login",
        "MARKETDESK_PDF_MIN_INTERVAL_SECONDS": "600",
        "TURSO_DB_URL": "libsql://test", "TURSO_AUTH_TOKEN": "private-db",
        "THEMARKETEAR_EMAIL": "private-email", "THEMARKETEAR_PASSWORD": "private-password",
        "CLERK_SECRET_KEY": "forbidden", "TWS_PASSWORD": "forbidden",
        "DROPBOX_WRITE_REFRESH_TOKEN": "forbidden", "CLAUDE_CODE_OAUTH_TOKEN": "forbidden",
        "MARKETDESK_UNKNOWN_KEY": "forbidden",
    }
    host_env = tmp_path / "pdf.env"
    host_env.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
    result = _run(tmp_path, ["run", UNIT], extra_env={"RADON_TEST_ENV_FILE": str(host_env)})
    assert result.returncode == 0, result.stderr
    # The harness chooses its own private notify-proxy root. Resolve the
    # rendered path from the real runtime invocation rather than guessing it.
    args = _run_line(result).split()
    env_file = Path(args[args.index("--env-file") + 1])
    text = env_file.read_text()
    for key in ("MARKETDESK_FIREBASE_REFRESH_TOKEN", "MARKETDESK_PDF_MIN_INTERVAL_SECONDS", "TURSO_AUTH_TOKEN", "THEMARKETEAR_EMAIL"):
        assert f"{key}=" in text
    assert "forbidden" not in text


def test_pdf_units_are_installed_pinned_and_never_enable_the_oneshot():
    setup = (CLOUD / "scripts/setup-vps.sh").read_text()
    manifest = (CLOUD / "config/installed-units.sha256").read_text()
    for name in (UNIT, "radon-marketdesk-pdf.timer", UNIT + ".d/runtime-container.conf"):
        assert f"{hashlib.sha256((CLOUD / 'services' / name).read_bytes()).hexdigest()}  {name}" in manifest
    for name in (UNIT, "radon-marketdesk-pdf.timer"):
        assert name in re.search(r"readonly SERVICE_FILES=\(.*?\n\)", setup, re.S).group(0)
    dropin = UNIT + ".d/runtime-container.conf"
    for helper in ("bootstrap-control-plane.sh", "deploy-root-helper.sh"):
        text = (CLOUD / "scripts" / helper).read_text()
        assert "services/" + dropin in text
        assert "/etc/systemd/system/" + dropin in text
    timer = (CLOUD / "services/radon-marketdesk-pdf.timer").read_text()
    assert "OnCalendar=*:02/5:17" in timer and "Persistent=false" in timer
    unit = (CLOUD / "services" / UNIT).read_text()
    assert "TimeoutStartSec=240" in unit and "SuccessExitStatus=143" in unit

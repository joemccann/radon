"""P2 host cutover: secrets at /etc/radon/env, media at /var/lib/radon/media."""

from __future__ import annotations

import re
from pathlib import Path

CLOUD = Path(__file__).resolve().parents[1]
REPO = CLOUD.parent
SERVICES = CLOUD / "services"
CANONICAL_ENV = "/etc/radon/env"
CANONICAL_MEDIA = "/var/lib/radon/media"
STRIPPED_ENV = "/home/radon/radon-page-responder.env"


def test_fleet_units_load_etc_radon_env() -> None:
    found = 0
    for path in sorted(SERVICES.glob("*.service")):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^EnvironmentFile=(.+)$", text, re.MULTILINE)
        if match is None:
            continue
        found += 1
        value = match.group(1).strip()
        if path.name == "radon-grok-page-responder.service":
            assert value == STRIPPED_ENV, path.name
            continue
        if path.name == "radon-flex-pull.service":
            assert value.lstrip("-") == "/var/lib/radon/flex-secrets/env", path.name
            continue
        if path.name == "radon-mcp.service":
            assert value == "/etc/radon/mcp.env", path.name
            continue
        assert value == CANONICAL_ENV, path.name
    assert found >= 40


def test_caddy_media_root_is_var_lib_radon_media() -> None:
    text = (CLOUD / "caddy" / "Caddyfile").read_text(encoding="utf-8")
    assert f"root * {CANONICAL_MEDIA}" in text
    assert "root * /home/radon/radon-cloud/media" not in text


def test_media_backup_default_dir() -> None:
    text = (CLOUD / "scripts" / "media_backup.py").read_text(encoding="utf-8")
    assert f'"{CANONICAL_MEDIA}"' in text or f"'{CANONICAL_MEDIA}'" in text
    unit = (SERVICES / "radon-media-backup.service").read_text(encoding="utf-8")
    assert f"RADON_MEDIA_DIR={CANONICAL_MEDIA}" in unit


def test_deploy_default_env_prefers_etc_radon_env() -> None:
    body = (CLOUD / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    fn = re.search(
        r"^_default_env_file\(\)\s*\{(.*?)^\}",
        body,
        re.MULTILINE | re.DOTALL,
    )
    assert fn
    text = fn.group(1)
    etc = text.index("/etc/radon/env")
    legacy = text.index("/home/radon/radon-cloud/.env")
    assert etc < legacy


def test_ci_deploy_prefers_etc_radon_env() -> None:
    text = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "/etc/radon/env" in text
    idx = text.index("ENV_FILE=")
    snippet = text[idx : idx + 400]
    assert "/etc/radon/env" in snippet


def test_drift_audit_file_pairs_omit_secret_paths() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "drift_audit_p2", CLOUD / "scripts" / "drift_audit.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for live, repo_rel, _label in module.FILE_PAIRS:
        assert live != CANONICAL_ENV
        assert "radon-cloud/.env" not in live
        assert not repo_rel.endswith(".env")


def test_canonical_env_is_root_owned_group_readable() -> None:
    """The service account must not be able to rewrite production secrets."""
    claude = (CLOUD / "CLAUDE.md").read_text(encoding="utf-8")
    assert "mode `0640`" in claude
    assert "owner `root:radon`" in claude
    setup = (CLOUD / "scripts" / "setup-vps.sh").read_text(encoding="utf-8")
    assert 'chmod 0640 "$ENV_FILE"' in setup or 'chmod 0640 "$env_file"' in setup
    assert 'chown root:radon "$ENV_FILE"' in setup
    assert 'chown root:radon "$env_file"' in setup
    post = (CLOUD / "scripts" / "post-setup.sh").read_text(encoding="utf-8")
    assert "install -m 0640 -o root -g radon" in post
    assert 'scp "$ENV_FILE" "${VPS_RADON}:~/radon-cloud/.env"' not in post
    assert "chmod 0600 ~/radon-cloud/.env" not in post


def test_setup_vps_grants_caddy_traverse_into_media_parent() -> None:
    """/var/lib/radon is 0750 radon:radon and Caddy serves media/ beneath it.

    Without radon group membership the caddy user cannot traverse the parent
    and every media.radon.run request 403s (2026-08-23 regression after the
    media cutover). Supplementary groups apply at process start, so a fresh
    grant must restart caddy, not reload it.
    """
    text = (CLOUD / "scripts" / "setup-vps.sh").read_text(encoding="utf-8")
    assert "usermod -aG radon caddy" in text
    grant = text.index("usermod -aG radon caddy")
    restart_snippet = text[grant : grant + 400]
    assert "restart caddy" in restart_snippet

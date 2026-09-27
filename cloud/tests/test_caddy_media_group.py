"""DS-2026-09-25-06: Caddy serves media without group radon.

Caddy used to be in group radon so it could traverse 0750 /var/lib/radon
into media/. That also let it read /etc/radon/env (0640 root:radon).
Media access now goes through dedicated group radon-media plus a traverse
ACL on the parent; caddy is removed from radon.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

CLOUD = Path(__file__).resolve().parents[1]
SETUP = CLOUD / "scripts" / "setup-vps.sh"
HELPER = CLOUD / "scripts" / "deploy-root-helper.sh"
WIPE = CLOUD / "scripts" / "wipe-vps.sh"
PLAYBOOK = CLOUD.parent / "docs" / "security-audit-playbook.md"

FORBIDDEN_GRANT = "usermod -aG radon caddy"


def _function_body(script: str, name: str) -> str:
    match = re.search(
        rf"^{name}\(\)\s*\{{\s*\n(.+?)\n\}}\s*$",
        script,
        re.DOTALL | re.MULTILINE,
    )
    assert match, f"{name}() not found"
    return match.group(1)


def _code_lines(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def test_no_cloud_script_adds_caddy_to_radon_group() -> None:
    hits: list[str] = []
    for path in sorted((CLOUD / "scripts").glob("*.sh")):
        text = _code_lines(path.read_text(encoding="utf-8"))
        if FORBIDDEN_GRANT in text:
            hits.append(path.name)
    assert hits == [], f"caddy must not be added to group radon: {hits}"


def test_setup_creates_radon_media_group_and_drops_caddy_from_radon() -> None:
    text = SETUP.read_text(encoding="utf-8")
    assert FORBIDDEN_GRANT not in _code_lines(text)
    assert "groupadd --system radon-media" in text
    assert "usermod -aG radon-media caddy" in text
    assert "gpasswd -d caddy radon" in text
    grant = _function_body(text, "grant_caddy_media_access")
    assert "g:radon-media" in grant
    assert 'chown --no-dereference radon:radon-media "$media"' in grant
    assert 'chmod 0640 "$env_file"' in grant
    assert 'chown root:radon "$env_file"' in grant


def test_setup_media_dir_is_owned_by_radon_media() -> None:
    body = _function_body(
        SETUP.read_text(encoding="utf-8"), "create_etc_radon_dir"
    )
    assert 'chown --no-dereference radon:radon-media "$media"' in body
    assert 'chown --no-dereference radon:radon "$media"' not in body


def test_install_caddy_restarts_after_media_group_grant() -> None:
    """Supplementary groups apply at process start; a fresh grant needs restart."""
    text = SETUP.read_text(encoding="utf-8")
    body = _function_body(text, "install_caddy")
    assert "grant_caddy_media_access" in body
    grant = body.index("grant_caddy_media_access")
    assert "restart caddy" in body[grant:]


def test_publish_caddy_migrates_media_group_on_existing_hosts() -> None:
    text = HELPER.read_text(encoding="utf-8")
    assert FORBIDDEN_GRANT not in _code_lines(text)
    assert "groupadd --system radon-media" in text
    assert "usermod -aG radon-media caddy" in text
    assert "gpasswd -d caddy radon" in text
    grant = _function_body(text, "grant_caddy_media_access")
    assert "g:radon-media" in grant
    assert 'chown --no-dereference radon:radon-media "$media"' in grant
    publish = _function_body(text, "publish_caddy")
    assert "grant_caddy_media_access" in publish
    assert publish.index("grant_caddy_media_access") > publish.index(
        'mv -f -- "$candidate" "$CADDY_CONFIG"'
    )


def test_wipe_vps_removes_radon_media_group() -> None:
    text = WIPE.read_text(encoding="utf-8")
    assert "groupdel radon-media" in text


def test_playbook_records_caddy_outside_radon_group() -> None:
    text = PLAYBOOK.read_text(encoding="utf-8")
    assert "`caddy` is NOT in the radon group" in text
    assert "radon-media" in text


def _grant_harness(tmp_path: Path) -> tuple[Path, Path, Path, Path, dict[str, str]]:
    """Returns (media, env_file, cmd_log, fake_bin, env)."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    cmd_log = tmp_path / "cmds"
    cmd_log.write_text("", encoding="utf-8")

    parent = tmp_path / "var" / "lib" / "radon"
    media = parent / "media"
    media.mkdir(parents=True)
    (media / "img.png").write_bytes(b"x")
    parent.chmod(0o750)
    media.chmod(0o750)

    env_file = tmp_path / "etc" / "radon" / "env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("TURSO_AUTH_TOKEN=secret\n", encoding="utf-8")
    env_file.chmod(0o666)

    def stub(name: str, body: str) -> None:
        path = fake_bin / name
        path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    quoted_log = str(cmd_log)
    stub("chmod", f'printf "chmod %s\\n" "$*" >> {quoted_log}\nexec /bin/chmod "$@"\n')
    stub(
        "chown",
        "spec=$1\n"
        'if [ "$spec" = -R ] || [ "$spec" = --no-dereference ]; then spec=$2; fi\n'
        f'printf "chown %s\\n" "$*" >> {quoted_log}\n'
        "exit 0\n",
    )
    stub(
        "setfacl",
        f'printf "setfacl %s\\n" "$*" >> {quoted_log}\nexit 0\n',
    )
    stub(
        "groupadd",
        f'printf "groupadd %s\\n" "$*" >> {quoted_log}\nexit 0\n',
    )
    stub(
        "usermod",
        f'printf "usermod %s\\n" "$*" >> {quoted_log}\nexit 0\n',
    )
    stub(
        "gpasswd",
        f'printf "gpasswd %s\\n" "$*" >> {quoted_log}\nexit 0\n',
    )
    stub(
        "getent",
        'if [ "$1" = group ] && [ "$2" = radon-media ]; then exit 1; fi\nexit 0\n',
    )
    stub(
        "id",
        'if [ "$1" = caddy ]; then exit 0; fi\n'
        'if [ "$1" = -nG ] && [ "$2" = caddy ]; then printf "caddy radon www-data\\n"; exit 0; fi\n'
        "exec /usr/bin/id \"$@\"\n",
    )
    # Drops to the media owner in production; here it runs the command as-is.
    stub(
        "setpriv",
        f'printf "setpriv %s\\n" "$*" >> {quoted_log}\n'
        'while [ "$1" != -- ]; do shift; done; shift\n'
        'exec "$@"\n',
    )
    for noop in ("sudo", "install", "bun", "systemctl"):
        stub(noop, "exit 0\n")

    env = {
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "RADON_SETUP_SOURCE_ONLY": "1",
        "RADON_MEDIA_PARENT": str(parent),
        "RADON_MEDIA_DIR": str(media),
        "RADON_CANONICAL_ENV_FILE": str(env_file),
        "RADON_HELPER_SKIP_CHOWN": "0",
        "RADON_SYSTEM_PYTHON": sys.executable,
    }
    return media, env_file, cmd_log, fake_bin, env


def test_grant_caddy_media_access_drops_radon_and_locks_env(tmp_path: Path) -> None:
    media, env_file, cmd_log, _bin, env = _grant_harness(tmp_path)
    proc = subprocess.run(
        [
            "bash",
            "-c",
            f"set -uo pipefail\nsource {SETUP}\ngrant_caddy_media_access\n"
            'printf "restart=%s\\n" "${GRANT_CADDY_NEEDS_RESTART:-unset}"\n',
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    cmds = cmd_log.read_text(encoding="utf-8")
    assert "usermod -aG radon caddy" not in cmds
    assert "usermod -aG radon-media caddy" in cmds
    assert "gpasswd -d caddy radon" in cmds
    assert "groupadd --system radon-media" in cmds
    assert "chown --no-dereference radon:radon-media" in cmds
    assert "g:radon-media" in cmds
    assert "chown root:radon" in cmds
    assert oct(env_file.stat().st_mode & 0o7777) == "0o640"
    assert "restart=1" in proc.stdout
    assert media.is_dir()


def test_grant_caddy_media_access_is_idempotent_when_already_migrated(
    tmp_path: Path,
) -> None:
    media, env_file, cmd_log, fake_bin, env = _grant_harness(tmp_path)
    (fake_bin / "getent").write_text(
        "#!/bin/bash\n"
        'if [ "$1" = group ] && [ "$2" = radon-media ]; then exit 0; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "id").write_text(
        "#!/bin/bash\n"
        'if [ "$1" = caddy ]; then exit 0; fi\n'
        'if [ "$1" = -nG ] && [ "$2" = caddy ]; then '
        'printf "caddy radon-media www-data\\n"; exit 0; fi\n'
        'exec /usr/bin/id "$@"\n',
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            "bash",
            "-c",
            f"set -uo pipefail\nsource {SETUP}\ngrant_caddy_media_access\n"
            'printf "restart=%s\\n" "${GRANT_CADDY_NEEDS_RESTART:-unset}"\n',
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    cmds = cmd_log.read_text(encoding="utf-8")
    assert "usermod -aG radon-media caddy" not in cmds
    assert "gpasswd -d caddy radon" not in cmds
    assert "groupadd --system radon-media" not in cmds
    assert "restart=0" in proc.stdout
    assert oct(env_file.stat().st_mode & 0o7777) == "0o640"
    assert media.is_dir()


def _grant_script(script: Path) -> str:
    """Run grant_caddy_media_access from either copy (setup-vps / root helper)."""
    if script == SETUP:
        return f"source {script}\n"
    text = script.read_text(encoding="utf-8")
    funcs = [
        f"{name}() {{\n{_function_body(text, name)}\n}}\n"
        for name in ("chmod_dir_nofollow", "grant_caddy_media_access")
        if re.search(rf"^{name}\(\)", text, re.MULTILINE)
    ]
    return 'SESSION_PYTHON="$RADON_SYSTEM_PYTHON"\nSETPRIV=setpriv\n' + "".join(funcs)


@pytest.mark.parametrize("script", [SETUP, HELPER], ids=["setup", "helper"])
def test_grant_never_follows_a_media_dir_swapped_mid_grant(
    tmp_path: Path, script: Path
) -> None:
    """DS-2026-09-27-01: radon owns /var/lib/radon, so it can swap media/ for
    a link after the -d/-L check. Root must not chmod the link target, and
    every ACL change inside media/ must run as radon, the owner."""
    media, _env_file, cmd_log, fake_bin, env = _grant_harness(tmp_path)
    victim = tmp_path / "root-only"
    victim.mkdir()
    victim.chmod(0o700)
    real_media = tmp_path / "real-media"
    quoted_log = str(cmd_log)
    (fake_bin / "setfacl").write_text(
        "#!/bin/bash\n"
        f'printf "setfacl %s\\n" "$*" >> {quoted_log}\n'
        f"if [ ! -L {media} ]; then /bin/mv {media} {real_media}; "
        f"/bin/ln -s {victim} {media}; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )

    subprocess.run(
        ["bash", "-c", f"set -uo pipefail\n{_grant_script(script)}grant_caddy_media_access\n"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert oct(victim.stat().st_mode & 0o7777) == "0o700"
    lines = cmd_log.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("setfacl") and line.rstrip().endswith(str(media)):
            previous = lines[index - 1] if index else ""
            if not (previous.startswith("setpriv ") and previous.endswith(line[len("setfacl "):])):
                pytest.fail(f"root setfacl on a radon-owned name: {line}")
    owner_calls = [line for line in lines if line.startswith("setpriv ")]
    assert owner_calls and all("--reuid=radon" in line for line in owner_calls)


# A second fixture for the same root cause (security loop, cherry-picked
# first): media/ is swapped on the FIRST setfacl, i.e. right after the -d/-L
# check and before any media operation, for a populated root-only target.


def _swap_on_first_setfacl(fake_bin: Path, media: Path, victim: Path, log: Path) -> None:
    moved = media.with_name("media.real")
    (fake_bin / "setfacl").write_text(
        "#!/bin/bash\n"
        f'printf "setfacl %s\\n" "$*" >> {log}\n'
        f"if [ ! -e {moved} ]; then /bin/mv {media} {moved}; /bin/ln -s {victim} {media}; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("script", [SETUP, HELPER], ids=["setup-vps", "root-helper"])
def test_grant_does_not_follow_media_swapped_for_a_link_after_the_check(
    tmp_path: Path, script: Path
) -> None:
    media, _env_file, cmd_log, fake_bin, env = _grant_harness(tmp_path)
    victim = tmp_path / "root-only"
    victim.mkdir()
    secret = victim / "secret"
    secret.write_text("x", encoding="utf-8")
    secret.chmod(0o600)
    victim.chmod(0o700)
    _swap_on_first_setfacl(fake_bin, media, victim, cmd_log)
    subprocess.run(
        ["bash", "-c", f"set -uo pipefail\n{_grant_script(script)}grant_caddy_media_access\n"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert media.is_symlink(), "fixture did not swap media/ after the check"
    assert oct(victim.stat().st_mode & 0o7777) == "0o700"
    assert oct(secret.stat().st_mode & 0o7777) == "0o600"
    lines = cmd_log.read_text(encoding="utf-8").splitlines()
    assert not [line for line in lines if line.startswith("chmod ") and str(media) in line]
    parent = str(media.parent)
    on_media = [
        index for index, line in enumerate(lines)
        if line.startswith("setfacl ") and not line.endswith(f" {parent}")
    ]
    assert on_media, lines
    for index in on_media:
        previous = lines[index - 1] if index else ""
        assert previous.startswith("setpriv --reuid=radon "), lines[index]
        assert previous.endswith(lines[index][len("setfacl "):]), lines[index]


@pytest.mark.parametrize("script", [SETUP, HELPER], ids=["setup-vps", "root-helper"])
def test_chmod_dir_nofollow_refuses_a_link(tmp_path: Path, script: Path) -> None:
    victim = tmp_path / "root-only"
    victim.mkdir()
    victim.chmod(0o700)
    link = tmp_path / "media"
    link.symlink_to(victim)
    real = tmp_path / "real"
    real.mkdir()
    real.chmod(0o700)
    proc = subprocess.run(
        [
            "bash",
            "-c",
            f"{_grant_script(script)}"
            f'chmod_dir_nofollow {link} 2750 && rc=0 || rc=$?\nprintf "link_rc=%s\\n" "$rc"\n'
            f'chmod_dir_nofollow {real} 2750 && rc=0 || rc=$?\nprintf "real_rc=%s\\n" "$rc"\n',
        ],
        env={**os.environ, "RADON_SETUP_SOURCE_ONLY": "1", "RADON_SYSTEM_PYTHON": sys.executable},
        capture_output=True,
        text=True,
    )
    assert "link_rc=0" not in proc.stdout and "link_rc=" in proc.stdout, proc.stdout + proc.stderr
    assert oct(victim.stat().st_mode & 0o7777) == "0o700"
    assert "real_rc=0" in proc.stdout, proc.stdout + proc.stderr
    assert oct(real.stat().st_mode & 0o7777) == "0o2750"


@pytest.mark.parametrize("script", [SETUP, HELPER], ids=["setup-vps", "root-helper"])
def test_root_never_mutates_media_by_path_or_walks_it_recursively(script: Path) -> None:
    """chmod and setfacl follow links: root changes media/ only through
    chmod_dir_nofollow, and every ACL change inside it (physical -R -P walks
    included) runs as radon via setpriv."""
    text = script.read_text(encoding="utf-8")
    grant = _function_body(text, "grant_caddy_media_access")
    assert re.search(r'as_owner=\("?\$?\{?SETPRIV\}?"? --reuid=radon|as_owner=\(setpriv --reuid=radon', grant), grant
    for line in _code_lines(text).splitlines():
        stripped = line.strip()
        if re.match(r"chmod\s", stripped):
            assert '"$media"' not in stripped, stripped
        if "setfacl" in stripped and '"$media"' in stripped:
            assert stripped.startswith('"${as_owner[@]}" setfacl '), stripped
        if "setfacl -R" in stripped:
            assert stripped.startswith('"${as_owner[@]}" setfacl -R -P '), stripped

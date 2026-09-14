"""`wipe-vps.sh` must actually clear the surfaces `setup-vps.sh` creates.

The script advertises a destructive reset ("Removes: all radon services,
repos, packages, user data") and closes by declaring the machine ready for a
fresh setup. Anything it silently keeps is a credential or privilege surface
left behind on a host the operator believes is clean -- typically one about to
be handed back to the provider, re-imaged, or re-bootstrapped from a different
account.

These assertions are deliberately path-level and read the two scripts as text:
the wipe path cannot be exercised anywhere but a disposable host.
"""

from __future__ import annotations

import fnmatch
import pathlib
import re

CLOUD_ROOT = pathlib.Path(__file__).resolve().parents[1]
WIPE = CLOUD_ROOT / "scripts" / "wipe-vps.sh"
SETUP = CLOUD_ROOT / "scripts" / "setup-vps.sh"
BOOTSTRAP = CLOUD_ROOT / "scripts" / "bootstrap-control-plane.sh"


def _body(path: pathlib.Path) -> str:
    """The script's executable lines, without comments or banner echoes."""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"^echo\b", stripped):
            continue
        lines.append(stripped)
    return "\n".join(lines)


def _token_covers(token: str, target: str) -> bool:
    """`token` (a literal path or shell glob) is `target` or a parent of it."""
    token = token.rstrip("/")
    if fnmatch.fnmatchcase(target, token):
        return True
    parent = target
    while "/" in parent.lstrip("/"):
        parent = parent.rsplit("/", 1)[0]
        if fnmatch.fnmatchcase(parent, token):
            return True
    return False


def _removal_lines(body: str, target: str) -> list[str]:
    """The `rm` / `shred` / `find ... -delete|shred` lines that reach `target`, in order."""
    hits = []
    for line in body.splitlines():
        if not re.match(r"^(rm|shred|find)\b", line):
            continue
        if line.startswith("find") and not re.search(r"-delete|shred", line):
            continue
        for token in line.split():
            token = token.strip("\"'")
            if token.startswith("/") and _token_covers(token, target):
                hits.append(line)
                break
    return hits


def _removes(body: str, target: str) -> bool:
    """True when the script removes `target` (or a directory containing it)."""
    return bool(_removal_lines(body, target))


def test_wipe_removes_the_secret_directories_setup_creates():
    """/etc/radon holds every production credential the units load."""
    body = _body(WIPE)
    missing = [
        target
        # Not /etc/credstore.encrypted itself: that store is systemd-wide and
        # may hold credentials this deployment did not put there.
        for target in (
            "/etc/radon",
            "/var/lib/radon",
            "/etc/credstore.encrypted/radon-secret-store-key",
        )
        if not _removes(body, target)
    ]
    assert not missing, (
        "wipe-vps.sh advertises a destructive reset but leaves these credential "
        f"and state surfaces on disk: {missing}"
    )


def test_wipe_removes_every_privilege_grant_setup_installs():
    """A leftover sudoers or polkit grant survives the account it was for."""
    body = _body(WIPE)
    grants = [
        f"/etc/sudoers.d/{path.name}"
        for path in sorted((CLOUD_ROOT / "config" / "sudoers.d").glob("radon-*"))
    ]
    grants += [
        f"/etc/polkit-1/rules.d/{path.name}"
        for path in sorted((CLOUD_ROOT / "config" / "polkit").glob("*.rules"))
    ]
    missing = [target for target in grants if not _removes(body, target)]
    assert not missing, (
        "wipe-vps.sh leaves privilege grants installed by setup-vps.sh in "
        f"place: {missing}"
    )


def test_setup_has_not_added_a_secret_path_the_wipe_does_not_know_about():
    """Catches the next /etc/radon-shaped path before it outlives a wipe."""
    setup = SETUP.read_text(encoding="utf-8")
    body = _body(WIPE)
    referenced = {
        match
        for match in re.findall(r"/etc/(?:radon|credstore\.encrypted)[\w./-]*", setup)
    }
    # /etc/credstore.encrypted is the SHARED systemd credential store, not ours:
    # the wipe removes this deployment's key from inside it and rmdirs the
    # directory only if that left it empty. A path is therefore also covered
    # when the wipe removes something beneath it.
    def _covered(target: str) -> bool:
        if _removes(body, target):
            return True
        return any(
            other.startswith(target.rstrip("/") + "/") and _removes(body, other)
            for other in referenced
        )

    unwiped = sorted(target for target in referenced if not _covered(target))
    assert not unwiped, (
        "setup-vps.sh creates or reads these secret paths and wipe-vps.sh does "
        f"not remove them: {unwiped}"
    )


def test_wipe_shreds_secret_files_before_unlinking_them():
    """An unlink leaves the bytes on disk; a shred after an unlink is dead code."""
    body = _body(WIPE)
    wrong = []
    for target in (
        "/etc/radon",
        "/etc/credstore.encrypted/radon-secret-store-key",
    ):
        lines = _removal_lines(body, target)
        if not lines or "shred" not in lines[0]:
            wrong.append((target, lines[:1]))
    assert not wrong, (
        "wipe-vps.sh must shred these secret surfaces BEFORE unlinking them; "
        f"first removal seen: {wrong}"
    )


def test_wipe_removes_the_control_plane_bootstrap_installs():
    """Root helpers and drop-ins outlive the account; a wiped host must not run stale root code."""
    body = _body(WIPE)
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    installed = sorted(
        set(
            re.findall(
                r"/(?:usr/local/(?:s?bin|lib)|etc/systemd/system)/radon[\w./-]*",
                bootstrap,
            )
        )
    )
    assert installed, "bootstrap-control-plane.sh no longer lists installed artifacts"
    targets = installed + [
        "/etc/systemd/system/radon-.service.d",
        "/etc/systemd/journald.conf.d/radon.conf",
    ]
    missing = [target for target in targets if not _removes(body, target)]
    assert not missing, (
        "wipe-vps.sh leaves control-plane artifacts installed by "
        f"bootstrap-control-plane.sh in place: {missing}"
    )
    assert re.search(r"^groupdel\b.*\bradon-secrets\b", body, re.M), (
        "wipe-vps.sh leaves the radon-secrets system group behind"
    )


def test_wipe_removes_host_state_and_private_corpus():
    body = _body(WIPE)
    missing = [
        target
        for target in (
            "/var/lib/radon",
            "/var/lib/radon-private",
            "/run/radon-app-runtime",
            "/run/radon-deploy-root.lock",
        )
        if not _removes(body, target)
    ]
    assert not missing, f"wipe-vps.sh leaves host state on disk: {missing}"


def test_wipe_removes_caddy_and_the_apt_sources_setup_adds():
    body = _body(WIPE)
    setup = SETUP.read_text(encoding="utf-8")
    sources = sorted(
        set(
            re.findall(
                r"/(?:etc/apt/keyrings|usr/share/keyrings|etc/apt/sources\.list\.d)/[\w.-]+",
                setup,
            )
        )
    )
    assert sources, "setup-vps.sh no longer adds apt keyrings or sources"
    targets = sources + ["/etc/caddy", "/var/lib/caddy"]
    missing = [target for target in targets if not _removes(body, target)]
    assert not missing, f"wipe-vps.sh leaves package sources or Caddy state behind: {missing}"

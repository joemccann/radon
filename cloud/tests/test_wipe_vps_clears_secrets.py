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

import pathlib
import re

CLOUD_ROOT = pathlib.Path(__file__).resolve().parents[1]
WIPE = CLOUD_ROOT / "scripts" / "wipe-vps.sh"
SETUP = CLOUD_ROOT / "scripts" / "setup-vps.sh"


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


def _removes(body: str, target: str) -> bool:
    """True when the script removes `target` (or a directory containing it)."""
    for line in body.splitlines():
        if not re.match(r"^(rm|shred)\b", line):
            continue
        for token in line.split():
            token = token.strip("\"'")
            if not token.startswith("/"):
                continue
            if target == token or target.startswith(f"{token.rstrip('/')}/"):
                return True
    return False


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

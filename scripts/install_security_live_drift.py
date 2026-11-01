#!/usr/bin/env python3
"""Install the weekly read-only live drift check as an operator LaunchAgent.

Runs as the operator, never root and never `_radonbot`: the check needs the
operator's admin `gh` login and BatchMode root ssh to the app host, and its
read-only Tailscale credential must not sit in the bot's home, where every
nightly agent can read it (docs/runner.md "Safety boundary"). Same shape as
scripts/install_unattended_release.py: an immutable release snapshot, a
`current` symlink, a 0600 plist. Nothing is loaded unless `--activate`.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

LABEL = "com.radon.security-live-drift"
FILES = ("scripts/security_live_drift.py", "docs/security-live-baseline.json", "cloud/tailscale/policy.hujson")
# Sunday 15:17 local: outside trading hours, after the Saturday nightly loops.
SCHEDULE = {"Weekday": 0, "Hour": 15, "Minute": 17}


def install(*, source_root: Path, destination: Path, home: Path, python: Path, activate: bool = False) -> Path:
    uid = os.getuid()
    if uid == 0:
        raise PermissionError("install as the operator, never root")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.is_symlink() or destination.stat().st_uid != uid:
        raise PermissionError(f"untrusted destination: {destination}")
    release = destination / "releases" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    release.mkdir(mode=0o700, parents=True)
    for rel in FILES:
        source = source_root / rel
        if source.is_symlink():
            raise PermissionError(f"source is a symlink: {source}")
        target = release / rel
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o400)
    current = destination / "current"
    temporary = destination / ".current-next"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(release)
    os.replace(temporary, current)

    env = {"HOME": str(home), "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
           # Paths only, never values. A missing file reports operator_required.
           "TAILSCALE_OAUTH_CLIENT_FILE": str(destination / "tailscale-oauth.env"),
           "RADON_LIVE_DRIFT_GH_TOKEN_FILE": str(destination / "github-token")}
    config = {
        "Label": LABEL,
        "ProgramArguments": [str(python), "-I", str(current / "scripts/security_live_drift.py"),
                             "--repo-root", str(current), "--out-dir", str(destination / "reports")],
        "EnvironmentVariables": env,
        "StartCalendarInterval": dict(SCHEDULE),
        "ProcessType": "Background",
        "StandardOutPath": str(destination / "launchd.log"),
        "StandardErrorPath": str(destination / "launchd.log"),
    }
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    plist = agents / f"{LABEL}.plist"
    if plist.is_symlink():
        raise PermissionError("LaunchAgent plist is a symlink")
    plist.write_bytes(plistlib.dumps(config))
    plist.chmod(0o600)
    subprocess.run(["/usr/bin/plutil", "-lint", str(plist)], check=True, capture_output=True)
    if activate:
        subprocess.run(["/bin/launchctl", "bootout", f"gui/{uid}/{LABEL}"], capture_output=True)
        subprocess.run(["/bin/launchctl", "bootstrap", f"gui/{uid}", str(plist)], check=True, capture_output=True)
    return plist


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--destination", type=Path, default=Path.home() / ".local/share/radon-security-live-drift")
    parser.add_argument("--activate", action="store_true", help="bootstrap the LaunchAgent into gui/<uid>")
    args = parser.parse_args()
    print(install(source_root=Path(__file__).resolve().parents[1], destination=args.destination,
                  home=Path.home(), python=Path(sys.executable), activate=args.activate))


if __name__ == "__main__":
    main()

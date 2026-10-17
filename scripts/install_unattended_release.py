#!/usr/bin/env python3
"""Install the operator-owned release LaunchAgent without granting the bot sudo."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

# The installer may itself run with -I; import only its reviewed source tree.
sys.path.insert(0, str(Path(__file__).resolve().parent))

LOOPS = ("security/", "security-deepsec/", "reliability/", "testing/", "documentation/", "ci-performance/", "performance/", "security-private-release/")
CHECKS = ("Secret scan (gitleaks)", "PR body template", "Path filter", "Vitest coverage ratchet", "pytest coverage ratchet", "pytest (cloud al)", "pytest (cloud edge)", "Perimeter smoke (next start + curl)", *(f"Vitest (shard {i}/8)" for i in range(1, 9)), *(f"pytest ({shard})" for shard in ("scripts-ac", "scripts-df", "scripts-i", "scripts-gh", "scripts-jm", "scripts-npsz", "scripts-rs", "scripts-daemons", "rest")))


def install(*, source: Path, destination: Path, home: Path, python: Path, notifications: Path, activate=False):
    from unattended_release import _trusted
    uid = os.getuid()
    for path in (source, notifications):
        _trusted(path, uid)
    if uid == 0:
        raise PermissionError("install as the operator, never root or the runner")
    _trusted(destination.parent, uid, directory=True)
    destination.mkdir(mode=0o700, exist_ok=True)
    _trusted(destination, uid, directory=True)
    releases = destination / "releases"
    releases.mkdir(mode=0o700, exist_ok=True)
    release = releases / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    release.mkdir(mode=0o700)
    shutil.copyfile(source, release / "unattended_release.py")
    private_source = source.with_name("private_security_release.py")
    _trusted(private_source, uid)
    shutil.copyfile(private_source, release / "private_security_release.py")
    (release / "private_security_release.py").chmod(0o500)
    refresh_source = source.with_name("operator_runtime_refresh.py")
    _trusted(refresh_source, uid)
    shutil.copyfile(refresh_source, release / "operator_runtime_refresh.py")
    (release / "operator_runtime_refresh.py").chmod(0o500)
    probe_source = source.with_name("codex_capability_probe.py")
    _trusted(probe_source, uid)
    shutil.copyfile(probe_source, release / "codex_capability_probe.py")
    (release / "codex_capability_probe.py").chmod(0o500)
    validator_source = source.parent / "ci/check_pr_body.py"
    _trusted(validator_source, uid)
    (release / "ci").mkdir(mode=0o700)
    shutil.copyfile(validator_source, release / "ci/check_pr_body.py")
    (release / "ci/check_pr_body.py").chmod(0o500)
    (release / "unattended_release.py").chmod(0o500)
    policy = destination / "policy.json"
    if not policy.exists():
        policy.write_text(json.dumps({"repository": "joemccann/radon", "authors": ["radon-runner-bot", "joemccann"], "branch_prefixes": LOOPS, "required_checks": CHECKS, "reviewers": ["gpt-6.1-sol", "gpt-6-luna"], "hosts": ["app", "broker"]}, indent=2) + "\n")
        policy.chmod(0o600)
    hosts = destination / "hosts.json"
    if not hosts.exists():
        hosts.write_text(json.dumps({"app": "radon-app", "broker": "radon-broker"}) + "\n")
        hosts.chmod(0o600)
    credentials = destination / "notifications.env"
    if not credentials.exists():
        values = {}
        for line in notifications.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() in ("PUSHOVER_USER", "PUSHOVER_TOKEN"):
                values[key.strip()] = value.strip()
        if set(values) != {"PUSHOVER_USER", "PUSHOVER_TOKEN"} or any(not value for value in values.values()):
            raise ValueError("Pushover credentials missing")
        fd = os.open(credentials, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    # Preserve standing authority and credentials across code upgrades.
    for path in (policy, hosts, credentials):
        _trusted(path, uid)
    current = destination / "current"
    temporary = destination / ".current-next"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(release)
    os.replace(temporary, current)
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    _trusted(agents, uid, directory=True)
    label = "com.radon.operator-releases"
    plist = agents / f"{label}.plist"
    config = {"Label": label, "ProgramArguments": [str(python), "-I", str(current / "unattended_release.py"), "--policy", str(policy), "--state", str(destination / "state"), "--hosts", str(hosts), "--notifications", str(credentials)], "EnvironmentVariables": {"HOME": str(home), "PATH": "/opt/homebrew/bin:/usr/bin:/bin"}, "StartInterval": 300, "RunAtLoad": True, "ProcessType": "Background", "StandardOutPath": str(destination / "launchd.log"), "StandardErrorPath": str(destination / "launchd.log")}
    # The plist itself must never follow a link into an unrelated file.
    if plist.is_symlink():
        raise PermissionError("LaunchAgent plist is a symlink")
    plist.write_bytes(plistlib.dumps(config))
    plist.chmod(0o600)
    subprocess.run(["/usr/bin/plutil", "-lint", str(plist)], check=True, capture_output=True)
    if activate:
        subprocess.run(["/bin/launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
        subprocess.run(["/bin/launchctl", "bootstrap", f"gui/{uid}", str(plist)], check=True, capture_output=True)
    return plist


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notifications", type=Path, required=True)
    parser.add_argument("--destination", type=Path, default=Path.home() / ".local/share/radon-operator-releases")
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    import sys
    args.destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    result = install(source=Path(__file__).with_name("unattended_release.py"), destination=args.destination, home=Path.home(), python=Path(sys.executable), notifications=args.notifications, activate=args.activate)
    print(result)


if __name__ == "__main__":
    main()

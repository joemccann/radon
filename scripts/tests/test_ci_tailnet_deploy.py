"""CI deploys SSH over the tailnet, not the public app-host address.

GitHub-hosted runners have no stable public IPs, so `fw-radon-app` currently
leaves tcp/22 open to the world. Moving `stage-release`, `prepull-images`,
and `deploy` onto Tailscale (OAuth client tagged `tag:ci`, SSH to
`secrets.VPS_TAILNET_HOST`) is the prerequisite for closing that rule.

`tailscale/github-action` is not on the joemccann/radon Actions allowlist, so
a `uses:` of it fails the whole workflow at parse time (startup_failure),
including PR runs whose deploy jobs are skipped. The in-repo composite
`.github/actions/connect-tailscale` is the allowed equivalent.

These tests pin the workflow shape; they do not mint keys or join a tailnet.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
ACTION = ROOT / ".github" / "actions" / "connect-tailscale" / "action.yml"
HOST_JOBS = ("stage-release", "prepull-images", "deploy")
LOCAL_ACTION = "./.github/actions/connect-tailscale"
CHECKOUT_SHA = re.compile(
    r"^actions/checkout@[0-9a-f]{40}(?:\s+#\s+\S+)?$"
)
SSH_SHA = re.compile(
    r"^appleboy/ssh-action@[0-9a-f]{40}(?:\s+#\s+\S+)?$"
)
SHA256_LITERAL = re.compile(r'sha256="[0-9a-f]{64}"')
VERSION_LITERAL = re.compile(r'version="\d+\.\d+\.\d+"')


def _workflow() -> dict:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _ssh_steps(job: dict) -> list[tuple[int, dict]]:
    return [
        (i, step)
        for i, step in enumerate(job.get("steps") or [])
        if "ssh-action" in str(step.get("uses", ""))
    ]


def test_the_three_host_jobs_each_have_one_ssh_action() -> None:
    jobs = _workflow()["jobs"]
    for name in HOST_JOBS:
        found = _ssh_steps(jobs[name])
        assert len(found) == 1, (name, found)


def test_ci_yml_does_not_use_the_disallowed_tailscale_action() -> None:
    jobs = _workflow()["jobs"]
    for job in jobs.values():
        for step in job.get("steps") or []:
            uses = str(step.get("uses", ""))
            assert "tailscale/github-action" not in uses, uses


def test_every_host_ssh_step_is_preceded_by_the_local_tailscale_oauth_login() -> None:
    jobs = _workflow()["jobs"]
    for name in HOST_JOBS:
        steps = jobs[name]["steps"]
        idx, ssh = _ssh_steps(jobs[name])[0]
        assert idx > 0, f"{name}: ssh-action is the first step; Tailscale must run first"
        ts = steps[idx - 1]
        assert str(ts.get("uses", "")) == LOCAL_ACTION, (
            f"{name}: step before ssh-action is not the in-repo Tailscale action: "
            f"{ts.get('uses')!r}"
        )
        with_ = ts.get("with") or {}
        assert with_.get("oauth-client-id") == "${{ secrets.TS_OAUTH_CLIENT_ID }}"
        assert with_.get("oauth-secret") == "${{ secrets.TS_OAUTH_SECRET }}"
        assert with_.get("tags") == "tag:ci"


def test_host_jobs_check_out_the_local_action_before_connecting() -> None:
    jobs = _workflow()["jobs"]
    for name in HOST_JOBS:
        steps = jobs[name]["steps"]
        idx, _ = _ssh_steps(jobs[name])[0]
        assert idx >= 2, f"{name}: checkout + Tailscale must precede ssh-action"
        checkout = steps[idx - 2]
        assert CHECKOUT_SHA.match(str(checkout.get("uses", ""))), (
            f"{name}: local action requires a SHA-pinned checkout before it: "
            f"{checkout.get('uses')!r}"
        )


def test_host_ssh_targets_the_tailnet_secret_and_pins_the_host_key() -> None:
    jobs = _workflow()["jobs"]
    for name in HOST_JOBS:
        _, ssh = _ssh_steps(jobs[name])[0]
        assert SSH_SHA.match(str(ssh.get("uses", ""))), ssh.get("uses")
        with_ = ssh.get("with") or {}
        assert with_.get("host") == "${{ secrets.VPS_TAILNET_HOST }}", (
            f"{name} still SSHes to {with_.get('host')!r}"
        )
        assert "VPS_HOST" not in str(with_.get("host", ""))
        assert with_.get("username") == "radon"
        assert with_.get("key") == "${{ secrets.VPS_SSH_KEY }}"
        assert with_.get("fingerprint") == "${{ secrets.VPS_SSH_FINGERPRINT }}", (
            f"{name} does not pin the SSH host key fingerprint"
        )


def test_host_jobs_do_not_read_the_public_vps_host_secret() -> None:
    jobs = _workflow()["jobs"]
    for name in HOST_JOBS:
        dumped = yaml.safe_dump(jobs[name], default_flow_style=False)
        assert "secrets.VPS_HOST" not in dumped, name


def test_a_host_job_that_skips_tailscale_fails_the_check() -> None:
    jobs = _workflow()["jobs"]
    stage = jobs["stage-release"]
    idx, _ = _ssh_steps(stage)[0]
    steps = list(stage["steps"])
    decoy = {"uses": "actions/checkout@deadbeef", "name": "not-tailscale"}
    if idx == 0:
        steps.insert(0, decoy)
        idx = 1
    else:
        steps[idx - 1] = decoy
    ts = steps[idx - 1]
    assert str(ts.get("uses", "")) != LOCAL_ACTION


def test_connect_tailscale_action_pins_tarball_version_and_checksum() -> None:
    text = ACTION.read_text(encoding="utf-8")
    assert VERSION_LITERAL.search(text), text
    assert SHA256_LITERAL.search(text), (
        "the Tailscale tarball digest must be a reviewed literal, not fetched"
    )
    assert "sha256sum --check" in text
    assert "_checksums.txt" not in text
    assert ".sha256" not in text
    assert "releases/latest" not in text
    assert "pkgs.tailscale.com/stable/tailscale_" in text
    assert "preauthorized=true&ephemeral=true" in text
    assert "--advertise-tags=" in text
    assert "oauth-client-id" in text
    assert "oauth-secret" in text

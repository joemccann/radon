"""CIP-032: skip apt-get when the runner already has sshd.

Gateway rebuild run 44386917407 job 135094530935 spent 43.6s on
`apt-get update` then printed `openssh-server is already the newest version`.
Python rebuild 44357745454 job 134990848552 spent 66.6s on the same install.
`check_model_transport.py` only needs `/usr/sbin/sshd` as a file. GitHub
ubuntu-24.04 already ships it. Install only when the file is missing; still
run the transport check either way.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "app-images.yml"
CHECK = "cloud/scripts/check_model_transport.py"
SSHD = "/usr/sbin/sshd"


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _verify_steps() -> list[tuple[str, dict]]:
    found = []
    for job_name in ("python-image", "gateway-image"):
        step = next(
            step
            for step in _jobs()[job_name]["steps"]
            if step.get("name") == "Verify model-only SSH and read-only export boundary"
        )
        found.append((job_name, step))
    return found


def test_python_and_gateway_share_the_sshd_skip() -> None:
    bodies = [step["run"] for _, step in _verify_steps()]
    assert len(bodies) == 2
    assert bodies[0] == bodies[1]


def test_verify_skips_apt_when_sshd_is_already_a_file() -> None:
    for job_name, step in _verify_steps():
        body = step["run"]
        assert "set -euo pipefail" in body
        assert f'[ ! -f {SSHD} ]' in body, job_name
        install_at = body.index("sudo apt-get update -qq")
        guard_at = body.index(f"[ ! -f {SSHD} ]")
        check_at = body.index(CHECK)
        assert guard_at < install_at < check_at, job_name
        assert "sudo apt-get install -y --no-install-recommends openssh-server" in body
        # Unconditional apt-get update is the 43-67s miss.
        unguarded = body[: body.index("if ")]
        assert "apt-get" not in unguarded, job_name
        after_fi = body.split("fi", 1)[1]
        assert CHECK in after_fi, job_name
        assert "apt-get" not in after_fi, job_name


def test_transport_check_still_runs_unconditionally() -> None:
    for job_name, step in _verify_steps():
        body = step["run"]
        assert "python3 -I" in body
        assert CHECK in body
        assert "GITHUB_ACTIONS=true" in body
        assert "continue-on-error" not in step
        assert "if" not in str(step.get("if", "")), job_name

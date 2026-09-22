"""Private operator reports for the two security loops (2026-09-19).

The operator does not read runner logs, and rail 7 keeps findings off every
public surface, so until now a night's results were unreadable without a
shell on the Mini. Each phase's agent now writes one complete report into
the private scratch; the WRAPPER publishes it to the private repository
`joemccann/radon-security-reports` with a write-only deploy key that lives
outside the clone (the agent never sees it) and links it from the Pushover
page. These tests run the real wrappers with stubbed git/curl and read what
reached the page, what reached the private checkout, and what reached the
public dead-man.
"""

from __future__ import annotations

import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LOOPS = {
    "security": (
        REPO / "scripts" / "security_nightly.sh",
        ".security-nightly-scratch",
        ".radon-security-runner",
        REPO / ".claude" / "skills" / "security-nightly" / "SKILL.md",
    ),
    "security-deepsec": (
        REPO / "scripts" / "security_deepsec_nightly.sh",
        ".security-deepsec-scratch",
        ".radon-security-deepsec-runner",
        REPO / ".claude" / "skills" / "security-deepsec" / "SKILL.md",
    ),
}
BASH = shutil.which("bash") or "/bin/bash"
REPORTS_WEB = "https://github.com/joemccann/radon-security-reports/blob/main"
CANARY_TOKEN = "ghp_" + "A" * 36
CANARY_FINDING = "web/lib/secret.ts:12 lets an anonymous caller read /api/admin/keys"


def _uncommented(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _marker(wrapper: Path) -> str:
    return re.search(r'PHASE_COMPLETE_MARKER="([^"]+)"', wrapper.read_text(encoding="utf-8")).group(1)


def _stage(tmp_path: Path, loop: str, *, write_report: bool, with_key: bool = True):
    wrapper, scratch, marker, _skill = LOOPS[loop]
    weekend = tmp_path / "weekend"
    clone = weekend / "clone"
    (clone / "scripts").mkdir(parents=True)
    (clone / "logs" / loop).mkdir(parents=True)
    shutil.copy2(wrapper, clone / "scripts" / wrapper.name)
    (clone / "scripts" / wrapper.name).chmod(0o755)
    (clone / "scripts" / "weekend_notify.py").write_text("# stub\n", encoding="utf-8")
    for m in (".radon-weekend-runner", marker):
        (clone / m).write_text("", encoding="utf-8")
    (weekend / scratch).mkdir()
    if with_key:
        (weekend / ".security-reports-deploy-key").write_text("stub-key\n", encoding="utf-8")
    (weekend / ".env").write_text("PUSHOVER_USER=u\nPUSHOVER_TOKEN=t\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git_log = tmp_path / "git.log"
    gh_log = tmp_path / "gh.log"
    curl_cfg = tmp_path / "curl-config.log"
    report_body = (
        "# report\n\n"
        f"| F-1 | P2 | verified | {CANARY_FINDING} |\n"
        f"GITHUB_TOKEN={CANARY_TOKEN}\n"
        f"Authorization: Bearer {CANARY_TOKEN}\n"
    )
    report_line = (
        f"printf '%s' '{report_body}' > '{weekend / scratch}/latest-report-audit.md'\n"
        if write_report else ""
    )
    stubs = {
        "gh": (
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> "{gh_log}"\n'
            'if [ "$1 $2" = "issue list" ]; then echo 4242; fi\n'
            "exit 0\n"
        ),
        # The real clone step must produce a checkout; everything else is a
        # recorded no-op so the copied report stays on disk for assertions.
        "git": (
            "#!/bin/bash\n"
            f'printf "%s|%s\\n" "${{GIT_SSH_COMMAND:-}}" "$*" >> "{git_log}"\n'
            'if [ "$1" = "clone" ]; then d="${@: -1}"; mkdir -p "$d/.git"; fi\n'
            "exit 0\n"
        ),
        "python3": "#!/bin/sh\nexit 0\n",
        "claude": (
            "#!/bin/sh\n"
            "sleep 1\n"
            + report_line
            + f"echo '{_marker(wrapper)} audit run_id=stub'\n"
            "exit 0\n"
        ),
        "timeout": (
            "#!/bin/bash\n"
            'while [ $# -gt 0 ]; do\n'
            '  case "$1" in -k|--kill-after) shift 2 ;; *) shift; break ;; esac\n'
            'done\n'
            'exec "$@"\n'
        ),
    }
    for name, body in stubs.items():
        exe = bin_dir / name
        exe.write_text(body, encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    # /usr/bin/curl is hardcoded in the wrapper; rewrite the clone copy the
    # way the survivability suite does so the page config lands in a file.
    curl_stub = bin_dir / "curl"
    curl_stub.write_text(f"#!/bin/bash\ncat >> '{curl_cfg}'\nexit 0\n", encoding="utf-8")
    curl_stub.chmod(0o755)
    w = clone / "scripts" / wrapper.name
    w.write_text(w.read_text(encoding="utf-8").replace("/usr/bin/curl", str(curl_stub)), encoding="utf-8")
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "RADON_WEEKEND_REPO": str(clone),
        "RADON_WEEKEND_SKIP_PRUNE": "1",
    }
    return clone, env, git_log, gh_log, curl_cfg, weekend


def _run(clone: Path, env: dict, wrapper_name: str):
    return subprocess.run(
        [BASH, str(clone / "scripts" / wrapper_name), "audit"],
        cwd=clone, env=env, capture_output=True, text=True, timeout=180,
    )


@pytest.mark.parametrize("loop", sorted(LOOPS))
class TestTheWrapperPublishesTheReportPrivately:
    def test_the_page_links_the_private_report(self, tmp_path, loop):
        wrapper, _s, _m, _k = LOOPS[loop]
        clone, env, git_log, gh_log, curl_cfg, weekend = _stage(tmp_path, loop, write_report=True)
        proc = _run(clone, env, wrapper.name)
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        cfg = curl_cfg.read_text(encoding="utf-8")
        m = re.search(r'data-urlencode = "url=([^"]+)"', cfg)
        assert m, cfg
        assert re.fullmatch(rf"{re.escape(REPORTS_WEB)}/reports/{loop}/\d{{4}}-\d{{2}}-\d{{2}}/audit\.md", m.group(1)), m.group(1)
        assert 'data-urlencode = "url_title=Open private report"' in cfg, cfg
        assert "(no private report this phase)" not in cfg
        # Published copy exists in the private checkout, findings intact,
        # secret literals gone.
        published = list((weekend / ".security-reports" / "reports" / loop).rglob("audit.md"))
        assert len(published) == 1, published
        body = published[0].read_text(encoding="utf-8")
        assert CANARY_FINDING in body, body
        assert CANARY_TOKEN not in body, body
        assert "[REDACTED]" in body

    def test_the_push_uses_the_deploy_key_outside_the_clone(self, tmp_path, loop):
        wrapper, _s, _m, _k = LOOPS[loop]
        clone, env, git_log, gh_log, curl_cfg, weekend = _stage(tmp_path, loop, write_report=True)
        _run(clone, env, wrapper.name)
        calls = git_log.read_text(encoding="utf-8")
        push = [ln for ln in calls.splitlines() if " push " in ln]
        assert push, calls
        key = str(weekend / ".security-reports-deploy-key")
        assert all(f"-i {key}" in ln and "IdentitiesOnly=yes" in ln for ln in push), push
        assert "radon-security-reports.git" in calls
        clone_lines = [ln for ln in calls.splitlines() if "|clone " in ln]
        assert clone_lines and all("radon-security-reports" in ln for ln in clone_lines), clone_lines
        assert not str(clone) in "".join(push), "the report push must never target the loop clone"

    def test_the_public_deadman_never_carries_the_link(self, tmp_path, loop):
        wrapper, _s, _m, _k = LOOPS[loop]
        clone, env, git_log, gh_log, curl_cfg, weekend = _stage(tmp_path, loop, write_report=True)
        _run(clone, env, wrapper.name)
        calls = gh_log.read_text(encoding="utf-8")
        assert "issue comment" in calls
        assert "radon-security-reports" not in calls, calls
        assert CANARY_FINDING not in calls and CANARY_TOKEN not in calls, calls

    def test_no_report_means_no_link_and_the_page_says_so(self, tmp_path, loop):
        wrapper, _s, _m, _k = LOOPS[loop]
        clone, env, git_log, gh_log, curl_cfg, weekend = _stage(tmp_path, loop, write_report=False)
        proc = _run(clone, env, wrapper.name)
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        cfg = curl_cfg.read_text(encoding="utf-8")
        assert 'data-urlencode = "url=' not in cfg, cfg
        assert "(no private report this phase)" in cfg, cfg
        assert not git_log.exists() or "radon-security-reports" not in git_log.read_text(encoding="utf-8")

    def test_no_key_means_no_publish(self, tmp_path, loop):
        wrapper, _s, _m, _k = LOOPS[loop]
        clone, env, git_log, gh_log, curl_cfg, weekend = _stage(tmp_path, loop, write_report=True, with_key=False)
        proc = _run(clone, env, wrapper.name)
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        assert not git_log.exists() or "radon-security-reports" not in git_log.read_text(encoding="utf-8")
        assert not (weekend / ".security-reports").exists()
        assert "(no private report this phase)" in curl_cfg.read_text(encoding="utf-8")

    def test_the_wrapper_source_keeps_the_key_out_of_the_clone(self, tmp_path, loop):
        wrapper, scratch, _m, _k = LOOPS[loop]
        body = _uncommented(wrapper)
        assert 'REPORTS_KEY="$WEEKEND_ROOT/.security-reports-deploy-key"' in body
        assert f'PRIVATE_SCRATCH="$WEEKEND_ROOT/{scratch}"' in body
        assert 'REPORTS_REMOTE="${RADON_SECURITY_REPORTS_REMOTE:-git@github.com:joemccann/radon-security-reports.git}"' in body
        assert "publish_private_report || true" in body

    def test_the_skill_tells_the_agent_to_write_the_report(self, tmp_path, loop):
        _w, scratch, _m, skill = LOOPS[loop]
        text = " ".join(skill.read_text(encoding="utf-8").split())
        assert "## Private operator report (every phase)" in text
        assert f"{scratch}/latest-report-<phase>.md" in text
        assert "joemccann/radon-security-reports" in text
        assert "Never push to that repository yourself" in text


@pytest.mark.parametrize("loop", sorted(LOOPS))
def test_report_publish_pins_github_host_key(loop):
    # TOFU (accept-new) on the report push would trust whatever answered
    # first contact with the deploy key's remote; both wrappers pin GitHub's
    # published ed25519 host key instead (matching cloud/scripts/setup-vps.sh).
    text = LOOPS[loop][0].read_text(encoding="utf-8")
    assert "StrictHostKeyChecking=accept-new" not in text
    assert "StrictHostKeyChecking=yes" in text
    assert "UserKnownHostsFile=" in text
    assert "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl" in text

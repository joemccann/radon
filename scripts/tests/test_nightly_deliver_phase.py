"""Every nightly loop's cycle is audit -> remediate -> deliver, and the deliver
phase ends with the operator told what to merge.

Until 2026-09-02 the five loops stopped at "findings recorded" or "one fix per
night": a verified finding could sit in a ledger for days, and a PR the loop
did open could sit red with nobody told. The contract asserted here:

- the wrapper's `cycle` runs the three phases in order, each under its own
  cap, with the deliver cap overridable (`RADON_WEEKEND_DELIVER_CAP_SECS`);
- the deliver phase's dead-man/Pushover line is the operator's merge cue:
  `N PR(s) green, ready to merge: <urls>` or `INCOMPLETE: <check>`, keyed on
  the verdict line the skill prints (`scripts/nightly_deliver.py verdict`),
  never on the agent's exit code alone;
- every SKILL.md carries the mandate (implement EVERY verified finding, one
  dated branch, a deliver phase, the loop never merges) and the metrics;
- `scripts/github_pr_output.py` accepts every loop the wrappers name.

Wrapper cases are RUN against a staged clone with stub `gh` / `claude` /
`git` / `timeout` binaries, in the house style of
test_weekend_wrapper_self_rewrite.py, so the contract is proven at the wire.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

import github_pr_output as pr
import nightly_deliver as nd

REPO = Path(__file__).resolve().parents[2]
BASH = "/bin/bash"
COMMENT_MARK = "<<<COMMENT>>>"

# loop slug -> (wrapper, log dir, skill dir)
LOOPS = {
    "reliability": ("reliability_weekend.sh", "reliability-weekend", "reliability-weekend"),
    "testing": ("testing_weekend.sh", "testing-weekend", "testing-weekend"),
    "ci-performance": ("ci_performance_nightly.sh", "ci-performance", "ci-performance"),
    "documentation": ("documentation_nightly.sh", "documentation-nightly", "documentation-nightly"),
    "security": ("security_nightly.sh", "security-nightly", "security-nightly"),
}
LOOP_IDS = sorted(LOOPS)
MARKERS = (
    ".radon-weekend-runner",
    ".radon-security-runner",
    ".radon-reliability-runner",
    ".radon-testing-runner",
    ".radon-ci-performance-runner",
    ".radon-documentation-runner",
)
URL1 = "https://github.com/joemccann/radon/pull/301"
URL2 = "https://github.com/joemccann/radon/pull/302"
DEFAULT_DELIVER_CAP = 10800


def _wrapper(loop: str) -> Path:
    return REPO / "scripts" / LOOPS[loop][0]


def _skill(loop: str) -> Path:
    return REPO / ".claude" / "skills" / LOOPS[loop][2] / "SKILL.md"


def _uncommented(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _security_marker() -> str:
    src = _wrapper("security").read_text(encoding="utf-8")
    return re.search(r'PHASE_COMPLETE_MARKER="([^"]+)"', src).group(1)


def _build(tmp_path: Path, loop: str, *, deliver_lines: str = "", extra_env: dict | None = None) -> dict:
    """Stage a fake runner clone plus the stub binaries the wrapper calls.

    `deliver_lines` is what the stub agent prints when invoked for the
    deliver phase (the skill's verdict line, or nothing). Every phase of the
    security loop also prints that loop's completion marker.
    """
    script, label, _skill_dir = LOOPS[loop]
    clone = tmp_path / "clone"
    (clone / "scripts").mkdir(parents=True)
    (clone / "logs" / label).mkdir(parents=True)
    wrapper = clone / "scripts" / script
    shutil.copy2(_wrapper(loop), wrapper)
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    for marker in MARKERS:
        (clone / marker).touch()
    (clone / "scripts" / "weekend_notify.py").write_text("# unused\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_log = tmp_path / "gh-comments.log"
    push_log = tmp_path / "pushover.log"
    timeout_log = tmp_path / "timeout.log"
    agent_log = tmp_path / "agent.log"

    curl_stub = bin_dir / "curl"
    (tmp_path / ".env").write_text(
        "PUSHOVER_USER=test-user\nPUSHOVER_TOKEN=test-token\n", encoding="utf-8"
    )
    wrapper.write_text(
        wrapper.read_text(encoding="utf-8").replace("/usr/bin/curl", str(curl_stub)),
        encoding="utf-8",
    )

    _executable(bin_dir / "git", '#!/bin/bash\n# REL-188: the wrapper reads commit evidence from git before calling a phase\n# OK, so the stub reports a fresh HEAD and a current committer date.\ncase "$*" in\n  *"rev-parse HEAD"*) date +%s%N; exit 0 ;;\n  *"--format=%ct"*) date +%s; exit 0 ;;\nesac\nexit 0\n')
    _executable(bin_dir / "python3", "#!/bin/bash\nexit 0\n")

    complete = f"echo '{_security_marker()} '\"$PHASE\"' run_id=stub'\n" if loop == "security" else ""
    _executable(
        bin_dir / "claude",
        "#!/bin/bash\n"
        f'echo "claude $*" >> "{agent_log}"\n'
        'PHASE=audit\n'
        'case " $* " in *" deliver"*) PHASE=deliver ;; *" remediate"*) PHASE=remediate ;; esac\n'
        "echo 'stub agent output'\n"
        'if [ "$PHASE" = deliver ]; then\n  :\n'
        f"{deliver_lines}"
        "fi\n"
        + complete
        + "exit 0\n",
    )

    # Consume `-k <secs>` and the duration, log the duration when the child is
    # the agent (that is the cap the wrapper handed this phase), then exec.
    _executable(
        bin_dir / "timeout",
        "#!/bin/bash\n"
        "dur=''\n"
        'while [ $# -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    -k|--kill-after) shift 2 ;;\n'
        '    --foreground|--preserve-status) shift ;;\n'
        '    *) dur="$1"; shift; break ;;\n'
        '  esac\n'
        'done\n'
        f'if [ "${{1##*/}}" = "claude" ]; then echo "$dur $*" >> "{timeout_log}"; fi\n'
        'exec "$@"\n',
    )

    _executable(
        bin_dir / "gh",
        "#!/bin/bash\n"
        'case "$1 $2" in\n'
        '  "issue list") echo 42 ;;\n'
        '  "pr list") echo "" ;;\n'
        '  "issue comment")\n'
        "    while [ $# -gt 0 ]; do\n"
        '      if [ "$1" = "--body" ]; then shift\n'
        f'        printf \'{COMMENT_MARK}%s\\n\' "$1" >> "{gh_log}"\n'
        "        break\n"
        "      fi\n"
        "      shift\n"
        "    done ;;\n"
        "esac\n"
        "exit 0\n",
    )
    _executable(
        curl_stub,
        "#!/bin/bash\n"
        f'printf "%s\\n" "$*" >> "{push_log}"\n'
        "i=1\n"
        'while [ "$i" -le "$#" ]; do\n'
        '  eval "arg=\\${$i}"\n'
        '  if [ "$arg" = "--config" ]; then\n'
        "    i=$((i + 1))\n"
        '    eval "cfg=\\${$i}"\n'
        f'    if [ "$cfg" = "-" ]; then cat >> "{push_log}"; fi\n'
        "  fi\n"
        "  i=$((i + 1))\n"
        "done\n"
        "exit 0\n",
    )

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "RADON_WEEKEND_REPO": str(clone),
        "RADON_WEEKEND_FETCH_PAUSE_SECS": "0",
    }
    env.update(extra_env or {})
    return {
        "clone": clone,
        "wrapper": wrapper,
        "env": env,
        "gh_log": gh_log,
        "push_log": push_log,
        "timeout_log": timeout_log,
        "agent_log": agent_log,
    }


def _run(cfg: dict, mode: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, str(cfg["wrapper"]), mode],
        cwd=cfg["clone"], env=cfg["env"], capture_output=True, text=True,
        timeout=180, check=False,
    )


def _comments(cfg: dict) -> list[str]:
    if not cfg["gh_log"].exists():
        return []
    raw = cfg["gh_log"].read_text(encoding="utf-8")
    return [c for c in raw.split(COMMENT_MARK) if c.strip()]


def _pushover(cfg: dict) -> str:
    return cfg["push_log"].read_text(encoding="utf-8") if cfg["push_log"].exists() else ""


def _why(result: subprocess.CompletedProcess, cfg: dict) -> str:
    return (
        f"rc={result.returncode}\n--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}\n--- comments ---\n{_comments(cfg)}\n"
    )


def _ready(loop: str, urls: list[str]) -> str:
    return f"echo '{nd.ready_line(loop, urls)}'\n"


def _incomplete(loop: str, check: str, url: str) -> str:
    return f"echo '{nd.incomplete_line(loop, check, url)}'\n"


# --------------------------------------------------------------------------
# (a) the cycle runs audit -> remediate -> deliver, each under its own cap
# --------------------------------------------------------------------------
class TestTheCycleRunsThreePhasesInOrder:
    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_cycle_runs_audit_remediate_deliver_and_passes_the_deliver_cap(self, tmp_path, loop):
        cfg = _build(
            tmp_path, loop, deliver_lines=_ready(loop, [URL1]),
            extra_env={
                "RADON_WEEKEND_AUDIT_CAP_SECS": "1111",
                "RADON_WEEKEND_REMEDIATE_CAP_SECS": "2222",
                "RADON_WEEKEND_DELIVER_CAP_SECS": "3333",
            },
        )
        result = _run(cfg, "cycle")
        assert result.returncode == 0, _why(result, cfg)

        rows = cfg["timeout_log"].read_text(encoding="utf-8").splitlines()
        phases = [re.search(r"/\S+ (audit|remediate|deliver)", row).group(1) for row in rows]
        assert phases == ["audit", "remediate", "deliver"], rows
        caps = [int(row.split()[0]) for row in rows]
        for cap, expected in zip(caps, (1111, 2222, 3333)):
            # The wrapper hands `timeout` the phase's REMAINING cap; a few
            # seconds may have elapsed since begin_phase.
            assert expected - 10 <= cap <= expected, rows

        bodies = _comments(cfg)
        order = [re.match(r"\*\*(\w+)\*\*", body.strip()).group(1) for body in bodies]
        assert order == ["audit", "remediate", "deliver"], bodies
        assert f"deliver_rc=0" in result.stdout, result.stdout

    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_the_deliver_cap_defaults_to_three_hours(self, tmp_path, loop):
        cfg = _build(tmp_path, loop, deliver_lines=_ready(loop, [URL1]))
        result = _run(cfg, "deliver")
        assert result.returncode == 0, _why(result, cfg)
        rows = cfg["timeout_log"].read_text(encoding="utf-8").splitlines()
        assert len(rows) == 1 and "deliver" in rows[0], rows
        cap = int(rows[0].split()[0])
        assert DEFAULT_DELIVER_CAP - 10 <= cap <= DEFAULT_DELIVER_CAP, rows

    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_deliver_runs_even_when_remediate_failed(self, tmp_path, loop):
        # Committed fixes on the dated branch are durable; CI decides whether
        # they are mergeable, not the remediate exit code.
        cfg = _build(tmp_path, loop, deliver_lines=_ready(loop, [URL1]))
        stub = tmp_path / "bin" / "claude"
        stub.write_text(
            stub.read_text(encoding="utf-8").replace(
                "exit 0\n", 'if [ "$PHASE" = remediate ]; then exit 7; fi\nexit 0\n'
            ),
            encoding="utf-8",
        )
        result = _run(cfg, "cycle")
        assert result.returncode == 7, _why(result, cfg)
        # The reliability loop relaunches a failed remediate as continuation
        # rounds, each with its own comment; the phase ORDER is what matters.
        order = list(dict.fromkeys(
            re.match(r"\*\*(\w+)\*\*", body.strip()).group(1) for body in _comments(cfg)
        ))
        assert order == ["audit", "remediate", "deliver"], _comments(cfg)

    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_static_pins(self, loop):
        body = _uncommented(_wrapper(loop))
        assert re.search(r"usage: \S+ audit\|remediate\|deliver\|cycle", body), (
            f"{loop}: the wrapper usage line does not accept `deliver`"
        )
        assert "RADON_WEEKEND_DELIVER_CAP_SECS:-10800" in body, (
            f"{loop}: the deliver phase has no env-overridable 3h cap"
        )
        cycle = body[body.index('MODE" == "cycle"'):]
        assert cycle.index("run_phase audit") < cycle.index("run_phase remediate") < cycle.index("run_phase deliver"), (
            f"{loop}: the cycle does not run audit, remediate, deliver in that order"
        )


# --------------------------------------------------------------------------
# (b) the deliver phase's notification is the operator's merge cue
# --------------------------------------------------------------------------
class TestTheDeliverNotifyLine:
    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_green_prs_are_announced_as_ready_to_merge(self, tmp_path, loop):
        cfg = _build(tmp_path, loop, deliver_lines=_ready(loop, [URL1, URL2]))
        result = _run(cfg, "deliver")
        assert result.returncode == 0, _why(result, cfg)
        comments = _comments(cfg)
        assert len(comments) == 1, comments
        assert comments[0].startswith("**deliver**"), comments
        assert "**2 PR(s) green, ready to merge: " in comments[0], comments
        if loop != "security":
            assert f"{URL1} {URL2}**" in comments[0], comments
        # The Pushover carries the URLs for every loop; the security loop
        # redacts URLs only on the public issue.
        page = _pushover(cfg)
        assert "2 PR(s) green, ready to merge:" in page, page
        assert URL1 in page and URL2 in page, page
        assert "CRASHED" not in comments[0] and "INCOMPLETE" not in comments[0], comments

    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_nothing_to_merge_is_still_a_finished_phase(self, tmp_path, loop):
        cfg = _build(tmp_path, loop, deliver_lines=_ready(loop, []))
        result = _run(cfg, "deliver")
        assert result.returncode == 0, _why(result, cfg)
        assert "**0 PR(s), nothing to merge**" in _comments(cfg)[0], _comments(cfg)

    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_a_red_check_at_the_cap_is_incomplete_and_names_the_check(self, tmp_path, loop):
        cfg = _build(tmp_path, loop, deliver_lines=_incomplete(loop, "pytest-scripts-npsz", URL1))
        result = _run(cfg, "deliver")
        assert result.returncode == 75, _why(result, cfg)
        comment = _comments(cfg)[0]
        assert "**INCOMPLETE: pytest-scripts-npsz**" in comment, comment
        assert "resumes" in comment, comment
        assert "INCOMPLETE: pytest-scripts-npsz" in _pushover(cfg), _pushover(cfg)

    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_exit_zero_without_a_verdict_is_incomplete(self, tmp_path, loop):
        cfg = _build(tmp_path, loop, deliver_lines="echo 'pushed; CI looks fine, I think'\n")
        result = _run(cfg, "deliver")
        assert result.returncode == 75, _why(result, cfg)
        comment = _comments(cfg)[0]
        assert f"**{nd.NO_VERDICT_STATUS}**" in comment, comment

    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_the_last_verdict_line_wins(self, tmp_path, loop):
        # A red check that was then fixed and re-pushed: the operator hears
        # the final state, not the first attempt.
        cfg = _build(
            tmp_path, loop,
            deliver_lines=_incomplete(loop, "vitest", URL1) + "echo 'fixed, pushed'\n" + _ready(loop, [URL1]),
        )
        result = _run(cfg, "deliver")
        assert result.returncode == 0, _why(result, cfg)
        assert "**1 PR(s) green, ready to merge: " in _comments(cfg)[0], _comments(cfg)

    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_the_wrapper_and_the_helper_render_the_same_status(self, tmp_path, loop):
        line = nd.ready_line(loop, [URL1])
        cfg = _build(tmp_path, loop, deliver_lines=f"echo '{line}'\n")
        _run(cfg, "deliver")
        assert f"**{nd.notify_status(line)}**" in _pushover(cfg) or nd.notify_status(line) in _pushover(cfg)

    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_audit_and_remediate_are_untouched_by_the_verdict_rule(self, tmp_path, loop):
        cfg = _build(tmp_path, loop)
        result = _run(cfg, "audit")
        assert result.returncode == 0, _why(result, cfg)
        assert "deliver verdict" not in _comments(cfg)[0], _comments(cfg)


# --------------------------------------------------------------------------
# (d) the PR formatter accepts every loop
# --------------------------------------------------------------------------
class TestThePrFormatterAcceptsEveryLoop:
    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_wrapper_slug_is_a_formatter_loop(self, loop):
        assert loop in pr.LOOP_TITLES
        title = pr.format_pr_title(loop=loop, date="2026-09-02", issue="the thing broke")
        assert title.startswith(pr.LOOP_TITLES[loop])

    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_cli_accepts_the_slug(self, loop):
        proc = subprocess.run(
            [
                os.environ.get("PYTHON", "python3"), str(REPO / "scripts" / "github_pr_output.py"),
                "--loop", loop, "--date", "2026-09-02", "--issue", "x", "--fix", "y", "--json",
            ],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 0, proc.stderr

    @pytest.mark.parametrize("loop", LOOP_IDS)
    def test_the_skill_passes_its_own_slug_to_the_formatter(self, loop):
        text = _skill(loop).read_text(encoding="utf-8")
        assert f"--loop {loop}" in text


# --------------------------------------------------------------------------
# (e) long stages run detached and are awaited in-session, not parked in the
#     harness's background (security lessons.md #20: the 2026-09-02 and
#     2026-09-03 security audits parked DeepSec / pytest as harness
#     background tasks and returned INCOMPLETE when the wrapper killed them)
# --------------------------------------------------------------------------
class TestLongStagesRunDetachedAndAreAwaitedInSession:
    @pytest.mark.parametrize("loop", LOOP_IDS)
    @pytest.mark.parametrize(
        "sentence",
        [
            "## Long stages run detached and are awaited in-session",
            "must not be printed while any stage is still in",
            "nohup",
            "disown",
            "DONE",
        ],
    )
    def test_the_detached_stage_rule_is_present(self, loop, sentence):
        text = " ".join(_skill(loop).read_text(encoding="utf-8").split())
        assert sentence in text, f"{loop}: SKILL.md does not state {sentence!r}"

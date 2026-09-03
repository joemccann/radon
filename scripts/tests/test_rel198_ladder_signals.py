"""REL-198 (R-530, R-532, R-533, R-536): the ladder's control signals come
from the harness, not the transcript."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
BASH = shutil.which("bash") or "/bin/bash"

LOOPS = {
    "reliability": SCRIPTS / "reliability_weekend.sh",
    "testing": SCRIPTS / "testing_weekend.sh",
    "security": SCRIPTS / "security_nightly.sh",
    "documentation": SCRIPTS / "documentation_nightly.sh",
    "ci-performance": SCRIPTS / "ci_performance_nightly.sh",
}
PLISTS = {
    "reliability": REPO / "config" / "com.radon.reliability-daily.plist",
    "testing": REPO / "config" / "com.radon.testing-daily.plist",
    "security": REPO / "config" / "com.radon.security-daily.plist",
    "documentation": REPO / "config" / "com.radon.documentation-daily.plist",
    "ci-performance": REPO / "config" / "com.radon.ci-performance-daily.plist",
}


def _uncommented(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


@pytest.mark.parametrize("loop", sorted(LOOPS))
class TestControlSignalHygiene:
    def test_begin_phase_resets_the_exhaustion_flag(self, loop):
        """R-533: an audit-phase exhaustion mislabelled a successful
        remediate in cycle mode; rung carry is intended, flag carry is not."""
        body = _uncommented(LOOPS[loop])
        start = body.index("begin_phase()")
        fn = body[start : body.index("\n}", start)]
        assert "ALL_MODELS_EXHAUSTED=0" in fn
        assert "MODEL_INDEX" not in fn, "rung carry must be preserved"

    def test_kill_round_group_runs_before_the_pid_is_cleared(self, loop):
        """R-532: `ROUND_PID=\"\"` before `kill_round_group` made orphan
        reaping after a normal exit dead code (the guard early-returns)."""
        body = _uncommented(LOOPS[loop])
        # reliability names it run_round; the siblings inline it in run_phase.
        wait_at = body.index('wait "$ROUND_PID"')
        tail = body[wait_at : wait_at + 1500]
        kill_at = tail.index("kill_round_group")
        clear_at = tail.find('ROUND_PID=""')
        assert clear_at == -1 or kill_at < clear_at, (
            f"{loop}: run_round clears ROUND_PID before kill_round_group, "
            "making the guard's early-return skip orphan reaping"
        )

    def test_quota_grep_excludes_wrapper_markers_and_bounds_the_tail(self, loop):
        """R-530: the detector reads the agent's own transcript; at minimum
        it must scan only the final lines and never its own marker lines."""
        body = _uncommented(LOOPS[loop])
        start = body.index("is_quota_exhausted()")
        fn = body[start : body.index("\n}", start)]
        assert re.search(r"tail -n \d+", fn), "no final-N-lines bound"
        assert "grep -v" in fn, "the wrapper's own marker lines are not excluded"

    def test_managed_settings_is_in_the_reroute_scan(self, loop):
        """R-536 (half): a managed-settings.json apiKeyHelper reaches the
        agent past every env unset."""
        body = _uncommented(LOOPS[loop])
        assert "managed-settings.json" in body

    def test_the_plist_freezes_the_autoupdater(self, loop):
        """R-536: the reroute variable list is pinned to CLI 2.1.258; a
        self-updating CLI silently invalidates it."""
        assert "DISABLE_AUTOUPDATER" in PLISTS[loop].read_text()


class TestQuoteInACrashIsNotQuota:
    def test_a_crash_quoting_529_early_does_not_drop_a_rung(self, tmp_path):
        """R-530 behavioral: a crashing round that QUOTES '529 Overloaded'
        deep in its transcript (not in the CLI's final lines) is a crash,
        not quota exhaustion."""
        sys.path.insert(0, str(SCRIPTS / "tests"))
        from test_weekend_model_ladder import _clone, _stub_bin

        wrapper = LOOPS["reliability"]
        models_log = tmp_path / "models.txt"
        gh_log = tmp_path / "gh.log"
        exhausted = tmp_path / "exhausted.txt"
        exhausted.write_text("", encoding="utf-8")
        bin_dir = _stub_bin(tmp_path, models_log, exhausted, gh_log)
        # Replace the claude stub: quote the pattern, pad 60 lines, crash.
        (bin_dir / "claude").write_text(
            "#!/bin/bash\n"
            'model=""\n'
            "while [ $# -gt 0 ]; do\n"
            '  if [ "$1" = "--model" ]; then model="$2"; shift 2; continue; fi\n'
            "  shift\n"
            "done\n"
            f'printf "%s\\n" "$model" >> "{models_log}"\n'
            'echo "the previous incident log said: 529 Overloaded"\n'
            "for i in $(seq 1 60); do echo \"working line $i\"; done\n"
            "exit 1\n",
            encoding="utf-8",
        )
        (bin_dir / "claude").chmod(0o755)
        repo = _clone(tmp_path, wrapper)
        proc = subprocess.run(
            [BASH, str(repo / "scripts" / wrapper.name), "audit"],
            cwd=repo,
            env={
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "HOME": str(tmp_path / "home"),
                "RADON_WEEKEND_REPO": str(repo),
            },
            capture_output=True, text=True, timeout=180,
        )
        models = (
            models_log.read_text(encoding="utf-8").splitlines()
            if models_log.exists() else []
        )
        first_rung = "claude-fable-5[1m]"
        assert models == [first_rung], (
            f"a crash quoting the quota pattern walked the ladder: {models!r}\n"
            f"{proc.stdout}{proc.stderr}"
        )

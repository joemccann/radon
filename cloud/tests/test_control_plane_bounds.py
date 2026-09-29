"""REL-069 tranche C — R-183, R-184, R-185, R-188.

Four control-plane paths where a benign race, a single failure, an ordinary
non-zero exit, or an unreviewed list edit produces a wrong outcome: a green
release red-flagged after it was already committed, a silently truncated
timer-enable loop, a false CRASHED dead-man comment, and an allowlist that
can grow without anyone deciding to grow it.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "cloud" / "scripts" / "deploy.sh"
HELPER = REPO / "cloud" / "scripts" / "deploy-root-helper.sh"
ALLOWLIST = REPO / "cloud" / "config" / "auto-sync-units.txt"
RUNNER = REPO / "scripts" / "runner" / "run_loop.sh"
POST_HOOK = REPO / "scripts" / "runner" / "hooks" / "security_post.sh"


# --------------------------------------------------------------------------
# R-183 — a post-commit sync failure must not red-flag a verified release
# --------------------------------------------------------------------------
class TestPostCommitSyncIsNotFatal:
    def test_the_sync_still_runs_after_commit_transition(self):
        src = DEPLOY.read_text()
        block = src.split('sudo "$DEPLOY_ROOT_HELPER" commit-transition')[1][:600]
        assert "sync_scheduled_units" in block

    def test_a_failed_sync_warns_instead_of_failing_the_deploy(self):
        src = DEPLOY.read_text()
        assert "sync_scheduled_units || return 1" not in src, (
            "the release is already verified, marked green and its transition "
            "committed by this point; the helper's exit 76 just means main "
            "moved during the deploy, which is a benign race"
        )
        assert src.count("sync_scheduled_units || log_warn") == 3, (
            "all three call sites are post-verification"
        )

    def test_the_warning_names_the_consequence(self):
        src = DEPLOY.read_text()
        assert src.count("the next deploy will republish them") == 3


# --------------------------------------------------------------------------
# R-184 — one failed enable must not silently skip the rest
# --------------------------------------------------------------------------
class TestEnableLoopIsNotTruncated:
    def test_the_loop_does_not_return_on_the_first_failure(self):
        src = HELPER.read_text()
        assert 'systemctl_bounded enable --now "$unit" || return $?' not in src, (
            "every later new timer was silently skipped, and neither the "
            "deploy nor the drift audit noticed"
        )

    def test_every_new_timer_is_attempted(self):
        src = HELPER.read_text()
        loop = src.split("for unit in ${new_timers[@]+\"${new_timers[@]}\"}; do")[1].split("done")[0]
        assert "continue" in loop or "failed_enables" in loop

    def test_the_failures_are_reported(self):
        src = HELPER.read_text()
        assert "install-units: enable failed" in src

    def test_the_verb_still_exits_non_zero_when_an_enable_failed(self):
        src = HELPER.read_text()
        block = src.split("for unit in ${new_timers[@]+\"${new_timers[@]}\"}; do")[1].split(
            "github_origin_is_allowed"
        )[0]
        assert re.search(r"return 1", block), (
            "a failed enable must still fail the verb, just not before the "
            "remaining timers have been tried"
        )


# --------------------------------------------------------------------------
# R-185 — an ordinary non-zero agent exit is not a runner crash
# --------------------------------------------------------------------------
class TestDeadManDoesNotCryWolf:
    def test_the_runner_treats_an_agent_exit_as_an_outcome(self):
        src = RUNNER.read_text()
        assert "set -uo pipefail" in src and "set -e" not in src, (
            "under errexit a failed or timed-out agent kills the runner before "
            "it reports the phase's real status"
        )

    def test_the_real_outcome_is_still_reported(self, tmp_path):
        """T-239: RUN the security loops' classifier. `claude -p` exits 0
        after killing unfinished background work, so a phase cut in half must
        not report OK."""
        src = POST_HOOK.read_text()
        start = src.index("phase_status() {")
        block = src[start:src.index("\n}\n", start) + 3]

        clean = tmp_path / "clean.log"
        clean.write_text("audit start\nall done\n", encoding="utf-8")
        truncated = tmp_path / "truncated.log"
        truncated.write_text("Background tasks still running after 600s; terminating.\n", encoding="utf-8")

        def status(rc: int, log: Path) -> str:
            proc = subprocess.run(
                [shutil.which("bash") or "/bin/bash", "-c",
                 'set -uo pipefail\nBG_CEILING_MARKER="Background tasks still running after"\n'
                 + f'PHASE_RC={rc}\nPHASE_LOG="{log}"\n' + block + "\nphase_status\n"],
                capture_output=True, text=True, timeout=60,
            )
            assert proc.returncode == 0, (proc.returncode, proc.stderr)
            return proc.stdout.strip()

        assert status(0, clean) == "OK"
        assert status(124, clean) == "TIMEOUT"
        assert status(9, clean) == "FAILED (exit 9)"
        assert status(0, truncated) != "OK", (
            "a phase the harness truncated still pages OK, so the operator "
            "cannot tell it from a finished run"
        )


# --------------------------------------------------------------------------
# R-188 — the auto-sync allowlist needs a membership ratchet
# --------------------------------------------------------------------------
# Pinned to the full scheduled-unit set main adopted in PR #73 (2026-08-22):
# every timer-owned unit CI may publish. Growing it is a review decision.
EXPECTED_AUTO_SYNC_UNITS = (
    # AI evidence daily collector: deliberately authorize these two scheduled units.
    "radon-aa-frontier-refresh.service",
    "radon-aa-frontier-refresh.timer",
    "radon-ai-cycle-backfill.service",
    "radon-ai-cycle-backfill.timer",
    "radon-ai-cycle.service",
    "radon-ai-cycle.timer",
    "radon-bpi.service",
    "radon-bpi.timer",
    "radon-breadth.service",
    "radon-breadth.timer",
    "radon-catalysts.service",
    "radon-catalysts.timer",
    "radon-cor.service",
    "radon-cor.timer",
    "radon-credit-spread.service",
    "radon-credit-spread.timer",
    "radon-cta-sync.service",
    "radon-cta-sync.timer",
    "radon-db-retention.service",
    "radon-db-retention.timer",
    "radon-demo-mirror.service",
    "radon-demo-mirror.timer",
    "radon-divyield.service",
    "radon-divyield.timer",
    "radon-equibles-13f.service",
    "radon-equibles-13f.timer",
    "radon-equibles-ats.service",
    "radon-equibles-ats.timer",
    "radon-equibles-cot.service",
    "radon-equibles-cot.timer",
    "radon-equibles-filings.service",
    "radon-equibles-filings.timer",
    "radon-equibles-short-crowding.service",
    "radon-equibles-short-crowding.timer",
    "radon-flex-pull.service",
    "radon-flex-pull.timer",
    "radon-flow-refresh.service",
    "radon-flow-refresh.timer",
    "radon-forecast-nightly.service",
    "radon-forecast-nightly.timer",
    "radon-garch.service",
    "radon-garch.timer",
    "radon-grok-page-responder.service",
    "radon-grok-page-responder.timer",
    "radon-host-metrics.service",
    "radon-host-metrics.timer",
    "radon-hhlev.service",
    "radon-hhlev.timer",
    "radon-hyad.service",
    "radon-hyad.timer",
    "radon-iei-hyg.service",
    "radon-iei-hyg.timer",
    "radon-incident-watchdog.service",
    "radon-incident-watchdog.timer",
    "radon-ivrank.service",
    "radon-ivrank.timer",
    "radon-knowledge.service",
    "radon-knowledge.timer",
    "radon-leap.service",
    "radon-leap.timer",
    "radon-liquidcompute.service",
    "radon-liquidcompute.timer",
    "radon-margin-debt.service",
    "radon-margin-debt.timer",
    "radon-media-backup.service",
    "radon-media-backup.timer",
    "radon-model-catalog.service",
    "radon-model-catalog.timer",
    "radon-oi-changes.service",
    "radon-oi-changes.timer",
    "radon-perf-twr.service",
    "radon-perf-twr.timer",
    "radon-portfolio-archive.service",
    "radon-portfolio-archive.timer",
    "radon-signals-refresh.service",
    "radon-signals-refresh.timer",
    "radon-skew.service",
    "radon-skew.timer",
    "radon-skew2d.service",
    "radon-skew2d.timer",
    "radon-straddle.service",
    "radon-straddle.timer",
    # 2026-09-18: the Antigravity swap added a PATH line; without CI publish
    # the unit would drift on every deploy (test_unit_install_acknowledgment).
    "radon-subscription-tokens.service",
    "radon-subscription-tokens.timer",
    "radon-trin.service",
    "radon-trin.timer",
    # Issue #457: TradingView alert drain (5-minute oneshot).
    "radon-tv-alerts.service",
    "radon-tv-alerts.timer",
    "radon-vcg-refresh.service",
    "radon-vcg-refresh.timer",
    "radon-vixcor.service",
    "radon-vixcor.timer",
    "radon-vixts.service",
    "radon-vixts.timer",
    "radon-vol-cone-intraday.service",
    "radon-vol-cone-intraday.timer",
    "radon-vol-cone.service",
    "radon-vol-cone.timer",
    "radon-watchdog-continuous.service",
    "radon-watchdog-continuous.timer",
    "radon-watchdog-daily.service",
    "radon-watchdog-daily.timer",
    "radon-watchdog-error.service",
    "radon-watchdog-error.timer",
    "radon-watchdog-intraday.service",
    "radon-watchdog-intraday.timer",
    "radon-yield-curve.service",
    "radon-yield-curve.timer",
)


class TestAutoSyncMembershipRatchet:
    def _names(self) -> list[str]:
        names = []
        for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                names.append(line)
        return names

    def test_the_membership_is_pinned_exactly(self):
        assert tuple(self._names()) == EXPECTED_AUTO_SYNC_UNITS, (
            "adding a unit to the auto-sync allowlist grants CI the right to "
            "publish it to /etc/systemd/system without a root bootstrap; that "
            "is a deliberate decision, so the list is pinned here and growing "
            "it requires editing this test"
        )

    def test_the_ratchet_lives_next_to_the_shape_checks(self):
        src = (REPO / "cloud" / "tests" / "test_sync_scheduled_units.py").read_text()
        assert "EXPECTED_AUTO_SYNC_UNITS" in src or "membership" in src.lower()

    def test_install_units_is_bounded_by_the_manifest_not_the_allowlist(self):
        """`install-units` needs no allowlist because the manifest digest is
        its review gate — pin that, so nobody 'fixes' the asymmetry by
        widening install-units instead."""
        src = HELPER.read_text()
        block = src.split("install_units()")[1] if "install_units()" in src else src
        assert "auto-sync-units" not in block[:4000]

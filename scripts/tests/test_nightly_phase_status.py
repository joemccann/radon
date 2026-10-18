"""Formatter and parser for the harness-emitted nightly phase line.

The line comes from scripts/runner/phase_status.sh, not from the agent.
Python is the parser the report uses, including the legacy-comment fallback.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import nightly_phase_status as nps

REPO = Path(__file__).resolve().parents[2]
BASH_LIB = REPO / "scripts" / "runner" / "phase_status.sh"
TS = "2026-10-18T15:47:00Z"
SHA = "0123456789abcdef0123456789abcdef01234567"


def _bash_exit(rc, status="", *, refused="", killed="", hook_required=""):
    script = (
        f'. "{BASH_LIB}"\n'
        'phase_status_exit "$1"\n'
    )
    env = {
        **os.environ,
        "PHASE_RC": str(rc),
        "PHASE_REFUSED": refused,
        "PHASE_KILLED": killed,
        "PHASE_HOOK_REQUIRED": hook_required,
    }
    proc = subprocess.run(
        ["bash", "-c", script, "phase_status_exit", status],
        capture_output=True, text=True, env=env, timeout=15, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return int(proc.stdout.strip())


def _bash_body(loop, phase, status, *, rc="0", sha="", ts=TS, refused=""):
    script = (
        f'. "{BASH_LIB}"\n'
        'phase_status_body "$1" "$2" "$3" "$4"\n'
    )
    env = {
        **os.environ,
        "PHASE_RC": str(rc),
        "PHASE_REFUSED": refused,
        "PHASE_AUDITED_SHA": sha,
        "PHASE_KILLED": "",
        "PHASE_HOOK_REQUIRED": "",
    }
    proc = subprocess.run(
        ["bash", "-c", script, "phase_status_body", loop, phase, status, ts],
        capture_output=True, text=True, env=env, timeout=15, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


class TestPhaseExit:
    def test_zero_is_ok(self):
        assert nps.phase_exit(0) == 0
        assert nps.phase_exit(0, hook_status="OK") == 0
        assert nps.phase_exit(0, hook_status="OK: 0 PR(s), nothing to merge") == 0

    def test_nonzero_agent_exit_is_failed(self):
        assert nps.phase_exit(3) == 1
        assert nps.phase_exit(9, hook_status="FAILED (exit 9)") == 1

    def test_timeout_is_124(self):
        assert nps.phase_exit(124) == 124
        assert nps.phase_exit(124, hook_status="INCOMPLETE: deliver cap hit (TIMEOUT)") == 124

    def test_missing_hook_status_is_failed(self):
        assert nps.phase_exit(0, hook_status="", hook_required=True) == 1
        assert nps.phase_exit(0, hook_status="FAILED (post-run hook gave no status)") == 1

    def test_refused_ground_truth_incomplete_and_killed(self):
        assert nps.phase_exit(0, refused=True) == 2
        assert nps.phase_exit(2) == 2
        assert nps.phase_exit(70) == 70
        assert nps.phase_exit(0, hook_status="INCOMPLETE (exit 0 without the phase-completion marker)") == 75
        assert nps.phase_exit(0, hook_status="TRUNCATED (background work killed before the phase finished)") == 75
        assert nps.phase_exit(143, killed=True) == 1
        assert nps.phase_exit(143) == 1

    @pytest.mark.parametrize(
        "rc,status,refused,killed,hook_required",
        [
            (0, "", "", "", ""),
            (0, "OK", "", "", ""),
            (3, "FAILED (exit 3)", "", "", ""),
            (124, "TIMEOUT", "", "", ""),
            (124, "INCOMPLETE: deliver cap hit (TIMEOUT)", "", "", ""),
            (0, "", "", "", "1"),
            (0, "FAILED (post-run hook gave no status)", "", "", "1"),
            (0, "REFUSED", "credential file present", "", ""),
            (2, "", "x", "", ""),
            (70, "GROUND TRUTH FAILED", "", "", ""),
            (0, "INCOMPLETE (exit 0 without the phase-completion marker)", "", "", ""),
            (143, "KILLED (SIGTERM)", "", "1", ""),
            (75, "", "", "", ""),
        ],
    )
    def test_bash_matches_python(self, rc, status, refused, killed, hook_required):
        expect = nps.phase_exit(
            rc,
            refused=bool(refused),
            killed=bool(killed),
            hook_status=status,
            hook_required=hook_required == "1",
        )
        assert _bash_exit(rc, status, refused=refused, killed=killed, hook_required=hook_required) == expect


class TestFormat:
    def test_ok_audit_has_html_visible_line_and_cursor(self):
        body = nps.format_phase_comment(
            loop="testing", phase="audit", exit_code=0, audited_through=SHA, timestamp=TS,
        )
        assert body == (
            f"<!-- radon-phase loop=testing phase=audit exit=0 audited-through={SHA} ts={TS} -->\n"
            f"radon-phase loop=testing phase=audit exit=0 audited-through={SHA} ts={TS}\n"
            f"audited-through: {SHA}\n"
        )

    def test_failed_phase_omits_the_sha(self):
        body = nps.format_phase_comment(
            loop="security", phase="audit", exit_code=1, audited_through=SHA, timestamp=TS,
        )
        assert "audited-through" not in body
        assert "exit=1" in body
        assert SHA not in body

    def test_non_audit_success_omits_the_sha(self):
        body = nps.format_phase_comment(
            loop="security-deepsec", phase="deliver", exit_code=0,
            audited_through=SHA, timestamp=TS,
        )
        assert "exit=0" in body
        assert "audited-through" not in body

    def test_public_body_has_only_loop_phase_exit_sha_and_timestamp(self):
        body = nps.format_phase_comment(
            loop="security", phase="audit", exit_code=0, audited_through=SHA, timestamp=TS,
        )
        assert "pull/" not in body
        assert "/" not in body.replace(f"ts={TS}", "")
        assert "secret" not in body
        assert body.count("\n") == 3

    def test_bash_body_matches_python(self):
        bash = _bash_body("testing", "audit", "OK", rc="0", sha=SHA)
        py = nps.format_phase_comment(
            loop="testing", phase="audit", exit_code=0, audited_through=SHA, timestamp=TS,
        )
        assert bash == py

    def test_bash_failed_body_drops_a_secret_in_the_status(self):
        bash = _bash_body(
            "security", "remediate", "FAILED (exit 3) TOKEN=not-a-real-secret /api/admin",
            rc="3", sha=SHA,
        )
        assert "not-a-real-secret" not in bash
        assert "/api/admin" not in bash
        assert "exit=1" in bash
        assert "audited-through" not in bash


class TestParse:
    def test_round_trip(self):
        body = nps.format_phase_comment(
            loop="ci-performance", phase="audit", exit_code=0, audited_through=SHA, timestamp=TS,
        )
        row = nps.parse_phase_status(body)
        assert row["loop"] == "ci-performance"
        assert row["phase"] == "audit"
        assert row["exit"] == 0
        assert row["audited_through"] == SHA
        assert row["timestamp"] == TS
        assert row["source"] == "marker"

    def test_html_comment_wins_and_visible_duplicate_is_one_row(self):
        body = nps.format_phase_comment(
            loop="reliability", phase="audit", exit_code=75, audited_through=SHA, timestamp=TS,
        )
        rows = nps.parse_phase_statuses(body)
        assert len(rows) == 1
        assert rows[0]["exit"] == 75
        assert rows[0]["audited_through"] == ""

    def test_machine_line_beats_a_contradictory_banner(self):
        body = (
            "> [!NOTE]\n> **Healthy** · testing · 2026-09-23 · audit\n\n"
            + nps.format_phase_comment(
                loop="testing", phase="audit", exit_code=1, timestamp=TS,
            )
        )
        assert nps.read_phase_status(body)["exit"] == 1
        assert nps.read_phase_status(body)["source"] == "marker"


class TestLegacyFallback:
    def test_banner_words(self):
        healthy = "> [!NOTE]\n> **Healthy** · testing · 2026-09-23 · audit → remediate → deliver\n"
        assert nps.infer_legacy_status(healthy)["exit"] == 0
        assert nps.infer_legacy_status(healthy)["loop"] == "testing"
        incomplete = "> [!CAUTION]\n> **Incomplete** · reliability · 2026-09-23 · remediate\n"
        row = nps.infer_legacy_status(incomplete)
        assert row["exit"] == 75 and row["phase"] == "remediate" and row["loop"] == "reliability"
        needs = "> [!WARNING]\n> **Needs you** · documentation · 2026-09-23 · deliver\n"
        assert nps.infer_legacy_status(needs)["exit"] == 0
        fixed = "> [!TIP]\n> **Fixed** · ci-performance · 2026-09-23 · audit\n"
        assert nps.infer_legacy_status(fixed)["exit"] == 0

    def test_old_phase_stamp_words(self):
        assert nps.infer_legacy_status("**audit** 20261018T120000 **OK**\n")["exit"] == 0
        assert nps.infer_legacy_status("**audit** 20261018T120000 **FAILED (exit 3)**\n")["exit"] == 1
        assert nps.infer_legacy_status("**deliver** 20261018T120000 **TIMEOUT**\n")["exit"] == 124
        assert nps.infer_legacy_status("**audit** 20261018T120000 **REFUSED**\n")["exit"] == 2
        refused = nps.infer_legacy_status("**audit** 20261018T120000 **GROUND TRUTH FAILED**\n")
        assert refused["exit"] == 70
        incomplete = "**audit** 20261018T120000 **INCOMPLETE (exit 0 without the phase-completion marker)**\n"
        assert nps.infer_legacy_status(incomplete)["exit"] == 75

    def test_legacy_keeps_an_audited_through_sha(self):
        body = f"**Healthy** · testing · 2026-09-23 · audit\naudited-through: `{SHA[:12]}`\n"
        row = nps.read_phase_status(body)
        assert row["source"] == "legacy"
        assert row["audited_through"] == SHA[:12]
        assert row["exit"] == 0

    def test_a_nonzero_marker_does_not_advance_the_cursor(self):
        failed = nps.format_phase_comment(
            loop="security", phase="audit", exit_code=1, audited_through=SHA, timestamp=TS,
        ) + f"audited-through: {SHA}\n"
        assert nps.cursor_allowed(failed) is False
        ok = nps.format_phase_comment(
            loop="security", phase="audit", exit_code=0, audited_through=SHA, timestamp=TS,
        )
        assert nps.cursor_allowed(ok) is True
        assert nps.cursor_allowed(f"audited-through: {SHA}\n") is True
        deliver = nps.format_phase_comment(
            loop="security", phase="deliver", exit_code=0, timestamp=TS,
        )
        assert nps.cursor_allowed(deliver) is False

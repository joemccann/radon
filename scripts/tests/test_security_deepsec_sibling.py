"""DeepSec is a sibling worker, not a second audit-remediate-deliver loop.

2026-09-13 cycle 20260913T000007: the 2h audit cap (rc=124) marked the whole
night TIMEOUT/INCOMPLETE while DeepSec was still writing under the private
scratch. last-audited already has a per-engine DeepSec field; the wrapper
still treated the night as all-or-nothing on the audit wall clock.

These tests pin Joe's locked split:
  * DeepSec still-running does not force audit TIMEOUT as the only story
  * harvest-when-ready folds verified findings into the shared private queue
  * harvest and remediate serialize on one queue lock (no double-lock / race)
  * the worker is not a second remediate/deliver nightly
  * public DeepSec dead-man copy stays sanitized
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import security_deepsec as deepsec  # noqa: E402

BASH = shutil.which("bash") or "/bin/bash"
WRAPPER = REPO / "scripts" / "security_nightly.sh"
WORKER = REPO / "scripts" / "security_deepsec_worker.sh"
SETUP = REPO / "scripts" / "setup_security_nightly.sh"
PLIST = REPO / "config" / "com.radon.security-deepsec.plist"
SKILL = REPO / ".claude" / "skills" / "security-nightly" / "SKILL.md"
HEAD = "aa" * 20
PREV = "bb" * 20


def _scratch(tmp_path: Path) -> Path:
    scratch = tmp_path / ".security-nightly-scratch"
    scratch.mkdir(parents=True)
    (scratch / "queue").mkdir()
    return scratch


def _write_status(scratch: Path, status: str, **extra) -> Path:
    payload = {"status": status, "updated_at": "2026-09-13T04:10:00Z", **extra}
    return deepsec.save_status(scratch, payload)


def _write_fast_complete(scratch: Path, head_sha: str = HEAD) -> None:
    (scratch / deepsec.FAST_ENGINES_MARKER).write_text(head_sha + "\n", encoding="utf-8")
    last = {"sha": PREV, "engines": {name: head_sha for name in deepsec.FAST_ENGINES}}
    last["engines"][deepsec.DEEPSEC_ENGINE] = PREV
    deepsec.save_last_audited(scratch, last)


def _export_payload(ids: list[str]) -> dict:
    return {
        "findings": [
            {"id": fid, "severity": "MEDIUM", "verified": True}
            for fid in ids
        ]
    }


class TestClassifyAuditDoesNotTimeoutSolelyOnDeepSec:
    def test_still_running_deepsec_is_not_timeout_when_fast_engines_finished(self, tmp_path):
        scratch = _scratch(tmp_path)
        _write_fast_complete(scratch)
        _write_status(scratch, "running", pid=4242)
        status, rc = deepsec.classify_audit(
            rc=124, scratch=scratch, head_sha=HEAD, cap_secs=7200
        )
        assert rc == 0, (status, rc)
        assert "TIMEOUT" not in status
        assert "still running" in status.lower()
        assert "fast engines" in status.lower()

    def test_timeout_still_wins_when_fast_engines_did_not_finish(self, tmp_path):
        scratch = _scratch(tmp_path)
        _write_status(scratch, "running", pid=4242)
        status, rc = deepsec.classify_audit(
            rc=124, scratch=scratch, head_sha=HEAD, cap_secs=7200
        )
        assert rc == 124
        assert status.startswith("TIMEOUT")

    def test_timeout_wins_when_completion_marker_is_for_an_older_head(self, tmp_path):
        scratch = _scratch(tmp_path)
        _write_fast_complete(scratch, PREV)
        _write_status(scratch, "running", pid=4242)

        status, rc = deepsec.classify_audit(
            rc=124, scratch=scratch, head_sha=HEAD, cap_secs=7200
        )

        assert rc == 124
        assert status.startswith("TIMEOUT")

    @pytest.mark.parametrize(
        "record",
        [
            "fast_engines: complete\nhead_sha: " + PREV + "\n",
            "fast_engines: complete\nhead_sha: not-a-sha\n",
        ],
    )
    def test_timeout_wins_when_run_record_is_stale_or_malformed(self, tmp_path, record):
        scratch = _scratch(tmp_path)
        run = scratch / "20260914-audit"
        run.mkdir()
        (run / "run-record.md").write_text(record, encoding="utf-8")
        _write_status(scratch, "running", pid=4242)

        status, rc = deepsec.classify_audit(
            rc=124, scratch=scratch, head_sha=HEAD, cap_secs=7200
        )

        assert rc == 124
        assert status.startswith("TIMEOUT")

    def test_exact_head_run_record_keeps_sibling_worker_timeout_exemption(self, tmp_path):
        scratch = _scratch(tmp_path)
        run = scratch / "20260914-audit"
        run.mkdir()
        (run / "run-record.md").write_text(
            "fast_engines: complete\nhead_sha: " + HEAD + "\n",
            encoding="utf-8",
        )
        _write_status(scratch, "running", pid=4242)

        status, rc = deepsec.classify_audit(
            rc=124, scratch=scratch, head_sha=HEAD, cap_secs=7200
        )

        assert rc == 0
        assert status == deepsec.AUDIT_OK_DEEPSEC_RUNNING

    def test_timeout_still_wins_when_deepsec_failed(self, tmp_path):
        scratch = _scratch(tmp_path)
        _write_fast_complete(scratch)
        _write_status(scratch, "failed")
        status, rc = deepsec.classify_audit(
            rc=124, scratch=scratch, head_sha=HEAD, cap_secs=7200
        )
        assert rc == 124
        assert status.startswith("TIMEOUT")

    def test_non_timeout_rc_is_unchanged(self, tmp_path):
        scratch = _scratch(tmp_path)
        _write_fast_complete(scratch)
        _write_status(scratch, "running")
        status, rc = deepsec.classify_audit(
            rc=7, scratch=scratch, head_sha=HEAD, cap_secs=7200
        )
        assert rc == 7
        assert "FAILED" in status


class TestLastAuditedEnginesAdvanceIndependently:
    def test_advancing_fast_engines_leaves_deepsec_sha(self, tmp_path):
        scratch = _scratch(tmp_path)
        state = {
            "sha": PREV,
            "engines": {
                "gitleaks": PREV,
                "deterministic": PREV,
                "claude_security": PREV,
                "deepsec": PREV,
            },
        }
        deepsec.save_last_audited(scratch, state)
        updated = deepsec.advance_engines(scratch, deepsec.FAST_ENGINES, HEAD)
        assert updated["engines"]["gitleaks"] == HEAD
        assert updated["engines"]["deepsec"] == PREV
        reloaded = deepsec.load_last_audited(scratch)
        assert reloaded["engines"]["deepsec"] == PREV

    def test_top_level_deepsec_field_is_the_engine_sha(self, tmp_path):
        scratch = _scratch(tmp_path)
        deepsec.save_last_audited(scratch, {"deepsec": PREV, "gitleaks": PREV})
        assert deepsec.engine_sha(deepsec.load_last_audited(scratch), "deepsec") == PREV


class TestHarvestWhenReady:
    def test_export_ready_folds_verified_findings_into_the_queue(self, tmp_path):
        scratch = _scratch(tmp_path)
        run = scratch / "20260913-audit"
        run.mkdir()
        export = run / "deepsec-verified-findings.json"
        export.write_text(json.dumps(_export_payload(["ds-1", "ds-2"])), encoding="utf-8")
        _write_status(
            scratch,
            "export_ready",
            run_id="20260913-audit",
            export_path=str(export),
            head_sha=HEAD,
        )
        result = deepsec.harvest(scratch)
        assert result.status == "harvested"
        assert result.harvested == 2
        assert result.skipped == 0
        queued = deepsec.load_queue(scratch)
        assert {row["id"] for row in queued} == {"ds-1", "ds-2"}
        assert deepsec.load_status(scratch)["status"] == "harvested"
        # Harvest is what advances the DeepSec engine, not a manual SHA bump.
        assert deepsec.engine_sha(deepsec.load_last_audited(scratch), "deepsec") == HEAD

    def test_still_running_is_a_no_op_harvest(self, tmp_path):
        scratch = _scratch(tmp_path)
        _write_status(scratch, "running", pid=9)
        result = deepsec.harvest(scratch)
        assert result.status == "still running"
        assert result.harvested == 0
        assert deepsec.load_queue(scratch) == []

    def test_second_harvest_does_not_duplicate(self, tmp_path):
        scratch = _scratch(tmp_path)
        run = scratch / "20260913-audit"
        run.mkdir()
        export = run / "deepsec-verified-findings.json"
        export.write_text(json.dumps(_export_payload(["ds-1"])), encoding="utf-8")
        _write_status(
            scratch,
            "export_ready",
            run_id="20260913-audit",
            export_path=str(export),
            head_sha=HEAD,
        )
        first = deepsec.harvest(scratch)
        second = deepsec.harvest(scratch)
        assert first.harvested == 1
        assert second.harvested == 0
        assert second.skipped == 1
        assert [row["id"] for row in deepsec.load_queue(scratch)] == ["ds-1"]

    def test_harvest_stdout_is_counts_only(self, tmp_path):
        scratch = _scratch(tmp_path)
        run = scratch / "20260913-audit"
        run.mkdir()
        export = run / "deepsec-verified-findings.json"
        export.write_text(
            json.dumps(
                {
                    "findings": [
                        {
                            "id": "ds-leak",
                            "title": "credential at /api/admin/secrets",
                            "file": "web/lib/secret.ts:12",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        _write_status(
            scratch,
            "export_ready",
            run_id="20260913-audit",
            export_path=str(export),
            head_sha=HEAD,
        )
        result = deepsec.harvest(scratch)
        line = result.public_line()
        assert "harvested=1" in line
        assert "/api/" not in line
        assert "secret.ts" not in line
        assert "credential" not in line.lower()


class TestQueueLockSerializesHarvestAndRemediate:
    def test_harvest_waits_out_a_held_remediate_lock(self, tmp_path):
        scratch = _scratch(tmp_path)
        run = scratch / "20260913-audit"
        run.mkdir()
        export = run / "deepsec-verified-findings.json"
        export.write_text(json.dumps(_export_payload(["ds-lock"])), encoding="utf-8")
        _write_status(
            scratch,
            "export_ready",
            run_id="20260913-audit",
            export_path=str(export),
            head_sha=HEAD,
        )
        order: list[str] = []
        lock = deepsec.QueueLock(scratch)

        def remediate() -> None:
            with lock:
                order.append("remediate-hold")
                time.sleep(0.25)
                order.append("remediate-release")

        def harvest() -> None:
            time.sleep(0.05)
            result = deepsec.harvest(scratch, timeout_secs=2.0)
            order.append(f"harvest-{result.status}")

        t1 = threading.Thread(target=remediate)
        t2 = threading.Thread(target=harvest)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        assert order[:2] == ["remediate-hold", "remediate-release"]
        assert order[-1] == "harvest-harvested"
        assert [row["id"] for row in deepsec.load_queue(scratch)] == ["ds-lock"]

    def test_consume_under_the_same_lock_cannot_race_a_harvest(self, tmp_path):
        scratch = _scratch(tmp_path)
        run = scratch / "20260913-audit"
        run.mkdir()
        export = run / "deepsec-verified-findings.json"
        export.write_text(json.dumps(_export_payload(["ds-a", "ds-b"])), encoding="utf-8")
        _write_status(
            scratch,
            "export_ready",
            run_id="20260913-audit",
            export_path=str(export),
            head_sha=HEAD,
        )
        seen: list[str] = []
        lock = deepsec.QueueLock(scratch)

        def consume() -> None:
            time.sleep(0.05)
            with lock:
                rows = deepsec.load_queue(scratch)
                seen.extend(row["id"] for row in rows)
                deepsec.replace_queue(scratch, [])

        t1 = threading.Thread(target=lambda: deepsec.harvest(scratch, timeout_secs=2.0))
        t2 = threading.Thread(target=consume)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        leftover = {row["id"] for row in deepsec.load_queue(scratch)}
        # Either consume saw both after harvest, or harvest wrote after consume
        # emptied an already-harvested queue. Never a torn half-write.
        assert leftover in (set(), {"ds-a", "ds-b"})
        if leftover:
            assert seen == []
        else:
            assert set(seen) in (set(), {"ds-a", "ds-b"})
        assert "ds-a" in leftover or "ds-a" in seen
        assert "ds-b" in leftover or "ds-b" in seen


class TestPublicDeepSecStatusIsSanitized:
    @pytest.mark.parametrize(
        "internal,public",
        [
            ("running", "still running"),
            ("failed", "failed"),
            ("export_ready", "export ready"),
            ("harvested", "harvested"),
        ],
    )
    def test_public_words_name_the_three_operator_states(self, internal, public):
        assert deepsec.public_status(internal) == public

    def test_public_status_never_echoes_a_path_or_route(self, tmp_path):
        scratch = _scratch(tmp_path)
        _write_status(
            scratch,
            "failed",
            log_path=str(scratch / "deepsec-process.log"),
            detail="matched /api/admin/secrets in web/lib/secret.ts:12",
        )
        line = deepsec.public_status_line(scratch)
        assert line == "DEEPSEC failed"
        assert "/api/" not in line
        assert "secret.ts" not in line
        assert "scratch" not in line


class TestWorkerIsNotASecondNightlyLoop:
    def test_worker_script_exists_and_has_no_remediate_deliver_cycle(self):
        assert WORKER.is_file()
        body = "\n".join(
            line
            for line in WORKER.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "run_phase remediate" not in body
        assert "run_phase deliver" not in body
        assert "security/<YYYY-MM-DD>" not in body
        assert "NIGHTLY DELIVER" not in body

    def test_plist_points_at_the_worker_not_cycle(self):
        resolved = (
            PLIST.read_text(encoding="utf-8")
            .replace("__DEEPSEC_REPO__", "/tmp/radon-security-deepsec")
            .replace("__WEEKEND_ROOT__", "/tmp/radon-weekend")
            .replace("__HOME__", "/tmp/home")
        )
        job = plistlib.loads(resolved.encode("utf-8"))
        assert job["Label"] == "com.radon.security-deepsec"
        program = " ".join(job["ProgramArguments"])
        assert "security_deepsec_worker.sh" in program
        assert program.rstrip().endswith("run")
        assert " cycle" not in program
        env = job["EnvironmentVariables"]
        assert env.get("DISABLE_AUTOUPDATER") == "1"
        assert set(env) <= {
            "PATH",
            "HOME",
            "DISABLE_AUTOUPDATER",
            "RADON_WEEKEND_REPO",
            "RADON_WEEKEND_DEEPSEC_REPO",
        }

    def test_setup_installs_both_jobs_and_both_labels(self):
        body = SETUP.read_text(encoding="utf-8")
        assert "com.radon.security-deepsec.plist" in body
        assert "com.radon.security-daily.plist" in body
        assert "gh label create security-deepsec" in body
        assert "gh label create security-nightly" in body

    def test_skill_tells_audit_to_harvest_not_wait(self):
        text = SKILL.read_text(encoding="utf-8")
        assert "sibling worker" in text.lower()
        assert "harvest" in text.lower()
        assert "still running" in text.lower()
        assert "export ready" in text.lower()


class TestWrapperAuditCapDoesNotOwnDeepSec:
    """Drive the real security wrapper: a 124 from the 2h cap is not TIMEOUT
    when fast engines finished and DeepSec is the sibling still chewing.
    """

    def _stub_bin(self, tmp_path: Path) -> Path:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        gh_log = tmp_path / "gh.log"
        bodies = tmp_path / "bodies.log"
        module = REPO / "scripts" / "security_deepsec.py"
        py = sys.executable
        stubs = {
            "gh": (
                "#!/bin/bash\n"
                f'printf "%s\\n" "$*" >> "{gh_log}"\n'
                'if [ "$1 $2" = "issue list" ]; then echo 4242; fi\n'
                'if [ "$1 $2" = "issue comment" ]; then\n'
                '  while [ $# -gt 0 ]; do\n'
                '    if [ "$1" = "--body" ]; then shift\n'
                f'      printf \'<<<COMMENT>>>%s\\n\' "$1" >> "{bodies}"\n'
                "      break\n"
                "    fi\n"
                "    shift\n"
                "  done\n"
                "fi\n"
                "exit 0\n"
            ),
            "claude": (
                "#!/bin/sh\n"
                "echo 'gitleaks: ok'\n"
                "exit 0\n"
            ),
            "timeout": (
                "#!/bin/bash\n"
                'while [ $# -gt 0 ]; do\n'
                '  case "$1" in\n'
                '    -k|--kill-after) shift 2 ;;\n'
                '    *) shift; break ;;\n'
                '  esac\n'
                'done\n'
                'if [ "${1##*/}" = "claude" ]; then "$@" ; exit 124; fi\n'
                'exec "$@"\n'
            ),
            "git": (
                "#!/bin/bash\n"
                'if [[ "$*" == *show*security_deepsec.py* ]]; then\n'
                f'  cat "{module}"\n'
                "  exit 0\n"
                "fi\n"
                'if [[ "$*" == *rev-parse* ]]; then echo "' + HEAD + '"; exit 0; fi\n'
                "exit 0\n"
            ),
            "python3": (
                "#!/bin/bash\n"
                f'exec "{py}" "$@"\n'
            ),
        }
        for name, body in stubs.items():
            exe = bin_dir / name
            exe.write_text(body, encoding="utf-8")
            exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return bin_dir

    def test_wrapper_reports_deepsec_still_running_not_timeout(self, tmp_path):
        weekend = tmp_path / "weekend"
        clone = weekend / "radon-security"
        (clone / "scripts").mkdir(parents=True)
        (clone / "logs" / "security-nightly").mkdir(parents=True)
        shutil.copy2(WRAPPER, clone / "scripts" / "security_nightly.sh")
        (clone / "scripts" / "security_nightly.sh").chmod(0o755)
        (clone / ".radon-weekend-runner").write_text("", encoding="utf-8")
        (clone / ".radon-security-runner").write_text("", encoding="utf-8")
        scratch = weekend / ".security-nightly-scratch"
        scratch.mkdir()
        (scratch / "queue").mkdir()
        _write_fast_complete(scratch)
        _write_status(scratch, "running", pid=99)

        bin_dir = self._stub_bin(tmp_path)
        bodies = tmp_path / "bodies.log"
        proc = subprocess.run(
            [BASH, str(clone / "scripts" / "security_nightly.sh"), "audit"],
            cwd=clone,
            env={
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "HOME": str(tmp_path / "home"),
                "RADON_WEEKEND_REPO": str(clone),
                "RADON_WEEKEND_SKIP_PRUNE": "1",
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
        comments = bodies.read_text(encoding="utf-8") if bodies.exists() else ""
        assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
        assert "TIMEOUT" not in comments
        assert "still running" in comments.lower()
        assert "fast engines" in comments.lower()

"""REL-221 (R-589, R-590): deploy-root-helper hygiene — verifier-unavailable
is not scratch-setup-failure, and an empty-parse role fallback is logged."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

CLOUD = Path(__file__).resolve().parent.parent
HELPER = CLOUD / "scripts" / "deploy-root-helper.sh"


def _function(name: str) -> str:
    body = HELPER.read_text()
    start = body.index(f"{name}()")
    return body[start : body.index("\n}", start) + 2]


def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=30
    )


class TestVerifierUnavailableVsScratchFailure:
    def test_missing_verifier_soft_passes_with_a_log_line(self, tmp_path):
        fn = _function("unit_candidate_verifies")
        candidate = tmp_path / "candidate"
        candidate.write_text("[Unit]\n")
        script = f"""
SYSTEMD_ANALYZE=""
SYSTEMD_UNIT_DIR="{tmp_path}"
RM=rm
{fn}
unit_candidate_verifies "{candidate}" "x.service"
"""
        result = _bash(script)
        assert result.returncode == 0
        assert "verif" in (result.stdout + result.stderr).lower()

    def test_scratch_setup_failure_skips_the_unit(self, tmp_path):
        """R-589: a failed mktemp -d returned success and INSTALLED the
        candidate unverified."""
        fn = _function("unit_candidate_verifies")
        candidate = tmp_path / "candidate"
        candidate.write_text("[Unit]\n")
        fake_analyze = tmp_path / "systemd-analyze"
        fake_analyze.write_text("#!/bin/sh\nexit 0\n")
        fake_analyze.chmod(0o755)
        unwritable = tmp_path / "units"
        unwritable.mkdir()
        unwritable.chmod(0o500)
        try:
            script = f"""
SYSTEMD_ANALYZE="{fake_analyze}"
SYSTEMD_UNIT_DIR="{unwritable}"
RM=rm
{fn}
unit_candidate_verifies "{candidate}" "x.service"
"""
            result = _bash(script)
            assert result.returncode != 0, (
                "a failed scratch setup soft-passed and would install the "
                "unit unverified"
            )
        finally:
            unwritable.chmod(0o700)


class TestRoleFallbackIsLogged:
    def test_existing_file_with_empty_parse_logs_the_fallback(self, tmp_path):
        fn = _function("read_host_role")
        envf = tmp_path / "env"
        envf.write_text("SOMETHING_ELSE=1\n")
        script = f"""
RADON_HOST_ROLE=""
RADON_DEPLOY_ENV_FILE="{envf}"
CONTROL_PLANE_ROOT=/nonexistent
{fn}
read_host_role
"""
        result = _bash(script)
        assert result.stdout.strip() == "combined"
        assert "combined" in result.stderr.lower() and "role" in result.stderr.lower(), (
            "the silent combined fallback hides a corrupt role file"
        )

    def test_absent_file_stays_quiet(self, tmp_path):
        fn = _function("read_host_role")
        script = f"""
RADON_HOST_ROLE=""
RADON_DEPLOY_ENV_FILE="{tmp_path}/missing"
CONTROL_PLANE_ROOT=/nonexistent
{fn}
read_host_role
"""
        result = _bash(script)
        assert result.stdout.strip() == "combined"
        assert result.stderr.strip() == ""

"""A production host env file must not carry RADON_SERVICE_TOKEN.

`RADON_SERVICE_TOKEN` authenticates the demo frontend to the DEMO VM's FastAPI
as a trusted service principal. It is a machine credential with no per-user
identity, so it also skips `ALLOWED_USER_IDS` /
`RADON_REQUIRE_OPERATOR_ALLOWLIST`, which only `verify_clerk_bearer` applies.
Prod is supposed to leave it unset (`cloud/.env.example` keeps it commented),
and `is_trusted_service_request` is inert while it is -- but nothing refused it
if an env edit ever set it on the app host.

This is the same interlock shape `RADON_AUTHLESS_TEST` already has on the web
side (`assertAuthlessTestFlagAbsentInProduction`, R-666): a flag that is safe
in one deployment must fail the OTHER deployment's boot rather than rely on an
operator remembering which file it belongs in. Only the production hosts run
`check-env.py` (deploy.sh, post-setup.sh, setup-vps.sh); the demo VM has its
own gates in `deploy-demo-vm-remote.sh`, so it is unaffected.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

CLOUD = Path(__file__).resolve().parents[1]
CHECK_ENV = CLOUD / "scripts" / "check-env.py"
REQUIRED_ENV = CLOUD / "config" / "required-env.txt"


def _load_check_env():
    spec = importlib.util.spec_from_file_location("radon_check_env_sti", CHECK_ENV)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _valid_assignments() -> dict[str, str]:
    keys = [
        line.strip()
        for line in REQUIRED_ENV.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assignments = {key: f"value_{key}" for key in keys}
    assignments.update(
        {
            "IB_GATEWAY_MODE": "cloud",
            "IB_GATEWAY_HOST": "127.0.0.1",
            "RADON_MODE": "hetzner",
            "NODE_ENV": "production",
            "RADON_REQUIRE_OPERATOR_ALLOWLIST": "1",
            "TRADING_MODE": "live",
            "IB_GATEWAY_PORT": "4001",
        }
    )
    return assignments


def _write_env(tmp_path: Path, assignments: dict[str, str]) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "".join(f"{key}={value}\n" for key, value in assignments.items()),
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    return env_file


def _run_check_env(env_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK_ENV), str(env_file), str(REQUIRED_ENV)],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestServiceTokenProductionInterlock:
    def test_baseline_production_env_passes(self, tmp_path: Path) -> None:
        """Guard the fixture: without the token the same file must still pass,
        so a failure below is the token and not a broken baseline."""
        result = _run_check_env(_write_env(tmp_path, _valid_assignments()))
        assert result.returncode == 0, result.stdout + result.stderr

    def test_service_token_present_fails_production_preflight(
        self, tmp_path: Path
    ) -> None:
        assignments = _valid_assignments()
        assignments["RADON_SERVICE_TOKEN"] = "demo-only-token"
        result = _run_check_env(_write_env(tmp_path, assignments))
        assert result.returncode == 1, result.stdout + result.stderr
        assert "RADON_SERVICE_TOKEN" in result.stdout

    def test_empty_service_token_is_not_a_failure(self, tmp_path: Path) -> None:
        """An explicitly blank assignment leaves the lane inert
        (`is_trusted_service_request` returns False on a falsy value), so it is
        not a misconfiguration and must not fail a deploy."""
        for blank in ("", "''", '""'):
            assignments = _valid_assignments()
            assignments["RADON_SERVICE_TOKEN"] = blank
            result = _run_check_env(_write_env(tmp_path, assignments))
            assert result.returncode == 0, (
                f"blank {blank!r} must pass; got {result.stdout + result.stderr}"
            )

    def test_invariant_is_reported_by_the_function_not_only_the_cli(self) -> None:
        mod = _load_check_env()
        assignments = _valid_assignments()
        assignments["RADON_SERVICE_TOKEN"] = "demo-only-token"
        errors = mod.production_invariant_errors(assignments)
        assert any("RADON_SERVICE_TOKEN" in error for error in errors), errors

    def test_example_env_keeps_the_key_commented(self) -> None:
        """The example file is the operator's template; an uncommented key
        there would walk straight into /etc/radon/env."""
        text = (CLOUD / ".env.example").read_text(encoding="utf-8")
        live = [
            line
            for line in text.splitlines()
            if line.strip().startswith("RADON_SERVICE_TOKEN=")
        ]
        assert live == [], live

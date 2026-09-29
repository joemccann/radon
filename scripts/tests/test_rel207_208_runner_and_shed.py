"""REL-207 (R-569, R-570, R-571) + REL-208 (R-572, R-573)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class TestHostRoleFailsClosed:
    def test_case_variants_gate_correctly(self, monkeypatch):
        from api.services import host_role

        for variant in ("App", "APP", " app "):
            monkeypatch.setenv("RADON_HOST_ROLE", variant)
            assert host_role() == "app", variant
        monkeypatch.setenv("RADON_HOST_ROLE", "Broker")
        assert host_role() == "broker"

    def test_garbage_maps_to_least_privileged_not_combined(self, monkeypatch):
        """R-570: 'garbage is combined' silently granted the most-privileged
        role to a typo on the app VM."""
        from api.services import host_role

        monkeypatch.setenv("RADON_HOST_ROLE", "appp")
        assert host_role() == "app"

    def test_unset_stays_combined(self, monkeypatch):
        from api.services import host_role

        monkeypatch.delenv("RADON_HOST_ROLE", raising=False)
        assert host_role() == "combined"


class TestPathFilterCasefolds:
    def test_case_variant_claude_md_arms_both_gates(self):
        from ci.path_filter import FAIL_CLOSED_DOC_BASENAMES, select_gates

        result = select_gates(["web/Claude.md"])
        assert result.python and result.web


class TestSetupInstallsDevRequirements:
    @pytest.mark.parametrize("prompt", ["security.md", "security-deepsec.md"])
    def test_the_loop_venv_gets_requirements_dev(self, prompt):
        """R-569: a loop venv lacked pytest-asyncio — 17 async auth tests
        false-red in the loop's own gate. The runner loops build their venv
        from the prompt; the security pre-run hook keeps .venv/ between phases."""
        body = (REPO / ".claude" / "runner-prompts" / prompt).read_text()
        assert "requirements-dev.txt" in body, prompt
        assert "pytest_asyncio" in body, f"{prompt} does not check the dev deps actually imported"


class TestCapacityShedContract:
    WRAPPERS = ("run_garch_refresh.sh", "run_leap_refresh.sh")

    def test_wrapper_literals_match_the_server_marker(self):
        """R-572: a reword of server.py's marker silently reverts both
        wrappers to instant-fail."""
        server = (SCRIPTS / "api" / "server.py").read_text()
        match = re.search(r'_CAPACITY_SHED_MARKER\s*=\s*"([^"]+)"', server)
        assert match, "server.py no longer defines _CAPACITY_SHED_MARKER"
        marker = match.group(1).lower()
        for wrapper in self.WRAPPERS:
            body = (SCRIPTS / wrapper).read_text()
            wmatch = re.search(r'CAPACITY_SHED_MARKER="([^"]+)"', body)
            assert wmatch, wrapper
            assert wmatch.group(1).lower() in marker or marker in wmatch.group(1).lower(), (
                f"{wrapper} shed marker {wmatch.group(1)!r} no longer matches "
                f"server.py {marker!r}"
            )

    @pytest.mark.parametrize("wrapper", WRAPPERS)
    def test_shed_budget_counts_wall_time_not_just_sleeps(self, wrapper):
        """R-573: slow marker-bodied 502s ran the wrapper past
        TimeoutStartSec into Result=timeout."""
        body = "\n".join(
            line for line in (SCRIPTS / wrapper).read_text().splitlines()
            if not line.lstrip().startswith("#")
        )
        loop = body[body.index("shed_waited=0"):]
        assert "attempt_started" in loop and "attempt_elapsed" in loop, (
            f"{wrapper} budgets only its sleeps; per-attempt curl wall time "
            "is unbounded and uncounted"
        )

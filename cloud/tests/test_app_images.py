"""P3 app-plane images: buildable, host-default, never Gateway/Caddy/docker.sock."""

from __future__ import annotations

import re
from pathlib import Path

CLOUD_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CLOUD_ROOT.parent
DOCKER_APP = REPO_ROOT / "docker" / "app"
PYTHON_DF = DOCKER_APP / "Dockerfile.python"
NODE_DF = DOCKER_APP / "Dockerfile.node"
DOCKERIGNORE = DOCKER_APP / ".dockerignore"
FLEET_DROPIN = (
    CLOUD_ROOT / "services" / "radon-.service.d" / "runtime-container.conf.example"
)
APP_UNITS = (
    "radon-api.service",
    "radon-nextjs.service",
    "radon-relay.service",
    "radon-monitor.service",
    "radon-newsfeed.service",
)
BOOTSTRAP = CLOUD_ROOT / "scripts" / "bootstrap-control-plane.sh"
SETUP = CLOUD_ROOT / "scripts" / "setup-vps.sh"
HELPER = CLOUD_ROOT / "scripts" / "deploy-root-helper.sh"


def _function_body(script: str, name: str) -> str:
    match = re.search(
        rf"^{name}\(\)\s*\{{\s*\n(.+?)\n\}}\s*$",
        script,
        re.DOTALL | re.MULTILINE,
    )
    assert match, f"{name}() function not found"
    return match.group(1)


def _readonly_array(script: str, name: str) -> str:
    match = re.search(rf"readonly(?: -a)? {name}=\((.*?)\)", script, re.DOTALL)
    assert match, f"{name} array not found"
    return match.group(1)


def _from_images(text: str) -> list[str]:
    return [line.split()[1] for line in text.splitlines() if line.startswith("FROM ")]


def _final_user(text: str) -> str | None:
    last_from = 0
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("FROM "):
            last_from = i
    user = None
    for line in lines[last_from:]:
        if line.startswith("USER "):
            user = line.split()[1]
    return user


def _start_lines(text: str) -> list[str]:
    return [
        line
        for line in text.splitlines()
        if line.startswith(("CMD", "ENTRYPOINT", "CMD ", "ENTRYPOINT "))
    ]


def _dropin_for(unit: str) -> Path:
    return CLOUD_ROOT / "services" / f"{unit}.d" / "runtime-container.conf.example"


class TestAppDockerfilesExist:
    def test_python_dockerfile_exists(self) -> None:
        assert PYTHON_DF.is_file()

    def test_node_dockerfile_exists(self) -> None:
        assert NODE_DF.is_file()

    def test_dockerignore_exists(self) -> None:
        assert DOCKERIGNORE.is_file()


class TestPythonImage:
    def test_base_and_cmd(self) -> None:
        text = PYTHON_DF.read_text(encoding="utf-8")
        assert _from_images(text) == ["python:3.13-slim"]
        assert "COPY requirements.txt" in text
        assert "scripts/requirements-api.txt" in text
        assert "WORKDIR /home/radon/radon" in text
        assert "uvicorn" in text
        assert "scripts.api.server:app" in text
        assert "0.0.0.0" in text
        assert "8321" in text
        assert "--proxy-headers" in text
        assert "127.0.0.1" in text

    def test_user_radon_is_final(self) -> None:
        text = PYTHON_DF.read_text(encoding="utf-8")
        assert _final_user(text) == "radon"
        assert "--uid 1000" in text
        assert "chmod 755 /home/radon" in text


class TestNodeImage:
    def test_base_copies_and_cmd(self) -> None:
        text = NODE_DF.read_text(encoding="utf-8")
        images = _from_images(text)
        assert images[-1] == "node:22-bookworm-slim"
        assert any("bun" in image for image in images)
        assert "1.3.14" in text
        assert "COPY web/" in text or "COPY web" in text
        assert "scripts/" in text
        assert "bun install --frozen-lockfile" in text
        assert "bun run build" in text
        assert "playwright" in text.lower()
        assert "bunx" not in text
        assert "next start" in text or '"next", "start"' in text or "npm run start" in text

    def test_playwright_install_uses_repo_root_binary(self) -> None:
        """Newsfeed imports playwright from the repo-root package
        (scripts/newsfeed/browser.js). `bun x playwright install` from
        WORKDIR web fetched a CLI revision that was not
        chromium_headless_shell-1217, so launch failed with Executable
        doesn't exist and the unit crash-looped (page 3e952746).
        """
        text = NODE_DF.read_text(encoding="utf-8")
        assert "bun x playwright" not in text
        assert "./node_modules/.bin/playwright install chromium chromium-headless-shell" in text
        workdir = None
        install_workdir = None
        for line in text.splitlines():
            if line.startswith("WORKDIR "):
                workdir = line.split(maxsplit=1)[1].strip()
            if "node_modules/.bin/playwright install" in line:
                install_workdir = workdir
        assert install_workdir == "/home/radon/radon", install_workdir

    def test_user_radon_is_final(self) -> None:
        text = NODE_DF.read_text(encoding="utf-8")
        assert _final_user(text) == "radon"
        assert "--uid 1000" in text
        assert "chmod 755 /home/radon" in text
        assert "web/public/data" in text
        assert "socat" in text

    def test_clerk_public_env_is_required_at_build(self) -> None:
        text = NODE_DF.read_text(encoding="utf-8")
        assert 'ARG NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=""' not in text
        assert "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" in text
        assert 'test -n "$NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"' in text


class TestImageSafety:
    def test_docker_app_files_omit_docker_sock(self) -> None:
        for path in DOCKER_APP.iterdir():
            if path.is_file():
                assert "docker.sock" not in path.read_text(encoding="utf-8"), path.name

    def test_dockerfiles_do_not_from_caddy(self) -> None:
        for path in (PYTHON_DF, NODE_DF):
            for image in _from_images(path.read_text(encoding="utf-8")):
                assert "caddy" not in image.lower(), path.name

    def test_dockerfiles_do_not_start_ib_gateway(self) -> None:
        for path in (PYTHON_DF, NODE_DF):
            text = path.read_text(encoding="utf-8")
            for line in _start_lines(text):
                assert "ib-gateway" not in line.lower(), path.name
            assert "privileged" not in text.lower()

    def test_dockerignore_pins(self) -> None:
        text = DOCKERIGNORE.read_text(encoding="utf-8")
        for pattern in (".git", "data/replica.db", "**/.env", "cloud/", "docker/ib-gateway"):
            assert pattern in text, pattern


class TestRuntimeContainerDropin:
    def test_fleet_example_is_commented_and_forbids_install(self) -> None:
        assert FLEET_DROPIN.is_file()
        text = FLEET_DROPIN.read_text(encoding="utf-8")
        assert "docker.sock" not in text
        assert "DO NOT" in text or "MUST NOT" in text
        assert "radon-ib-gateway" in text
        assert "radon-health" in text
        for line in text.splitlines():
            if line.strip():
                assert line.lstrip().startswith("#")

    def test_per_unit_examples_call_wrapper_and_stay_commented(self) -> None:
        for unit in APP_UNITS:
            path = _dropin_for(unit)
            assert path.is_file(), unit
            text = path.read_text(encoding="utf-8")
            assert "docker.sock" not in text, unit
            assert "RADON_RUNTIME=container" in text, unit
            assert "radon-app-runtime run %n" in text, unit
            assert "NotifyAccess=all" in text, unit
            assert "User=root" in text, unit
            for line in text.splitlines():
                if line.strip():
                    assert line.lstrip().startswith("#"), f"{unit}: {line}"

    def test_example_not_in_bootstrap_sources(self) -> None:
        sources = _readonly_array(BOOTSTRAP.read_text(encoding="utf-8"), "SOURCES")
        assert "runtime-container.conf.example" not in sources

    def test_example_not_in_setup_service_files(self) -> None:
        units = _readonly_array(SETUP.read_text(encoding="utf-8"), "SERVICE_FILES")
        assert "runtime-container.conf.example" not in units

    def test_fleet_dropin_installs_only_common_conf(self) -> None:
        body = _function_body(SETUP.read_text(encoding="utf-8"), "install_fleet_dropin")
        assert "common.conf" in body
        assert "runtime-container" not in body

    def test_helper_control_plane_includes_runtime_wrapper(self) -> None:
        sources = _readonly_array(HELPER.read_text(encoding="utf-8"), "CONTROL_PLANE_SOURCES")
        targets = _readonly_array(HELPER.read_text(encoding="utf-8"), "CONTROL_PLANE_TARGETS")
        assert "scripts/radon-app-runtime.sh" in sources
        assert "/usr/local/sbin/radon-app-runtime" in targets
        assert "runtime-container.conf.example" not in sources

    def test_live_dropins_are_control_plane_and_type_simple(self) -> None:
        sources = _readonly_array(BOOTSTRAP.read_text(encoding="utf-8"), "SOURCES")
        helper = _readonly_array(HELPER.read_text(encoding="utf-8"), "CONTROL_PLANE_SOURCES")
        assert "radon-ib-gateway.service.d" not in sources
        assert "radon-health.service.d" not in sources
        for unit in APP_UNITS:
            rel = f"services/{unit}.d/runtime-container.conf"
            live = CLOUD_ROOT / "services" / f"{unit}.d" / "runtime-container.conf"
            assert live.is_file(), unit
            text = live.read_text(encoding="utf-8")
            assert "Type=simple" in text, unit
            assert "WatchdogSec=infinity" in text, unit
            assert "ExecStart=/usr/local/sbin/radon-app-runtime run %n" in text, unit
            assert "ExecStartPre=" in text, unit
            assert "ib-gateway" not in text, unit
            assert rel in sources, unit
            assert rel in helper, unit

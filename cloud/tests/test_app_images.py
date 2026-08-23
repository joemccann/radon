"""Static pins for app-plane image scaffolding (not production runtime)."""

from __future__ import annotations

import re
from pathlib import Path

CLOUD_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CLOUD_ROOT.parent
DOCKER_APP = REPO_ROOT / "docker" / "app"
PYTHON_DF = DOCKER_APP / "Dockerfile.python"
NODE_DF = DOCKER_APP / "Dockerfile.node"
DOCKERIGNORE = DOCKER_APP / ".dockerignore"
EXAMPLE_DROPIN = (
    CLOUD_ROOT / "services" / "radon-.service.d" / "runtime-container.conf.example"
)
BOOTSTRAP = CLOUD_ROOT / "scripts" / "bootstrap-control-plane.sh"
SETUP = CLOUD_ROOT / "scripts" / "setup-vps.sh"


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
        assert "127.0.0.1" in text
        assert "8321" in text

    def test_user_radon_is_final(self) -> None:
        text = PYTHON_DF.read_text(encoding="utf-8")
        assert _final_user(text) == "radon"


class TestNodeImage:
    def test_base_copies_and_cmd(self) -> None:
        text = NODE_DF.read_text(encoding="utf-8")
        assert _from_images(text) == ["node:22-bookworm-slim"]
        assert "COPY web/" in text
        assert "scripts/ib_realtime_server.js" in text
        assert "scripts/newsfeed/" in text
        assert "npm ci --omit=dev" in text
        assert '"npm", "run", "start"' in text or "npm run start" in text

    def test_user_radon_is_final(self) -> None:
        text = NODE_DF.read_text(encoding="utf-8")
        assert _final_user(text) == "radon"


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
    def test_example_exists_and_is_commented(self) -> None:
        assert EXAMPLE_DROPIN.is_file()
        text = EXAMPLE_DROPIN.read_text(encoding="utf-8")
        assert "docker.sock" not in text
        assert "RADON_RUNTIME=container" in text
        assert (
            "ExecStart=docker run --network host --env-file /etc/radon/env "
            "--rm --name %N"
        ) in text
        for line in text.splitlines():
            if line.strip():
                assert line.lstrip().startswith("#")

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

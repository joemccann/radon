"""App-plane host runtime must boot with GHCR unreachable."""

from __future__ import annotations

import re
from pathlib import Path

CLOUD = Path(__file__).resolve().parents[1]
REPO = CLOUD.parent
SERVICES = CLOUD / "services"
DEPLOY = CLOUD / "scripts" / "deploy.sh"
DROPIN = CLOUD / "services" / "radon-.service.d" / "runtime-container.conf.example"
UNIT_DROPINS = tuple(
    CLOUD / "services" / f"{name}.d" / "runtime-container.conf"
    for name in (
        "radon-api.service",
        "radon-nextjs.service",
        "radon-relay.service",
        "radon-monitor.service",
        "radon-newsfeed.service",
    )
)
BOOTSTRAP = CLOUD / "scripts" / "bootstrap-control-plane.sh"
SETUP = CLOUD / "scripts" / "setup-vps.sh"

APP_UNITS = (
    "radon-nextjs.service",
    "radon-api.service",
    "radon-relay.service",
    "radon-monitor.service",
    "radon-newsfeed.service",
)


def test_app_units_execstart_host_binaries() -> None:
    for name in APP_UNITS:
        text = (SERVICES / name).read_text(encoding="utf-8")
        starts = [
            line.split("=", 1)[1]
            for line in text.splitlines()
            if line.startswith("ExecStart=")
        ]
        assert starts, name
        for cmd in starts:
            assert "docker " not in cmd, name
            assert "ghcr.io" not in cmd, name
            assert cmd.startswith(("/", "/usr/", "/home/radon/")) or cmd.startswith(
                "/usr/bin/"
            )


def test_deploy_sh_never_docker_pulls() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    assert "docker pull" not in text
    assert "ghcr.io" not in text
    assert "RADON_RUNTIME" in text


def test_container_dropin_is_not_a_live_unit() -> None:
    """The FLEET template is inert; the per-unit drop-ins are live by design.

    This case required every per-unit drop-in to be fully commented out. That
    stopped being true when bootstrap and `refresh_install_file` began
    installing the real `.conf` for all five on every deploy, which is exactly
    what R-420 filed: the `.conf.example` files said "MUST NOT be installed"
    while the installer shipped their siblings automatically. The half that
    still holds — no fleet-wide runtime-container drop-in, and no `.example`
    reaches an installer — is what is asserted now.
    """
    text = DROPIN.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip():
            assert line.lstrip().startswith("#"), DROPIN.name
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    assert "runtime-container.conf.example" not in bootstrap
    setup = SETUP.read_text(encoding="utf-8")
    assert "runtime-container.conf.example" not in setup
    # ...and the live ones are control-plane artifacts, not stray files.
    helper = (CLOUD / "scripts" / "deploy-root-helper.sh").read_text(encoding="utf-8")
    for path in UNIT_DROPINS:
        assert path.is_file(), path
        rel = path.relative_to(CLOUD).as_posix()
        assert rel in helper, rel


def test_live_units_do_not_reference_ghcr() -> None:
    hits = []
    for path in SERVICES.glob("*.service"):
        text = path.read_text(encoding="utf-8")
        if "ghcr.io" in text or re.search(r"^ExecStart=docker ", text, re.M):
            hits.append(path.name)
    assert hits == []

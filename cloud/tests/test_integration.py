"""Cross-file consistency tests for the radon-cloud project.

Verifies that ports, paths, dependencies, security settings, and file
structure are consistent across systemd units, Caddyfile, docker-compose,
shell scripts, and documentation.
"""

import os
import pathlib
import re
import subprocess

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_text(path):
    return path.read_text(encoding="utf-8")


def read_all_services(services_dir):
    return {
        f.name: read_text(f)
        for f in services_dir.iterdir()
        if f.suffix in (".service", ".timer")
    }


def read_text_lossy(path):
    return path.read_text(encoding="utf-8", errors="ignore")


def tracked_files(root):
    """Return every git-tracked file under ``root`` as an absolute Path."""
    try:
        listing = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        return _walk_source_files(root)
    return [
        root / rel
        for rel in listing.decode("utf-8").split("\0")
        if rel and (root / rel).is_file()
    ]


UNTRACKED_DIRS = {".git", "__pycache__", "node_modules", ".next", ".venv"}


def _walk_source_files(root):
    # Fallback ONLY when git is unavailable: prune build artifacts so a
    # constant-folded fixture in a .pyc cannot masquerade as a tracked secret.
    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in UNTRACKED_DIRS]
        results.extend(
            pathlib.Path(dirpath) / fname
            for fname in filenames
            if not fname.endswith(".pyc")
        )
    return results


SECRET_PATTERNS = [
    r"sk_live_[a-zA-Z0-9]{20,}",
    r"sk_test_[a-zA-Z0-9]{20,}",
    r"pk_live_[a-zA-Z0-9]{20,}",
    r"ghp_[a-zA-Z0-9]{36,}",
    r"AKIA[0-9A-Z]{16}",
]


def scan_for_secrets(paths, read=read_text_lossy):
    """Return one violation line per (path, pattern) with a secret-shaped match."""
    violations = []
    for path in paths:
        content = read(path)
        for pattern in SECRET_PATTERNS:
            matches = re.findall(pattern, content)
            if matches:
                violations.append(f"{path}: matched {pattern} -> {matches}")
    return violations


# ---------------------------------------------------------------------------
# Port Consistency
# ---------------------------------------------------------------------------

class TestPortConsistency:

    def test_caddyfile_routes_to_relay_port_8765(self, caddy_dir):
        caddyfile = read_text(caddy_dir / "Caddyfile")
        assert "127.0.0.1:8765" in caddyfile

    def test_deploy_health_checks_fastapi_port_8321(self, scripts_dir):
        deploy = read_text(scripts_dir / "deploy.sh")
        assert "8321" in deploy

    def test_api_service_binds_port_8321(self, services_dir):
        api = read_text(services_dir / "radon-api.service")
        assert "--port 8321" in api

    def test_caddyfile_api_route_matches_api_service_port(self, caddy_dir, services_dir):
        caddyfile = read_text(caddy_dir / "Caddyfile")
        api = read_text(services_dir / "radon-api.service")
        assert "127.0.0.1:8321" in caddyfile
        assert "--port 8321" in api

    def test_relay_service_corresponds_to_port_8765(self, caddy_dir):
        caddyfile = read_text(caddy_dir / "Caddyfile")
        assert re.search(r"handle\s+/ws", caddyfile)
        assert "127.0.0.1:8765" in caddyfile

    def test_nextjs_service_corresponds_to_port_3000(self, caddy_dir, services_dir):
        caddyfile = read_text(caddy_dir / "Caddyfile")
        nextjs = read_text(services_dir / "radon-nextjs.service")
        assert "127.0.0.1:3000" in caddyfile
        assert "radon-nextjs" in nextjs.lower() or "Next.js" in nextjs

    def test_docker_compose_exposes_live_and_paper_gateway_ports(self, root):
        compose = read_text(root / "docker-compose.yml")
        # The image's socat listeners are 4003 (live) and 4004 (paper).
        assert "4001:4003" in compose
        assert "4002:4004" in compose


# ---------------------------------------------------------------------------
# Path Consistency
# ---------------------------------------------------------------------------

class TestPathConsistency:

    def test_all_services_share_same_environment_file_path(self, services_dir):
        services = read_all_services(services_dir)
        env_paths = set()
        for name, content in services.items():
            if name.startswith(("radon-grok-page-responder", "radon-flex-pull", "radon-mcp")):
                continue
            match = re.search(r"EnvironmentFile=(.+)", content)
            if match:
                env_paths.add(match.group(1).strip())
        assert len(env_paths) == 1, f"Expected one EnvironmentFile path, got: {env_paths}"

    def test_environment_file_path_matches_env_example_location(self, services_dir):
        services = read_all_services(services_dir)
        env_paths = set()
        for name, content in services.items():
            match = re.search(r"EnvironmentFile=(.+)", content)
            if match:
                env_paths.add((name, match.group(1).strip()))
        expected = "/etc/radon/env"
        stripped = "/home/radon/radon-page-responder.env"
        values = {path for _, path in env_paths}
        assert expected in values, f"Expected {expected}, got {values}"
        responder = [path for name, path in env_paths if "grok-page-responder" in name]
        assert responder == [stripped], f"responder env {responder}"

    def test_ib_gateway_working_directory_matches_docker_compose_location(self, services_dir):
        gateway = read_text(services_dir / "radon-ib-gateway.service")
        match = re.search(r"WorkingDirectory=(.+)", gateway)
        assert match
        working_dir = match.group(1).strip()
        assert working_dir == "/home/radon/radon/cloud"

    def test_setup_vps_references_all_service_files(self, scripts_dir, services_dir):
        setup = read_text(scripts_dir / "setup-vps.sh")
        service_files = [
            f.name for f in services_dir.iterdir()
            if f.suffix in (".service", ".timer")
        ]
        for svc in service_files:
            assert svc in setup, f"setup-vps.sh does not reference {svc}"

    def test_services_with_radon_workdir_use_same_venv(self, services_dir):
        services = read_all_services(services_dir)
        venv_paths = set()
        for name, content in services.items():
            if re.search(r"WorkingDirectory=/home/radon/radon(?:/\S+)?$", content, re.M) \
                    and "radon-page-responder" not in content \
                    and "radon-cloud" not in content:
                for match in re.finditer(r"(/home/radon/radon/\.venv\S*)", content):
                    venv_paths.add(
                        re.sub(r"/bin/[^/\s]+$", "", match.group(1))
                    )
        assert len(venv_paths) <= 1, f"Multiple venv paths found: {venv_paths}"
        if venv_paths:
            assert "/home/radon/radon/.venv" in venv_paths


# ---------------------------------------------------------------------------
# Dependency Chain
# ---------------------------------------------------------------------------

class TestDependencyChain:

    def _services_depending_on(self, services_dir, target):
        services = read_all_services(services_dir)
        dependents = []
        for name, content in services.items():
            if not name.endswith(".service"):
                continue
            if name == target:
                continue
            after_line = re.search(r"After=(.+)", content)
            requires_line = re.search(r"Requires=(.+)", content)
            deps = ""
            if after_line:
                deps += after_line.group(1)
            if requires_line:
                deps += " " + requires_line.group(1)
            if target in deps:
                dependents.append(name)
        return sorted(dependents)

    def test_ib_gateway_dependents(self, services_dir):
        dependents = self._services_depending_on(services_dir, "radon-ib-gateway.service")
        expected = sorted([
            "radon-api.service",
            "radon-relay.service",
            "radon-monitor.service",
            "radon-refresh.service",
            "radon-portfolio-sync.service",
        ])
        assert dependents == expected

    def test_monitor_depends_on_api(self, services_dir):
        monitor = read_text(services_dir / "radon-monitor.service")
        assert "radon-api.service" in monitor

    def test_timer_triggers_refresh_service(self, services_dir):
        timer = read_text(services_dir / "radon-refresh.timer")
        assert "[Timer]" in timer
        service = services_dir / "radon-refresh.service"
        assert service.exists(), "radon-refresh.service must exist for timer to trigger"


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

class TestSecurity:

    def test_no_unexpected_wildcard_bindings_in_runtime_configs(self, root):
        runtime_paths = [root / "docker-compose.yml", root / "caddy" / "Caddyfile"]
        runtime_paths.extend((root / "services").glob("*.service"))
        violations = []
        for path in runtime_paths:
            content = read_text(path)
            for lineno, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "0.0.0.0" not in stripped:
                    continue
                if path.name == "radon-api.service" and "--host 0.0.0.0" in stripped:
                    continue
                violations.append(f"{path}:{lineno}: {stripped}")
        assert not violations, (
            "Found unexpected wildcard bindings in runtime configuration:\n"
            + "\n".join(violations)
        )

    def test_docker_compose_ports_bound_to_localhost(self, root):
        # Policy: published ports bind to loopback OR the VPS Tailscale interface
        # IP (100.x), never to the public wildcard 0.0.0.0. IB Gateway 4001 is
        # reachable on loopback (VPS FastAPI) and over Tailscale (laptop) only.
        compose = read_text(root / "docker-compose.yml")
        port_lines = re.findall(r'"(.+?)"', compose)
        for port_mapping in port_lines:
            if ":" in port_mapping and any(c.isdigit() for c in port_mapping):
                # A `${VAR:-default}` host part is judged by its default: that
                # is what binds when the env leaves the override unset.
                bind = re.sub(r"^\$\{[A-Z0-9_]+:-([^}]+)\}", r"\1", port_mapping)
                bound_to_loopback = bind.startswith("127.0.0.1:")
                bound_to_tailscale = bind.startswith("100.")
                assert bound_to_loopback or bound_to_tailscale, (
                    f"Port mapping {port_mapping} must bind to 127.0.0.1 or the "
                    "Tailscale interface IP (100.x), never 0.0.0.0"
                )

    def test_gitignore_includes_env(self, root):
        gitignore = read_text(root / ".gitignore")
        assert ".env" in gitignore

    def test_no_real_secrets_in_tracked_files(self, root):
        violations = scan_for_secrets(tracked_files(root))
        assert not violations, (
            "Found potential real secrets in tracked files:\n"
            + "\n".join(violations)
        )

    def test_secret_scan_set_is_the_tracked_set(self, root):
        scanned = tracked_files(root)
        assert root / "tests" / "test_next_clerk_guard.py" in scanned
        assert root / "tests" / "test_integration.py" in scanned
        artifacts = [p for p in scanned if "__pycache__" in p.parts or p.suffix == ".pyc"]
        assert not artifacts, f"scan set includes untracked build artifacts: {artifacts}"

    def test_secret_scan_catches_planted_clerk_key(self, tmp_path):
        # Built at runtime so CPython cannot fold it into this file's .pyc and
        # so no tracked file ever carries a key-shaped literal.
        planted = "pk_live_" + "".join(chr(c) for c in range(ord("a"), ord("a") + 20))
        clean = tmp_path / "clean.txt"
        clean.write_text("nothing to see\n", encoding="utf-8")
        leaked = tmp_path / "leaked.txt"
        leaked.write_text(f"key={planted}\n", encoding="utf-8")

        assert scan_for_secrets([clean]) == []
        violations = scan_for_secrets([clean, leaked])
        assert len(violations) == 1
        assert str(leaked) in violations[0]
        assert planted in violations[0]

    def test_fastapi_tailnet_bind_preserves_proxy_trust_boundary(self, services_dir):
        api = read_text(services_dir / "radon-api.service")
        assert "--host 0.0.0.0" in api
        assert "--proxy-headers" in api
        assert "--forwarded-allow-ips 127.0.0.1" in api


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------

class TestCompleteness:

    PLANNED_SERVICES = [
        "radon-nextjs.service",
        "radon-api.service",
        "radon-relay.service",
        "radon-monitor.service",
        "radon-refresh.service",
        "radon-refresh.timer",
        "radon-ib-gateway.service",
    ]

    def test_every_planned_service_exists_as_systemd_unit(self, services_dir):
        existing_units = {f.name for f in services_dir.iterdir()}
        for svc in self.PLANNED_SERVICES:
            assert svc in existing_units, f"planned unit {svc} is missing"

    def test_readme_references_all_services(self, root):
        readme = read_text(root / "README.md")
        service_names = [
            "radon-ib-gateway",
            "radon-nextjs",
            "radon-api",
            "radon-relay",
            "radon-monitor",
            "radon-refresh",
        ]
        for name in service_names:
            assert name in readme, f"README.md does not mention {name}"

    def test_env_example_has_required_variables(self, root):
        env_example = read_text(root / ".env.example")
        required_vars = [
            "IB_GATEWAY_HOST",
            "IB_GATEWAY_PORT",
            "CLERK_JWKS_URL",
            "CLERK_ISSUER",
            "ALLOWED_USER_IDS",
            "UW_TOKEN",
            "RADON_API_URL",
            "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
            "CLERK_SECRET_KEY",
        ]
        for var in required_vars:
            assert var in env_example, f".env.example is missing {var}"

    def test_claude_md_exists(self, root):
        assert (root / "CLAUDE.md").exists()

    def test_required_directories_exist(self, root):
        for dirname in ("services", "caddy", "scripts"):
            assert (root / dirname).is_dir(), f"Directory {dirname}/ is missing"

    def test_docker_compose_exists(self, root):
        assert (root / "docker-compose.yml").exists()

    def test_all_scripts_are_executable(self, scripts_dir):
        scripts = [f for f in scripts_dir.iterdir() if f.suffix == ".sh"]
        assert scripts, "No .sh scripts found"
        for script in scripts:
            assert os.access(script, os.X_OK), f"{script.name} is not executable"


# ---------------------------------------------------------------------------
# File Tree
# ---------------------------------------------------------------------------

class TestFileTree:

    def test_file_tree_matches_plan_structure(self, root):
        planned_files = [
            "docker-compose.yml",
            "services/radon-nextjs.service",
            "services/radon-api.service",
            "services/radon-relay.service",
            "services/radon-monitor.service",
            "services/radon-refresh.service",
            "services/radon-refresh.timer",
            "caddy/Caddyfile",
            "scripts/setup-vps.sh",
            "scripts/deploy.sh",
            "scripts/migrate-data.sh",
            ".env.example",
            "CLAUDE.md",
            "README.md",
        ]
        for relative_path in planned_files:
            full = root / relative_path
            assert full.exists(), f"required cloud file {relative_path} is missing"

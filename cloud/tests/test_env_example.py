"""Tests for .env.example configuration."""

import re


KNOWN_PLACEHOLDER_PATTERNS = [
    "sk_live_...",
    "pk_live_...",
    "user_...",
    "your-app",
]


def read_env_example(root):
    return (root / ".env.example").read_text()


def parse_env_vars(content):
    """Return dict of KEY=VALUE pairs from non-comment, non-empty lines."""
    result = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, _, value = stripped.partition("=")
        result[key] = value
    return result


class TestFileExists:
    def test_env_example_exists(self, root):
        assert (root / ".env.example").exists()


class TestIBGatewayVariables:
    REQUIRED = [
        "IB_GATEWAY_HOST",
        "IB_GATEWAY_PORT",
        "TWS_USERID",
        "TWS_PASSWORD",
        "TRADING_MODE",
    ]

    def test_contains_ib_variables(self, root):
        env_vars = parse_env_vars(read_env_example(root))
        for var in self.REQUIRED:
            assert var in env_vars, f"Missing required variable: {var}"


class TestClerkVariables:
    REQUIRED = [
        "CLERK_JWKS_URL",
        "CLERK_ISSUER",
        "CLERK_SECRET_KEY",
        "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
    ]

    def test_contains_clerk_variables(self, root):
        env_vars = parse_env_vars(read_env_example(root))
        for var in self.REQUIRED:
            assert var in env_vars, f"Missing required Clerk variable: {var}"


class TestAllowedUserIDs:
    def test_contains_allowed_user_ids(self, root):
        env_vars = parse_env_vars(read_env_example(root))
        assert "ALLOWED_USER_IDS" in env_vars


class TestProbeFreshnessToken:
    def test_contains_probe_freshness_token(self, root):
        env_vars = parse_env_vars(read_env_example(root))
        assert "RADON_PROBE_FRESHNESS_TOKEN" in env_vars


class TestAPIURLs:
    REQUIRED = [
        "RADON_API_URL",
        "NEXT_PUBLIC_RADON_API_URL",
        "NEXT_PUBLIC_IB_REALTIME_WS_URL",
    ]

    def test_contains_api_urls(self, root):
        env_vars = parse_env_vars(read_env_example(root))
        for var in self.REQUIRED:
            assert var in env_vars, f"Missing required API URL variable: {var}"


class TestDomainVariable:
    def test_contains_domain(self, root):
        env_vars = parse_env_vars(read_env_example(root))
        assert "DOMAIN" in env_vars


class TestUWToken:
    def test_contains_uw_token(self, root):
        env_vars = parse_env_vars(read_env_example(root))
        assert "UW_TOKEN" in env_vars


class TestIBSessionVariables:
    def test_contains_vnc_server_password(self, root):
        env_vars = parse_env_vars(read_env_example(root))
        assert "VNC_SERVER_PASSWORD" in env_vars

    def test_contains_existing_session_detected_action(self, root):
        env_vars = parse_env_vars(read_env_example(root))
        assert "EXISTING_SESSION_DETECTED_ACTION" in env_vars
        assert env_vars["EXISTING_SESSION_DETECTED_ACTION"] == "primary"


class TestNodeEnvironment:
    def test_contains_node_env(self, root):
        env_vars = parse_env_vars(read_env_example(root))
        assert "NODE_ENV" in env_vars

    def test_contains_port(self, root):
        env_vars = parse_env_vars(read_env_example(root))
        assert "PORT" in env_vars


class TestNoRealSecrets:
    def test_no_actual_secrets_committed(self, root):
        env_vars = parse_env_vars(read_env_example(root))
        clerk_secret = env_vars.get("CLERK_SECRET_KEY", "")
        assert clerk_secret == "sk_live_..." or clerk_secret == "", (
            "CLERK_SECRET_KEY must be a placeholder, not a real key"
        )
        publishable = env_vars.get("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "")
        assert publishable == "pk_live_..." or publishable == "", (
            "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY must be a placeholder"
        )
        tws_password = env_vars.get("TWS_PASSWORD", "")
        assert tws_password == "", (
            "TWS_PASSWORD must be empty in .env.example"
        )


class TestDefaults:
    def test_ib_gateway_host_defaults_to_localhost(self, root):
        env_vars = parse_env_vars(read_env_example(root))
        assert env_vars["IB_GATEWAY_HOST"] == "127.0.0.1"

    def test_paper_gateway_port_defaults_to_4002(self, root):
        env_vars = parse_env_vars(read_env_example(root))
        assert env_vars["IB_GATEWAY_PORT"] == "4002"

    def test_trading_mode_defaults_to_paper(self, root):
        env_vars = parse_env_vars(read_env_example(root))
        assert env_vars["TRADING_MODE"] == "paper"

    def test_node_env_defaults_to_production(self, root):
        env_vars = parse_env_vars(read_env_example(root))
        assert env_vars["NODE_ENV"] == "production"

    def test_port_defaults_to_3000(self, root):
        env_vars = parse_env_vars(read_env_example(root))
        assert env_vars["PORT"] == "3000"


class TestFileFormat:
    def test_every_line_is_comment_empty_or_key_value(self, root):
        content = read_env_example(root)
        for i, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            assert re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", stripped), (
                f"Line {i} is not valid KEY=VALUE format: {stripped!r}"
            )


class TestGitignore:
    def test_gitignore_includes_env(self, root):
        gitignore = root / ".gitignore"
        assert gitignore.exists(), ".gitignore must exist"
        lines = gitignore.read_text().splitlines()
        env_patterns = [line.strip() for line in lines if not line.strip().startswith("#")]
        assert ".env" in env_patterns, ".gitignore must include .env"


class TestOperatorAllowlistInterlock:
    """REL-029 (R-054): the fail-closed allowlist interlock must be enforced.

    RADON_REQUIRE_OPERATOR_ALLOWLIST=1 is asserted in auth code comments but
    was absent from the deploy contract — a blanked/typo'd ALLOWED_USER_IDS
    on radon-api would silently let any valid Clerk JWT through. The key
    belongs in required-env.txt so check-env.py fails the deploy preflight
    when it is missing or empty.
    """

    def _contract_keys(self, root):
        contract = (root / "config" / "required-env.txt").read_text()
        return [
            line.strip()
            for line in contract.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

    def test_required_env_contract_contains_interlock_key(self, root):
        assert "RADON_REQUIRE_OPERATOR_ALLOWLIST" in self._contract_keys(root)

    def test_env_example_pins_interlock_on(self, root):
        env_vars = parse_env_vars(read_env_example(root))
        assert env_vars.get("RADON_REQUIRE_OPERATOR_ALLOWLIST") == "1"


class TestDemoMigrationEnvContract:
    """R-300 (REL-102b): the demo migration cannot start without its keys.

    `radon-demo-mirror.service` gained an `ExecStartPre` that runs
    `scripts/db/migrate.py --demo`, and `resolve_target()` `sys.exit(2)`s when
    `TURSO_DEMO_DB_URL` or `TURSO_DEMO_AUTH_TOKEN` is unset. Neither key was in
    the deploy contract, so the preflight shipped a unit that fails on every
    fire — and the demo schema silently stops migrating.
    """

    KEYS = ("TURSO_DEMO_DB_URL", "TURSO_DEMO_AUTH_TOKEN")

    def _contract_keys(self, root):
        contract = (root / "config" / "required-env.txt").read_text()
        return [
            line.strip()
            for line in contract.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

    def test_the_preflight_gates_on_both_demo_keys(self, root):
        keys = self._contract_keys(root)
        for key in self.KEYS:
            assert key in keys, f"{key} is not gated by the deploy preflight"

    def test_env_example_documents_both_demo_keys(self, root):
        env_vars = parse_env_vars(read_env_example(root))
        for key in self.KEYS:
            assert key in env_vars, f"{key} is undocumented in .env.example"

    def test_the_unit_and_the_contract_name_the_same_keys(self, root):
        """The ExecStartPre's own requirement is what the contract must gate."""
        unit = (root / "services" / "radon-demo-mirror.service").read_text()
        assert "migrate.py --demo" in unit, (
            "the demo-mirror unit no longer runs the migration; re-scope this test"
        )
        migrate = (root.parent / "scripts" / "db" / "migrate.py").read_text()
        demo_branch = migrate[migrate.index("def resolve_target") :]
        for key in self.KEYS:
            assert key in demo_branch, f"{key} is not what migrate.py --demo reads"
            assert key in self._contract_keys(root)

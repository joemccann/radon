"""Tests for scripts/ci/check_demo_isolation.py — the CI deploy guard."""

import importlib

guard = importlib.import_module("ci.check_demo_isolation")


def _good_env():
    return {
        "TURSO_DEMO_DB_URL": "libsql://radon-demo-abc.aws-us-west-2.turso.io",
        # REL-065 / R-156: every account route reads through TURSO_DB_URL
        # (dbExecute -> getDb()), not getDemoDb(), so on the demo VM it must
        # be set and must be the DEMO database. Leaving it out of the "good"
        # env is what let the guard pass a host pointing at production.
        "TURSO_DB_URL": "libsql://radon-demo-abc.aws-us-west-2.turso.io",
        "RADON_API_TEST_MODE": "1",
        # IB_GATEWAY_HOST intentionally unset → no real IB reachable.
    }


def test_good_demo_env_passes():
    assert guard.check_demo_isolation(_good_env()) == []
    assert guard.main(_good_env()) == 0


def test_prod_turso_url_fails():
    env = _good_env()
    env["TURSO_DEMO_DB_URL"] = "libsql://radon-joemccann.aws-us-west-2.turso.io"
    violations = guard.check_demo_isolation(env)
    assert any("prod marker" in v for v in violations)
    assert guard.main(env) == 1


def test_missing_demo_url_fails():
    env = _good_env()
    del env["TURSO_DEMO_DB_URL"]
    violations = guard.check_demo_isolation(env)
    assert any("TURSO_DEMO_DB_URL is not set" in v for v in violations)


def test_prod_url_in_the_var_every_account_route_reads_fails():
    """REL-065 / R-156 replaced the old `TURSO_DB_URL == TURSO_DEMO_DB_URL`
    rule. On the demo VM those two being equal is the DESIRED state — that
    is what points `getDb()` at demo data. The real hazard the rule was
    supposed to cover is TURSO_DB_URL naming production, which it never
    detected when a distinct TURSO_DEMO_DB_URL was also present."""
    env = _good_env()
    env["TURSO_DB_URL"] = "libsql://radon-joemccann.aws-us-west-2.turso.io"
    violations = guard.check_demo_isolation(env)
    assert any("TURSO_DB_URL" in v and "prod marker" in v for v in violations)
    assert guard.main(env) == 1


def test_both_vars_pointing_at_prod_fails_twice():
    same = "libsql://radon-joemccann.aws-us-west-2.turso.io"
    env = _good_env()
    env["TURSO_DEMO_DB_URL"] = same
    env["TURSO_DB_URL"] = same
    assert len(guard.check_demo_isolation(env)) >= 2


def test_missing_test_mode_fails():
    env = _good_env()
    del env["RADON_API_TEST_MODE"]
    violations = guard.check_demo_isolation(env)
    assert any("RADON_API_TEST_MODE" in v for v in violations)


def test_real_ib_host_fails():
    env = _good_env()
    env["IB_GATEWAY_HOST"] = "ib-gateway"
    violations = guard.check_demo_isolation(env)
    assert any("IB_GATEWAY_HOST" in v for v in violations)
    assert guard.main(env) == 1


def test_loopback_ib_host_passes():
    for host in ("127.0.0.1", "localhost", "::1", ""):
        env = _good_env()
        env["IB_GATEWAY_HOST"] = host
        assert guard.check_demo_isolation(env) == []

"""REL-065 / R-156 (P1) — the demo isolation guard is unreachable and blind.

Two independent failures in the ONLY layer keeping demo-tier users off the
operator's account figures. On demo.radon.run `ALLOWED_USER_IDS` is absent by
design and the account routes carry no `operatorOnly`, so DB isolation is the
single thing between a trial user and the real positions, TWR curve, NAV and
blotter.

(a) A repo-wide grep for `check_demo_isolation` returns the module, its own
unit test and two doc references — no workflow, deploy script or preflight
invokes it. `docs/demo-environment.md` states a CI isolation guard "rejects
any demo deploy whose env carries a prod TURSO_DB_URL"; it did not exist.

(b) Even when invoked it never asserted that the PROD var `TURSO_DB_URL` is
not the production database. The equality check fires only when
`prod_url == demo_url`, so a demo env carrying the REAL `TURSO_DB_URL`
alongside a distinct `TURSO_DEMO_DB_URL` returned zero violations — and every
account route goes through `dbExecute` -> `getDb()` -> `TURSO_DB_URL`, not
`getDemoDb()`.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from ci.check_demo_isolation import PROD_DB_MARKER, check_demo_isolation

CLEAN = {
    "TURSO_DEMO_DB_URL": "libsql://radon-demo.aws-us-west-2.turso.io",
    "TURSO_DB_URL": "libsql://radon-demo.aws-us-west-2.turso.io",
    "RADON_API_TEST_MODE": "1",
    "IB_GATEWAY_HOST": "127.0.0.1",
}


class TestProdUrlIsRejected:
    def test_a_prod_turso_db_url_is_a_violation_even_beside_a_demo_url(self):
        """The exact shape the guard missed: distinct URLs, but the one every
        account route actually reads is production."""
        env = {
            **CLEAN,
            "TURSO_DB_URL": f"libsql://{PROD_DB_MARKER}.aws-us-west-2.turso.io",
        }
        violations = check_demo_isolation(env)
        assert violations, "a demo env pointing TURSO_DB_URL at prod passed"
        assert any("TURSO_DB_URL" in v and PROD_DB_MARKER in v for v in violations), violations

    def test_the_equality_check_still_fires(self):
        env = {**CLEAN, "TURSO_DB_URL": CLEAN["TURSO_DEMO_DB_URL"]}
        assert check_demo_isolation(env) == []

        same_prod = {
            "TURSO_DEMO_DB_URL": f"libsql://{PROD_DB_MARKER}.turso.io",
            "TURSO_DB_URL": f"libsql://{PROD_DB_MARKER}.turso.io",
            "RADON_API_TEST_MODE": "1",
            "IB_GATEWAY_HOST": "127.0.0.1",
        }
        assert len(check_demo_isolation(same_prod)) >= 2

    def test_an_unset_prod_url_is_a_violation(self):
        """Unset means `getDb()` resolves to whatever the host inherits — on
        the demo VM that must be an explicit demo URL, not a default."""
        env = {k: v for k, v in CLEAN.items() if k != "TURSO_DB_URL"}
        assert check_demo_isolation(env)

    def test_a_clean_demo_env_still_passes(self):
        assert check_demo_isolation(CLEAN) == []


class TestTheGuardIsActuallyInvoked:
    def test_ci_runs_the_isolation_check(self):
        workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text()
        assert "check_demo_isolation" in workflow, (
            "nothing invokes the guard: docs/demo-environment.md claims a CI "
            "check that does not run"
        )

    def test_the_docs_claim_matches_what_runs(self):
        doc = (REPO / "docs" / "demo-environment.md").read_text()
        assert "check_demo_isolation" in doc
        assert "ci.yml" in doc, (
            "the doc must name where the guard runs so the next reader can "
            "verify the claim instead of trusting it"
        )

"""Thin-index docs contract.

Durable facts have one owner file. This test fails when the index drifts or
when a mapped path changes without its owner doc (unless the commit message
contains ``docs-skip: <reason>``).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from fnmatch import fnmatch
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
_OWNERS = _ROOT / "docs" / "owners.json"
_ZERO = "0" * 40
_EXEMPT_PREFIXES = (
    "docs/owners.json",
    "scripts/tests/test_docs_contract.py",
    ".github/workflows/ci.yml",
    "CONTRIBUTING.md",
)


def _load_owners() -> dict:
    assert _OWNERS.is_file(), (
        "docs/owners.json is missing. It is the path-glob to owner-doc map "
        "for the thin-index contract."
    )
    return json.loads(_OWNERS.read_text(encoding="utf-8"))


def _section(text: str, heading: str) -> str:
    marker = f"## {heading}"
    start = text.find(marker)
    assert start != -1, f"README is missing ## {heading}"
    rest = text[start + len(marker) :]
    nxt = rest.find("\n## ")
    return rest if nxt == -1 else rest[:nxt]


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=_ROOT,
        text=True,
        stderr=subprocess.DEVNULL,
    )


def _changed_paths() -> list[str]:
    base = (os.environ.get("DOCS_CONTRACT_BASE") or "").strip()
    explicit_base = base not in {"", _ZERO}
    if not explicit_base:
        try:
            _git("rev-parse", "--verify", "origin/main")
            base = "origin/main"
        except subprocess.CalledProcessError:
            base = "HEAD~1"
    else:
        # REL-201 (R-560): an explicit base that does not resolve made every
        # diff below `continue` — zero changed paths, silent pass. Mirror the
        # gitleaks ensure_commit: fail LOUDLY naming the base.
        try:
            _git("rev-parse", "--verify", f"{base}^{{commit}}")
        except subprocess.CalledProcessError as exc:
            raise AssertionError(
                f"DOCS_CONTRACT_BASE {base} is not a resolvable commit in this "
                "clone — the ownership gate would silently pass (R-560); fetch "
                "the base or fix the workflow's fetch depth"
            ) from exc
    names: set[str] = set()
    for args in (
        ["diff", "--name-only", f"{base}...HEAD"],
        ["diff", "--name-only"],
        ["diff", "--cached", "--name-only"],
    ):
        try:
            out = _git(*args)
        except subprocess.CalledProcessError as exc:
            if explicit_base and args[:2] == ["diff", "--name-only"] and len(args) == 3:
                raise AssertionError(
                    f"git diff against DOCS_CONTRACT_BASE {base} failed — the "
                    "ownership gate would silently pass (R-560)"
                ) from exc
            continue
        names.update(line.strip() for line in out.splitlines() if line.strip())
    return sorted(names)


def _commit_messages() -> str:
    base = (os.environ.get("DOCS_CONTRACT_BASE") or "").strip()
    if base in {"", _ZERO}:
        try:
            _git("rev-parse", "--verify", "origin/main")
            base = "origin/main"
        except subprocess.CalledProcessError:
            base = "HEAD~1"
    try:
        return _git("log", "--format=%B", f"{base}..HEAD")
    except subprocess.CalledProcessError:
        return ""


def _matches(path: str, glob: str) -> bool:
    return fnmatch(path, glob) or fnmatch(path.split("/")[-1], glob)


def _violations(changed: list[str], rules: list[dict]) -> list[str]:
    relevant = [p for p in changed if p not in _EXEMPT_PREFIXES]
    hits: list[str] = []
    for rule in rules:
        touched = [
            p for p in relevant
            if p not in rule["owners"]
            and any(_matches(p, g) for g in rule["globs"])
        ]
        if not touched:
            continue
        owners = list(rule["owners"])
        if any(owner in changed for owner in owners):
            continue
        hits.append(
            f"{rule['id']}: changed {touched} but none of {owners} "
            f"(or add 'docs-skip: <reason>' to the commit message)"
        )
    return hits


class TestOwnersMap:
    def test_owners_file_exists_and_is_well_formed(self):
        data = _load_owners()
        assert data.get("rules"), "docs/owners.json must have a non-empty rules list"
        for rule in data["rules"]:
            assert rule.get("id"), rule
            assert rule.get("globs"), rule
            assert rule.get("owners"), rule
            for owner in rule["owners"]:
                assert (_ROOT / owner).is_file(), f"owner missing: {owner}"


_YAHOO_LAST_RESORT_FILES = (
    "CLAUDE.md",
    "AGENTS.md",
    ".pi/AGENTS.md",
    "scripts/CLAUDE.md",
    "scripts/AGENTS.md",
)


class TestYahooLastResortRule:
    """CREDIT shipped Yahoo as the scheduled source. The hard rule must live
    in the agent instruction files, not only in strategy docs."""

    def test_instruction_files_call_yahoo_absolute_last_resort(self):
        for rel in _YAHOO_LAST_RESORT_FILES:
            text = (_ROOT / rel).read_text(encoding="utf-8")
            assert "ABSOLUTE LAST RESORT" in text, rel
            assert "Never make Yahoo the scheduled" in text, rel


class TestRobinhoodRankRule:
    """Robinhood is a READ-ONLY failover: it must rank ABOVE Yahoo and BELOW
    IB / UW / Cboe everywhere the priority list is stated, and execution must
    stay on IB."""

    def test_instruction_files_state_the_full_order(self):
        for rel in _YAHOO_LAST_RESORT_FILES:
            text = (_ROOT / rel).read_text(encoding="utf-8")
            assert "Robinhood" in text, rel
            assert "IB > UW > Cboe > Robinhood > Yahoo" in text, rel

    def test_strategies_table_slots_rh_between_cboe_and_yahoo(self):
        text = (_ROOT / "docs" / "strategies.md").read_text(encoding="utf-8")
        cboe = text.index("| **5th** | Cboe official index feeds")
        rh = text.index("| **6th** | Robinhood")
        yahoo = text.index("| **7th ⚠️** | Yahoo Finance")
        assert cboe < rh < yahoo, "priority table must read Cboe -> Robinhood -> Yahoo"

    def test_read_only_and_ib_execution_are_stated(self):
        for rel in ("CLAUDE.md", "docs/external-services.md", "docs/strategies.md"):
            text = (_ROOT / rel).read_text(encoding="utf-8").lower()
            assert "execution stays on ib" in text, rel

    def test_env_vars_are_documented_with_the_other_vendors(self):
        env_example = (_ROOT / ".env.example").read_text(encoding="utf-8")
        services = (_ROOT / "docs" / "external-services.md").read_text(encoding="utf-8")
        for name in (
            "ROBINHOOD_MCP_TOKEN",
            "ROBINHOOD_MCP_TOKEN_FILE",
            "ROBINHOOD_MCP_REFRESH_TOKEN",
            "ROBINHOOD_MCP_CLIENT_ID",
            "ROBINHOOD_MCP_URL",
        ):
            assert name in env_example, name
        for name in (
            "ROBINHOOD_MCP_TOKEN_FILE",
            "ROBINHOOD_MCP_REFRESH_TOKEN",
            "ROBINHOOD_MCP_CLIENT_ID",
        ):
            assert name in services, name
        assert "https://agent.robinhood.com/mcp/trading" in services

    def test_numbered_priority_lists_put_cboe_before_rh_before_yahoo(self):
        # The class of mismatch where RH is numbered directly after UW and
        # Cboe exists only in prose: the numbered list itself must read
        # Cboe -> Robinhood -> Yahoo.
        for rel in ("CLAUDE.md", "AGENTS.md", ".pi/AGENTS.md"):
            text = (_ROOT / rel).read_text(encoding="utf-8")
            start = text.index("## Data Source Priority")
            end = text.find("\n## ", start + 1)
            section = text[start:end] if end != -1 else text[start:]
            cboe = section.index("Cboe official index feeds")
            rh = section.index("Robinhood")
            yahoo = section.index("Yahoo Finance — **ABSOLUTE LAST RESORT**")
            assert cboe < rh < yahoo, (
                f"{rel}: the numbered priority list must read "
                "Cboe -> Robinhood -> Yahoo"
            )

    def test_vps_secret_paths_are_pinned(self):
        # The rotating token store is a writable secret OUTSIDE the read-only
        # env file: both operator docs must name it.
        for rel in ("docs/external-services.md", "docs/operations.md"):
            text = (_ROOT / rel).read_text(encoding="utf-8")
            assert "/etc/radon/rh-mcp.json" in text, rel
        operations = (_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
        for name in (
            "ROBINHOOD_MCP_TOKEN",
            "ROBINHOOD_MCP_REFRESH_TOKEN",
            "ROBINHOOD_MCP_CLIENT_ID",
            "ROBINHOOD_MCP_TOKEN_FILE",
        ):
            assert name in operations, name

    def test_official_links_and_non_dependencies_are_pinned(self):
        services = (_ROOT / "docs" / "external-services.md").read_text(encoding="utf-8")
        for link in (
            "https://agent.robinhood.com/mcp/trading",
            "https://agent.robinhood.com/.well-known/oauth-authorization-server/mcp/trading",
            "https://api.robinhood.com/oauth2/token/",
            "https://robinhood.com/us/en/support/articles/agentic-trading-overview/",
            "https://robinhood.com/us/en/support/articles/trading-with-your-agent/",
        ):
            assert link in services, link
        # Explicit non-dependencies: unofficial wrappers, Banking MCP, crypto
        # REST, and the crowding series' gate isolation.
        for marker in (
            "robin-stocks",
            "banking-agent.robinhood.com",
            "trading.robinhood.com",
            "cannot trip the three gates",
        ):
            assert marker in services, marker

    def test_token_expiry_and_refresh_are_documented(self):
        # A static access token goes stale in ~3 days; both env docs must say
        # refresh is mandatory and point at the official token endpoint.
        env_example = (_ROOT / ".env.example").read_text(encoding="utf-8")
        services = (_ROOT / "docs" / "external-services.md").read_text(encoding="utf-8")
        for text, rel in ((env_example, ".env.example"), (services, "docs/external-services.md")):
            assert "~3 days" in text, rel
            assert "refresh is mandatory" in text.lower(), rel
            assert "https://api.robinhood.com/oauth2/token/" in text, rel


class TestThinIndex:
    def test_readme_has_now_true_not_recent_additions(self):
        text = (_ROOT / "README.md").read_text(encoding="utf-8")
        assert "## Recent additions" not in text
        body = _section(text, "Now true")
        bullets = [ln for ln in body.splitlines() if ln.startswith("- ")]
        assert 1 <= len(bullets) <= 5, bullets
        assert "docs/indicators/README.md" in text
        assert "docs/incident-runbook.md" in text
        assert "docs/equibles-api.md" in text

    def test_indicators_index_lists_every_spec(self):
        specs = sorted(
            p.name
            for p in (_ROOT / "docs" / "indicators").glob("*.md")
            if p.name != "README.md"
        )
        index = (_ROOT / "docs" / "indicators" / "README.md").read_text(encoding="utf-8")
        missing = [name for name in specs if f"]({name})" not in index]
        assert not missing, f"docs/indicators/README.md missing rows for {missing}"

    def test_scripts_reference_does_not_teach_npm_test(self):
        text = (_ROOT / "docs" / "scripts-reference.md").read_text(encoding="utf-8")
        assert "npm test" not in text

    def test_readme_defers_catalog_and_index_to_docs(self):
        text = (_ROOT / "README.md").read_text(encoding="utf-8")
        assert "## External services" not in text
        assert "## What's where" not in text
        assert "## Glossary" not in text
        assert "docs/README.md" in text
        assert "docs/external-services.md" in text
        assert "SECURITY.md" in text
        assert "SUPPORT.md" in text

    def test_owner_docs_list_credit_spread_timer(self):
        operations = (_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
        cloud = (_ROOT / "docs" / "cloud-services.md").read_text(encoding="utf-8")
        assert "radon-credit-spread.timer" in operations
        assert "radon-credit-spread.timer" in cloud

    def test_docs_index_exists_and_lists_owners(self):
        index = (_ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        for required in (
            "CLAUDE.md",
            "docs/operations.md",
            "docs/cloud-services.md",
            "docs/indicators/README.md",
            "docs/external-services.md",
            "docs/archive/",
        ):
            assert required in index, required

    def test_security_and_support_files_exist(self):
        assert (_ROOT / "SECURITY.md").is_file()
        assert (_ROOT / "SUPPORT.md").is_file()
        assert (_ROOT / ".github" / "CODEOWNERS").is_file()

    def test_web_readme_does_not_teach_npm(self):
        text = (_ROOT / "web" / "README.md").read_text(encoding="utf-8")
        assert "npm install" not in text
        assert "npm test" not in text
        assert "npm run" not in text


class TestEdgeHealthRunbook:
    def test_edge_health_status_caveat_states_the_200_body_contract(self):
        # R-444: after cd6af110 / b8eda2b6 every failure mode of
        # /edge-health/status is HTTP 200 -- an upstream 5xx and a
        # Caddy-synthesized dial-refused are both rewritten to
        # {"reachable":false,"observer":"caddy"}. The runbook still told the
        # operator the path "returns 502 when the daemon is down", which is
        # the discriminating check a status-code-only monitor would rely on.
        for rel in ("docs/operations.md", "scripts/health_service/CLAUDE.md"):
            text = (_ROOT / rel).read_text(encoding="utf-8")
            assert "returns `502` when the daemon" not in text, rel
            assert "502s when the daemon is down" not in text, rel
            assert '{"reachable":false,"observer":"caddy"}' in text, rel
        operations = (_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
        assert "no `ok` field" in operations
        assert "never on the status code" in operations


class TestDbBackupRunbook:
    def test_local_prune_is_documented_as_upload_aware(self):
        # R-445: the local window is 7 days and a dump is unlinked only once
        # B2 holds it; the runbook still reasoned about a 30-day window.
        text = (_ROOT / "docs" / "cloud-services.md").read_text(encoding="utf-8")
        assert "Off-boxing a 30-day window would buy nothing" not in text
        assert "present in B2" in text


class TestOwnership:
    def test_matcher_requires_an_owner_when_a_glob_hits(self):
        rules = [
            {
                "id": "equibles",
                "globs": ["cloud/services/radon-equibles-*"],
                "owners": ["docs/operations.md", "docs/equibles-api.md"],
            }
        ]
        assert _violations(
            ["cloud/services/radon-equibles-13f.timer"], rules
        )
        assert not _violations(
            [
                "cloud/services/radon-equibles-13f.timer",
                "docs/operations.md",
            ],
            rules,
        )
        assert not _violations(["docs/indicators/README.md"], [
            {
                "id": "indicators",
                "globs": ["docs/indicators/*.md"],
                "owners": ["docs/indicators/README.md"],
            }
        ])

    def test_changed_mapped_paths_update_an_owner_doc(self):
        data = _load_owners()
        changed = _changed_paths()
        if re.search(r"docs-skip:\s+\S+", _commit_messages()):
            return
        hits = _violations(changed, data["rules"])
        assert not hits, "docs contract:\n  " + "\n  ".join(hits)


# ── T-163: preflight claims must match the preflight contract ─────
#
# `1b326772` removed EQUIBLES_API_KEY from cloud/config/required-env.txt and
# deleted its only assertion, leaving two docs asserting a fail-closed guard
# that no longer exists. Nothing noticed. This closes that loop in both
# directions: a doc may not claim a key IS in the contract when it is absent,
# and may not claim one is NOT there when it is present.

_REQUIRED_ENV = _ROOT / "cloud" / "config" / "required-env.txt"
_PREFLIGHT_DOCS = ("docs/cloud-services.md", "docs/operations.md")
_CONTRACT_PATH = "required-env.txt"
_CODE_SPAN = re.compile(r"`[^`]*`")
_ENV_NAME = re.compile(r"`([A-Z][A-Z0-9_]{2,})`")
_NEGATION = re.compile(r"\b(not|NOT|never|no longer|absent|missing|held out)\b")


def _required_env_names() -> set[str]:
    text = _REQUIRED_ENV.read_text(encoding="utf-8")
    return {
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _sentences(paragraph: str) -> list[str]:
    """Split on sentence ends, holding dotted code spans together.

    `cloud/config/required-env.txt` and `check-env.py` are full of periods, so
    the dots inside backticks are masked before the split and restored after.
    """
    masked = _CODE_SPAN.sub(lambda m: m.group(0).replace(".", "\x00"), paragraph)
    parts = re.split(r"(?<=[.!?])\s+", masked)
    return [part.replace("\x00", ".") for part in parts if part.strip()]


def _claim_units(text: str) -> list[str]:
    """Markdown split into claim-sized units: one sentence or table row each.

    Wrapped prose is rejoined before splitting so a claim broken across two
    source lines reads as one sentence. Table rows stay one unit per row so an
    unrelated row in the same table cannot lend its env names to a neighbour.
    """
    units: list[str] = []
    for block in text.split("\n\n"):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if all(line.lstrip().startswith("|") for line in lines):
            units.extend(lines)
        else:
            units.extend(_sentences(" ".join(lines)))
    return units


class TestPreflightContractClaims:
    """Docs claiming check-env.py fails closed on a key must match reality.

    Polarity is read per claim unit: a unit naming the contract file and
    carrying a negation ("NOT in", "no longer", "held out") asserts absence,
    otherwise it asserts presence. Coarse by design — it cannot parse an
    argument, only whether the names a unit ties to the contract file are
    actually in it.
    """

    def test_the_contract_file_is_parseable(self):
        names = _required_env_names()
        assert "TURSO_DB_URL" in names, f"required-env.txt parse looks wrong: {sorted(names)[:5]}"

    def test_no_doc_misstates_the_required_env_contract(self):
        required = _required_env_names()
        wrong: list[str] = []
        for rel in _PREFLIGHT_DOCS:
            path = _ROOT / rel
            assert path.is_file(), f"{rel} is missing"
            for unit in _claim_units(path.read_text(encoding="utf-8")):
                if _CONTRACT_PATH not in unit:
                    continue
                negated = bool(_NEGATION.search(unit))
                for name in _ENV_NAME.findall(unit):
                    present = name in required
                    if negated and present:
                        wrong.append(f"{rel}: claims {name} is NOT in the contract, but it is")
                    elif not negated and not present:
                        wrong.append(f"{rel}: claims {name} IS in the contract, but it is absent")

        assert not wrong, (
            "Doc claims about cloud/config/required-env.txt do not match the "
            "file:\n  " + "\n  ".join(sorted(set(wrong))) +
            "\nFix the doc, or add the key to the contract deliberately."
        )


# ── DOC-020/022: the private-net trust scope must read the same in code and docs ─
#
# REL-170 narrowed 10.0.0.0/16 from the global server-to-server bypass to the
# broker watchdog's /health probe. Both owner docs kept describing the old
# global trust, so a reviewer or broker-side integrator worked from a wrong
# trust map. The docs must name the scoping function; the code must keep the
# private net out of the bypass helper.

_AUTH_SRC = _ROOT / "scripts" / "api" / "auth.py"
_TRUST_DOCS = ("scripts/api/CLAUDE.md", "docs/spof-host-split.md")


class TestPrivateNetTrustScope:
    def test_auth_keeps_the_private_net_out_of_the_global_bypass(self):
        src = _AUTH_SRC.read_text(encoding="utf-8")
        assert "def is_private_net_probe" in src
        start = src.index("def is_local_or_tailnet")
        end = src.index("def is_private_net_peer")
        assert "_HETZNER_PRIVATE" not in src[start:end], (
            "is_local_or_tailnet consults the Hetzner private net: the docs in "
            f"{_TRUST_DOCS} describe it as probe-only and must change with this"
        )

    def test_owner_docs_describe_the_private_net_as_probe_only(self):
        for rel in _TRUST_DOCS:
            text = (_ROOT / rel).read_text(encoding="utf-8")
            assert "is_private_net_probe" in text, rel
            assert "trusts exactly `10.0.0.0/16`" not in text, rel
            assert "tailnet/`10.0.0.0/16`" not in text, rel


# ── DOC-021: TEST_LOG.md is an append-only ledger ─────────────────
#
# 4584e84a (#213) replaced the 543-line ledger with its one new row: header
# and 176 prior T-rows vanished from HEAD while testing-weekend/SKILL.md kept
# declaring the file append-only and reading it at pre-flight. Rows may only
# be added relative to the base the change is reviewed against.

_TEST_LOG = "TEST_LOG.md"
_LEDGER_ROW = re.compile(r"^\| T-\d{3} \|", re.MULTILINE)


def _ledger_base_ref() -> str | None:
    base = (os.environ.get("DOCS_CONTRACT_BASE") or "").strip()
    if base in {"", _ZERO}:
        base = "origin/main"
    try:
        _git("rev-parse", "--verify", base)
    except subprocess.CalledProcessError:
        return None
    return base


class TestTestLogLedgerIsAppendOnly:
    def test_header_is_present(self):
        text = (_ROOT / _TEST_LOG).read_text(encoding="utf-8")
        assert text.startswith("# TEST_LOG.md — testing remediation execution log"), (
            "TEST_LOG.md lost its header: the ledger was overwritten, not appended"
        )

    def test_row_count_never_decreases_against_the_base(self):
        base = _ledger_base_ref()
        if base is None:
            pytest.skip("no base ref to compare the ledger against")
        try:
            before = _git("show", f"{base}:{_TEST_LOG}")
        except subprocess.CalledProcessError:
            pytest.skip(f"{_TEST_LOG} absent at {base}")
        now = (_ROOT / _TEST_LOG).read_text(encoding="utf-8")
        was, is_now = len(_LEDGER_ROW.findall(before)), len(_LEDGER_ROW.findall(now))
        assert is_now >= was, (
            f"TEST_LOG.md has {is_now} T-rows but {base} has {was}: the ledger "
            "is append-only (testing-weekend/SKILL.md rail 4); restore the rows"
        )


# DOC-032 / DOC-033 (2026-09-01): docs/operations.md is the one place that
# indexes all five nightly loops. Its "all fire 00:00 local" sentence had been
# wrong since the loops were staggered, and its rails named only the shared
# runner marker while every wrapper also requires a per-loop one. Both facts
# are mechanically derivable, so pin them instead of re-reading the prose.

_LOOPS = {
    "reliability": ("com.radon.reliability-daily.plist", "reliability_weekend.sh"),
    "testing": ("com.radon.testing-daily.plist", "testing_weekend.sh"),
    "ci-performance": ("com.radon.ci-performance-daily.plist", "ci_performance_nightly.sh"),
    "documentation": ("com.radon.documentation-daily.plist", "documentation_nightly.sh"),
    "security": ("com.radon.security-daily.plist", "security_nightly.sh"),
}


_LOOP_SKILLS = {
    "reliability": "reliability-weekend",
    "testing": "testing-weekend",
    "ci-performance": "ci-performance",
    "documentation": "documentation-nightly",
    "security": "security-nightly",
}


def _operations_text() -> str:
    return (_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")


class TestNightlyLoopIndex:
    def test_each_loop_row_states_the_plist_fire_time(self):
        import plistlib

        text = _operations_text()
        for loop, (plist_name, _) in _LOOPS.items():
            plist = _ROOT / "config" / plist_name
            assert plist.is_file(), f"{plist} is missing"
            with plist.open("rb") as fh:
                when = plistlib.load(fh)["StartCalendarInterval"]
            fires = f"{when['Hour']:02d}:{when['Minute']:02d}"
            row = next(
                (ln for ln in text.splitlines() if ln.startswith(f"| {loop} |")),
                None,
            )
            assert row is not None, (
                f"docs/operations.md has no nightly-loop row for {loop}"
            )
            assert f"| {fires} |" in row, (
                f"docs/operations.md says {row.strip()} but "
                f"{plist_name} fires at {fires}"
            )

    def test_the_per_loop_runner_marker_rail_is_stated(self):
        assert ".radon-<loop>-runner" in _operations_text(), (
            "docs/operations.md must state that a wrapper needs BOTH "
            ".radon-weekend-runner and its own .radon-<loop>-runner marker; "
            "every wrapper refuses the clone without the second one"
        )

    def test_each_wrapper_actually_requires_its_own_marker(self):
        for loop, (_, wrapper) in _LOOPS.items():
            text = (_ROOT / "scripts" / wrapper).read_text(encoding="utf-8")
            assert f".radon-{loop}-runner" in text, (
                f"scripts/{wrapper} no longer names .radon-{loop}-runner; "
                "docs/operations.md documents that marker as the rail"
            )

    # DOC-084 (2026-09-04): three SKILL.md rails named only
    # `.radon-weekend-runner`, so an agent reading its own rail believed the
    # shared marker was the whole gate while its wrapper also required the
    # per-loop one.
    @pytest.mark.parametrize("loop,skill", sorted(_LOOP_SKILLS.items()))
    def test_each_skill_rail_names_its_own_marker(self, loop, skill):
        text = (_ROOT / ".claude" / "skills" / skill / "SKILL.md").read_text(
            encoding="utf-8"
        )
        assert f".radon-{loop}-runner" in text, (
            f".claude/skills/{skill}/SKILL.md states the runner-clone rail "
            f"without .radon-{loop}-runner, but scripts/"
            f"{_LOOPS[loop][1]} refuses the clone without it"
        )


# DOC-045 (2026-09-01): TEST_LOG.md is not the only append-only root ledger,
# and it was only guarded after a truncation shipped green. `path_filter.py`
# classifies every root `.md` as documentation and routes it to a contract
# test ONLY when a test names the file, so the other five ledgers selected no
# gate at all. Naming them here is what puts them behind one.

_LEDGERS = {
    "RELIABILITY_AUDIT.md": r"^\| R-\d+",
    "RELIABILITY_LOG.md": r"^\| REL-\d+",
    "TEST_AUDIT.md": r"^\| T-\d+",
    "REMEDIATION_LOG.md": r"^\| T-\d+",
    "CI_PERFORMANCE_LOG.md": r"^### CIP-\d+",
}


class TestRootLedgersAreAppendOnly:
    @pytest.mark.parametrize("ledger,row", sorted(_LEDGERS.items()))
    def test_entry_count_never_decreases_against_the_base(self, ledger, row):
        base = _ledger_base_ref()
        if base is None:
            pytest.skip("no base ref to compare the ledger against")
        try:
            before = _git("show", f"{base}:{ledger}")
        except subprocess.CalledProcessError:
            pytest.skip(f"{ledger} absent at {base}")
        pattern = re.compile(row, re.MULTILINE)
        now = (_ROOT / ledger).read_text(encoding="utf-8")
        was, is_now = len(pattern.findall(before)), len(pattern.findall(now))
        assert is_now >= was, (
            f"{ledger} has {is_now} entries but {base} has {was}: the nightly "
            "ledgers are append-only — restore the rows instead of rewriting "
            "history (see TEST_LOG.md, truncated 543 -> 2 lines in 4584e84a "
            "with every gate green)"
        )


# --- the workflow has to hand this contract a history it can diff -------------
#
# DOC-038. `_changed_paths` diffs `$DOCS_CONTRACT_BASE...HEAD`. In a depth-1
# clone that base commit is not in the object graph, `git diff` exits non-zero,
# and the loop `continue`s — so the contract sees ZERO changed paths and passes
# for every commit. It fails OPEN, silently.
#
# ci.yml asked for the full history with
#     fetch-depth: ${{ matrix.shard == 'scripts-df' && 0 || 1 }}
# but GitHub casts the NUMBER 0 to false, so the true branch falls through to
# the `||` and every shard, `scripts-df` included, got depth 1. The ownership
# gate has been vacuous since 424e66da; two commits in that range violated it
# and went green.

_WORKFLOW = _ROOT / ".github" / "workflows" / "ci.yml"
# The shard whose checkout must carry history: it is the one that runs this file.
_HISTORY_SHARD = "scripts-df"
_TERNARY = re.compile(
    r"\$\{\{\s*matrix\.shard\s*==\s*'(?P<shard>[^']+)'\s*"
    r"&&\s*(?P<yes>\S+)\s*\|\|\s*(?P<no>\S+?)\s*\}\}"
)


def _gha_truthy(token: str) -> bool:
    """GitHub's documented cast to Boolean.

    ``null``, ``false``, the NUMBER ``0`` and the EMPTY string are false;
    everything else is true — including the non-empty string ``'0'``, which is
    why quoting the branches is the fix and not a cosmetic change.
    """
    return token not in {"0", "false", "null", "''", '""'}


def _resolve_ternary(match: re.Match, *, condition: bool) -> str:
    yes, no = match.group("yes"), match.group("no")
    chosen = yes if condition and _gha_truthy(yes) else no
    return chosen.strip("'\"")


class TestTheWorkflowGivesThisContractAHistoryToDiff:
    def _fetch_depth_expression(self) -> re.Match:
        text = _WORKFLOW.read_text(encoding="utf-8")
        line = next(
            (l for l in text.splitlines() if "fetch-depth:" in l and "matrix.shard" in l),
            None,
        )
        assert line is not None, (
            "ci.yml no longer varies fetch-depth by shard; if the python job "
            "now checks out a fixed depth, this contract needs the deep one"
        )
        match = _TERNARY.search(line)
        assert match is not None, f"unrecognised fetch-depth expression: {line.strip()}"
        assert match.group("shard") == _HISTORY_SHARD, (
            f"the deep checkout is keyed on {match.group('shard')!r}, but this "
            f"contract runs in the {_HISTORY_SHARD!r} shard"
        )
        return match

    def test_the_shard_that_runs_this_file_checks_out_full_history(self):
        match = self._fetch_depth_expression()
        depth = _resolve_ternary(match, condition=True)
        assert depth == "0", (
            f"the {_HISTORY_SHARD} shard resolves to fetch-depth {depth!r}, not "
            "'0'. GitHub casts the number 0 to false, so `cond && 0 || 1` "
            "always yields 1: the base commit is absent from the clone, every "
            "`git diff BASE...HEAD` fails, _changed_paths returns nothing, and "
            "this ownership gate passes for every commit. Quote the branches "
            "('0' / '1') so the true branch survives the ||."
        )

    def test_the_other_shards_stay_shallow(self):
        match = self._fetch_depth_expression()
        depth = _resolve_ternary(match, condition=False)
        assert depth == "1", (
            f"the non-{_HISTORY_SHARD} shards resolve to fetch-depth {depth!r}; "
            "a full clone on every python shard is a needless CI cost"
        )


class TestRel201GateFailsClosed:
    """REL-201 (R-560): an unresolvable DOCS_CONTRACT_BASE must FAIL the
    gate loudly, never `continue` into a zero-changed-paths pass."""

    def test_a_nonexistent_base_fails_naming_it(self, monkeypatch):
        monkeypatch.setenv("DOCS_CONTRACT_BASE", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
        with pytest.raises(AssertionError, match="deadbeef"):
            _changed_paths()

    def test_an_empty_base_still_uses_the_fallback(self, monkeypatch):
        monkeypatch.setenv("DOCS_CONTRACT_BASE", "")
        assert isinstance(_changed_paths(), list)

# ── DOC-057: "root install-copy is still owed" must not outlive the install ──
#
# The topology owners kept telling an operator that radon-ivrank and
# radon-credit-spread units were waiting on a manual root install-copy after
# both pairs were pinned in cloud/config/installed-units.sha256 (the deploy's
# install-units verb installs anything listed there). An operator following
# the stale sentence copies units by hand over a root-owned install. A section
# may say the copy is owed only while its units are absent from the manifest.

_INSTALLED_UNITS = _ROOT / "cloud" / "config" / "installed-units.sha256"
_TOPOLOGY_DOCS = ("docs/cloud-services.md", "docs/operations.md")
_OWED = re.compile(r"install-copy[^.|\n]*owed|owed[^.|\n]*install-copy")
_UNIT_NAME = re.compile(r"\bradon-[a-z0-9-]+\.(?:timer|service)\b")


def _installed_unit_names() -> set[str]:
    names: set[str] = set()
    for line in _INSTALLED_UNITS.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and _UNIT_NAME.fullmatch(parts[1]):
            names.add(parts[1])
    return names


class TestInstallCopyOwedClaims:
    def test_the_manifest_is_parseable(self):
        assert "radon-ivrank.timer" in _installed_unit_names()

    def test_no_owner_says_an_installed_unit_still_owes_its_copy(self):
        installed = _installed_unit_names()
        wrong: list[str] = []
        for rel in _TOPOLOGY_DOCS:
            text = (_ROOT / rel).read_text(encoding="utf-8")
            for unit in _claim_units(text):
                if not _OWED.search(unit):
                    continue
                for name in _UNIT_NAME.findall(unit):
                    if name in installed:
                        wrong.append(f"{rel}: says {name} still owes its root install-copy")
        assert not wrong, (
            "Doc claims a root install-copy is still owed for a unit that "
            "cloud/config/installed-units.sha256 already pins:\n  "
            + "\n  ".join(sorted(set(wrong)))
        )

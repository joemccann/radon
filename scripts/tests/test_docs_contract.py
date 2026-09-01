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
    if base in {"", _ZERO}:
        try:
            _git("rev-parse", "--verify", "origin/main")
            base = "origin/main"
        except subprocess.CalledProcessError:
            base = "HEAD~1"
    names: set[str] = set()
    for args in (
        ["diff", "--name-only", f"{base}...HEAD"],
        ["diff", "--name-only"],
        ["diff", "--cached", "--name-only"],
    ):
        try:
            out = _git(*args)
        except subprocess.CalledProcessError:
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

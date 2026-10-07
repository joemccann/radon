"""Thin-index docs contract.

Durable facts have one owner file. This test fails when the index drifts or
when a mapped path changes without its owner doc (unless the commit message
contains ``docs-skip: <reason>``).
"""
from __future__ import annotations

import gzip
import json
import shlex
import shutil
import sqlite3
import sys
import types
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
            assert "IB > Robinhood > UW > Cboe > Yahoo" in text, rel

    def test_strategies_table_slots_rh_between_ib_and_uw(self):
        text = (_ROOT / "docs" / "strategies.md").read_text(encoding="utf-8")
        ib = text.index("| **1st** | Interactive Brokers")
        rh = text.index("| **2nd** | Robinhood")
        uw = text.index("| **3rd** | Unusual Whales")
        yahoo = text.index("| **7th ⚠️** | Yahoo Finance")
        assert ib < rh < uw < yahoo, "priority table must read IB -> Robinhood -> UW -> ... -> Yahoo"

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

    def test_numbered_priority_lists_put_rh_after_ib_and_before_uw(self):
        # Robinhood serves commodity price data right after IB so UW calls go
        # to the endpoints only UW has; Cboe and Yahoo still follow.
        for rel in ("CLAUDE.md", "AGENTS.md", ".pi/AGENTS.md"):
            text = (_ROOT / rel).read_text(encoding="utf-8")
            start = text.index("## Data Source Priority")
            end = text.find("\n## ", start + 1)
            section = text[start:end] if end != -1 else text[start:]
            ib = section.index("1. Interactive Brokers")
            rh = section.index("2. Robinhood")
            uw = section.index("3. Unusual Whales")
            cboe = section.index("4. Cboe official index feeds")
            yahoo = section.index("Yahoo Finance — **ABSOLUTE LAST RESORT**")
            assert ib < rh < uw < cboe < yahoo, (
                f"{rel}: the numbered priority list must read "
                "IB -> Robinhood -> UW -> Cboe -> Yahoo"
            )

    def test_vps_secret_paths_are_pinned(self):
        # The rotating token store is a writable secret OUTSIDE the read-only
        # env file: both operator docs must name it.
        for rel in ("docs/external-services.md", "docs/operations.md"):
            text = (_ROOT / rel).read_text(encoding="utf-8")
            assert "/var/lib/radon/rh-mcp/rh-mcp.json" in text, rel
        operations = (_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
        for name in (
            "ROBINHOOD_MCP_TOKEN",
            "ROBINHOOD_MCP_REFRESH_TOKEN",
            "ROBINHOOD_MCP_CLIENT_ID",
            "ROBINHOOD_MCP_TOKEN_FILE",
        ):
            assert name in operations, name

    # DOC-072 (2026-09-04): four docs still claimed the host env file was mode
    # 0600 owned by radon, one of them contradicting a correct line in the same
    # file. cloud/scripts/setup-vps.sh installs it 0640 root:radon, so an
    # operator "repairing" the permissions from a doc would lock the units out.
    def test_no_doc_claims_a_stale_mode_for_the_host_env_file(self):
        env_names = ("/etc/radon/env", "radon-cloud/.env")
        for path in sorted(_ROOT.glob("docs/**/*.md")) + sorted(
            _ROOT.glob("cloud/**/*.md")
        ):
            rel = path.relative_to(_ROOT).as_posix()
            if rel.startswith("docs/archive/"):
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if not any(name in line for name in env_names):
                    continue
                if "0600" not in line:
                    continue
                # the rotating token store IS 0600 and is often named on the
                # same line as the env file
                if "rh-mcp.json" in line:
                    continue
                raise AssertionError(
                    f"{rel}:{lineno} gives mode 0600 for the host env file; "
                    "cloud/scripts/setup-vps.sh:606-607 installs /etc/radon/env "
                    "0640 root:radon"
                )

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


class TestIphoneAppDirection:
    """Foundation pin: iPhone path is documented, not claimed shipping."""

    _SOURCE = "https://x.com/breejeanadkat/status/2098728437133476089"
    _DOC = _ROOT / "docs" / "mobile" / "iphone-app-direction.md"
    _STUB = _ROOT / "apps" / "ios" / "README.md"

    def test_direction_doc_exists_and_is_indexed(self):
        assert self._DOC.is_file(), "docs/mobile/iphone-app-direction.md is the iPhone foundation"
        index = (_ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        assert "mobile/iphone-app-direction.md" in index
        rules = _load_owners()["rules"]
        assert any(rule.get("id") == "iphone-app" for rule in rules)

    def test_direction_doc_credits_source_and_states_non_goals(self):
        text = self._DOC.read_text(encoding="utf-8")
        assert self._SOURCE in text
        for marker in (
            "references",
            "AI draft",
            "specific critique",
            "Figma",
            "interactive MVP",
            "propose",
            "never auto-trade",
            "not rewriting the web UI",
            "not shipping",
            "App Store",
        ):
            assert marker in text, marker

    def test_ios_readme_is_a_stub_not_a_project(self):
        assert self._STUB.is_file()
        text = self._STUB.read_text(encoding="utf-8")
        assert "docs/mobile/iphone-app-direction.md" in text
        assert "xcodeproj" not in text.lower()
        ios_files = [p.name for p in self._STUB.parent.iterdir() if p.is_file()]
        assert ios_files == ["README.md"], ios_files


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


class TestRecoveryInstructions:
    def test_scratch_restore_is_repeatable_and_rejects_bad_input(self, tmp_path):
        if not shutil.which("sqlite3"):
            pytest.skip("sqlite3 CLI is required for the documented scratch drill")
        text = (_ROOT / "docs/cloud-services.md").read_text()
        section = text.split("### Restore runbook", 1)[1]
        command = re.search(r"```bash\n(.*?)```", section, re.S).group(1)
        backup = tmp_path / "data/db_backups/radon-fixture.sql.gz"
        backup.parent.mkdir(parents=True)
        with gzip.open(backup, "wt") as out:
            out.write("CREATE TABLE journal(id); INSERT INTO journal VALUES(1); "
                      "CREATE TABLE service_health(id); INSERT INTO service_health VALUES(1);")
        command = command.replace("<stamp>", "fixture")
        # Reject the old shared /tmp target before executing any doc shell.
        assert "mktemp -d" in command, "scratch restore must allocate a private fresh directory"
        env = {"PATH": os.defpath, "TMPDIR": str(tmp_path), "HOME": str(tmp_path)}
        for _ in range(2):
            result = subprocess.run(["bash", "-c", command], cwd=tmp_path,
                                    env=env, capture_output=True, text=True)
            assert result.returncode == 0, result.stderr
        databases = sorted(tmp_path.glob("radon-restore.*/restore.db"))
        assert len(databases) == 2
        for database in databases:
            with sqlite3.connect(database) as db:
                assert db.execute("SELECT COUNT(*) FROM journal").fetchone() == (1,)
        backup.write_bytes(b"not a gzip dump")
        result = subprocess.run(["bash", "-c", command], cwd=tmp_path,
                                env=env, capture_output=True, text=True)
        assert result.returncode != 0, "invalid backup must stop the restore drill"
        assert "Scratch restore:" not in result.stdout
        with gzip.open(backup, "wt") as out:
            out.write("THIS IS NOT SQL;")
        result = subprocess.run(["bash", "-c", command], cwd=tmp_path,
                                env=env, capture_output=True, text=True)
        assert result.returncode != 0, "SQL errors must stop the restore drill"
        assert "Scratch restore:" not in result.stdout

    def test_contributing_uses_reviewed_branches(self):
        text = (_ROOT / "CONTRIBUTING.md").read_text()
        assert "All work commits to `main`" not in text
        assert "pull request" in text.lower()
        assert "Never push directly to `main`" in text

    def test_knowledge_documented_test_paths_exist(self):
        text = (_ROOT / "docs/knowledge-embeddings.md").read_text()
        paths = set(re.findall(r"scripts/tests/test_[a-z0-9_]+\.py", text))
        assert paths
        assert all((_ROOT / path).is_file() for path in paths), paths

    def test_knowledge_coverage_command_uses_real_imports(self, monkeypatch, capsys):
        text = (_ROOT / "docs/knowledge-embeddings.md").read_text()
        line = next(line for line in text.splitlines() if "**Verify backfill complete:**" in line)
        command = re.search(r"`([^`]+)`", line).group(1)
        args = shlex.split(command)
        program = args[args.index("-c") + 1]
        # The only substituted module is the external DB client. Execute the
        # documented imports and real coverage query against an offline DB.
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE TABLE knowledge(embedding_v2 BLOB)")
            client = types.ModuleType("scripts.db.client")
            client.get_db = lambda: db
            monkeypatch.setitem(sys.modules, "scripts.db.client", client)
            from scripts.knowledge import embed
            monkeypatch.setattr(embed, "_coverage_cache", {"at": None, "ready": None})
            exec(program, {})
            assert capsys.readouterr().out.strip() == "True"


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
# and 176 prior T-rows vanished from HEAD while the testing loop's manual kept
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
            "is append-only (.claude/runner-prompts/testing.md); restore the rows"
        )


# T-492 (2026-09-13): PR #411 merged onto a tree that already carried the
# 2026-09-13 TEST_LOG section and left unresolved conflict markers on main.
# The append-only row-count tests stayed green because both sides' T-rows
# survived inside the conflict. A marker scan is the gate that would have
# redded that merge.
def _is_git_conflict_marker(line: str) -> bool:
    return (
        line.startswith("<<<<<<< ")
        or line.startswith(">>>>>>> ")
        or line == "======="
    )
_TESTING_LEDGERS = (
    "TEST_LOG.md",
    "TEST_AUDIT.md",
    "REMEDIATION_LOG.md",
)


class TestTestingLedgersHaveNoConflictMarkers:
    @pytest.mark.parametrize("ledger", _TESTING_LEDGERS)
    def test_no_unresolved_conflict_markers(self, ledger):
        path = _ROOT / ledger
        if not path.is_file():
            pytest.skip(f"{ledger} is not in this tree")
        hits = [
            i
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if _is_git_conflict_marker(line)
        ]
        assert hits == [], (
            f"{ledger} has unresolved git conflict markers at lines {hits}; "
            "keep both sides of a TEST_LOG merge, never ship the markers"
        )


# DOC-032 / DOC-033 (2026-09-01): docs/operations.md is the one place that
# indexes every nightly loop. Its fire times had drifted from the schedules, and
# its rails once named only a shared runner marker. Both facts are mechanically
# derivable, so pin them instead of re-reading the prose. Since the runner
# cutover (2026-09-28) the schedule is each loop env's SCHEDULE_HOUR:MINUTE and
# the clone rail is the runner clone ~/radon-runner/work/<loop>.

_LOOP_ENVS = _ROOT / "scripts" / "runner" / "loops"
_SECURITY_PROMPTS = {
    "security": _ROOT / ".claude" / "runner-prompts" / "security.md",
    "security-deepsec": _ROOT / ".claude" / "runner-prompts" / "security-deepsec.md",
}


def _operations_text() -> str:
    return (_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")


def _schedule(loop: str) -> str:
    values = {}
    for line in (_LOOP_ENVS / f"{loop}.env").read_text(encoding="utf-8").splitlines():
        if line.startswith(("SCHEDULE_HOUR=", "SCHEDULE_MINUTE=")):
            key, value = line.split("=", 1)
            values[key] = int(value)
    return f"{values['SCHEDULE_HOUR']:02d}:{values['SCHEDULE_MINUTE']:02d}"


class TestNightlyLoopIndex:
    def test_each_loop_row_states_the_scheduled_fire_time(self):
        text = _operations_text()
        loops = sorted(p.stem for p in _LOOP_ENVS.glob("*.env"))
        assert {"security", "security-deepsec"} <= set(loops)
        for loop in loops:
            fires = _schedule(loop)
            row = next(
                (ln for ln in text.splitlines() if ln.startswith(f"| {loop} |")),
                None,
            )
            assert row is not None, (
                f"docs/operations.md has no nightly-loop row for {loop}"
            )
            assert f"| {fires} |" in row, (
                f"docs/operations.md says {row.strip()} but "
                f"scripts/runner/loops/{loop}.env fires at {fires}"
            )

    def test_the_runner_clone_rail_is_stated(self):
        assert "`~/radon-runner/work/<loop>`" in _operations_text(), (
            "docs/operations.md must state that the security loops refuse a "
            "phase outside their own runner clone"
        )

    def test_the_pre_run_hook_actually_enforces_the_clone_rail(self):
        text = (_ROOT / "scripts" / "runner" / "hooks" / "security_pre.sh").read_text(encoding="utf-8")
        assert '$HOME/radon-runner/work/$LOOP' in text
        assert "remote.origin.url" in text

    def test_deepsec_failure_has_a_safe_operator_path(self):
        """DOC-109: a failed DeepSec loop is never an in-run repair (it is
        operator-bootstrapped)."""
        text = _operations_text()
        assert "A `failed` DeepSec status is operator-only" in text
        assert "DeepSec itself stays operator-bootstrapped (rail 8)" in text
        assert "`sudo launchctl print system/com.radon.runner.security-deepsec`" in text
        assert "Do not bootstrap or restart DeepSec from a nightly run." in text

    # DOC-084 (2026-09-04): the prompts' clone rail must name the loop's own
    # runner clone, not a generic one.
    @pytest.mark.parametrize("loop", sorted(_SECURITY_PROMPTS))
    def test_each_prompt_rail_names_its_own_clone(self, loop):
        text = _SECURITY_PROMPTS[loop].read_text(encoding="utf-8")
        assert f"`~/radon-runner/work/{loop}`" in text, (
            f"the {loop} prompt states the clone rail without its runner clone"
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

_MERGE_MARKER = re.compile(r"^(?:<<<<<<<|=======|>>>>>>>)", re.MULTILINE)
_FINDING_HEADING = re.compile(r"^### (T-\d+) —", re.MULTILINE)


def _assert_ledger_integrity(ledger: str, text: str) -> None:
    marker = _MERGE_MARKER.search(text)
    assert marker is None, (
        f"{ledger} contains an unresolved merge marker at line "
        f"{text.count(chr(10), 0, marker.start()) + 1}"
    )
    headings = _FINDING_HEADING.findall(text)
    duplicates = sorted({heading for heading in headings if headings.count(heading) > 1})
    assert not duplicates, f"{ledger} reuses finding heading(s): {', '.join(duplicates)}"


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


class TestTestLedgersHaveNoUnresolvedMergeState:
    @pytest.mark.parametrize("ledger", ("TEST_AUDIT.md", "TEST_LOG.md"))
    def test_real_ledger_is_integral(self, ledger):
        _assert_ledger_integrity(
            ledger, (_ROOT / ledger).read_text(encoding="utf-8")
        )

    def test_conflict_marker_in_a_copied_ledger_is_rejected(self):
        with pytest.raises(AssertionError, match="unresolved merge marker"):
            _assert_ledger_integrity("copy.md", "# ledger\n<<<<<<< HEAD\n")

    def test_duplicate_finding_heading_in_a_copied_ledger_is_rejected(self):
        with pytest.raises(AssertionError, match="reuses finding heading.*T-492"):
            _assert_ledger_integrity("copy.md", "### T-492 — first\n### T-492 — second\n")


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

    def test_hosted_mcp_runbook_does_not_claim_a_pending_root_copy(self):
        """DOC-073: the stripped radon-mcp unit is installed; the pending-copy
        sentence must not outlive that install."""
        text = (_ROOT / "docs" / "cloud-services.md").read_text(encoding="utf-8")
        assert "unit-mismatch:radon-mcp.service" not in text
        assert "live unit still differs" not in text


class TestOperatorSafetyOwners:
    """DOC-115..119: source-backed operator decisions, not route inventories."""

    def test_hosted_mcp_transport_owner(self):
        caddy = (_ROOT / "cloud/caddy/Caddyfile").read_text()
        cloud = (_ROOT / "cloud/CLAUDE.md").read_text()
        assert "redir https://mcp.radon.run{uri} 308" in caddy
        assert "`http://mcp.radon.run` until HTTPS ACME" not in cloud
        assert "../docs/cloud-services.md#hosted-mcp" in cloud

    def test_sizing_is_distinct_from_order_admission(self):
        doc = (_ROOT / "docs/risk/kelly-fortunes-formula.md").read_text()
        current = doc.split("## Historical design")[0]
        assert "RADON_BANKROLL_CAP_ENFORCE_ALL_PATHS" in current
        assert "defaults off" in current
        assert "RADON_KELLY_ENFORCE_MODE" in current
        assert "verified close-out" in current
        assert "fresh" in current and "warning" in current
        assert "Still required for the implement PR" not in current
        rules = {r["id"]: r for r in _load_owners()["rules"]}
        assert "scripts/bankroll_guard.py" in rules["kelly-sizing"]["globs"]
        assert "scripts/app_preferences.py" in rules["kelly-sizing"]["globs"]
        for placer in ("ib_place_order.py", "ib_execute.py", "exit_order_service.py"):
            assert "check_if_enforced_on_all_paths" in (_ROOT / "scripts" / placer).read_text()

    def test_subscription_recovery_links_opt_in_policy(self):
        ops = (_ROOT / "docs/operations.md").read_text()
        binds = ops.split("**Subscription credential binds")[1].split("The staged copy")[0]
        assert "oauth-subscription-auth.md#radon-http-model-ladder-server" in binds
        assert "RADON_LADDER_ALLOW_PREPAID" in binds
        assert "as the fallback" not in binds
        for path in ("scripts/clients/model_ladder.py", "web/lib/llm/subscriptionAuth.ts"):
            assert "RADON_LADDER_ALLOW_PREPAID" in (_ROOT / path).read_text()

    def test_tradingview_retention_owner_and_migration(self):
        import sqlite3

        doc = (_ROOT / "docs/tradingview-integration.md").read_text()
        assert "sanitized" in doc and "not an exact copy" in doc
        assert "0084_redact_tv_alert_raw_body.sql" in doc
        assert "operator-only" in doc and "version IN (84, 85, 86)" in doc
        assert "0085_redact_tv_alert_raw_body_nested_secret.sql" in doc
        assert "0086_redact_tv_alert_raw_body_residual_secret.sql" in doc
        assert "Parsed columns survive" in doc
        assert "cannot be reconstructed" in doc
        assert "INSERT raw body" not in doc
        cloud = (_ROOT / "docs/cloud-services.md").read_text()
        assert "then writes the raw body" not in cloud
        route = (_ROOT / "web/app/api/webhooks/tradingview/[token]/route.ts").read_text()
        assert "redactSecret(raw, process.env.TV_WEBHOOK_SECRET)" in route
        with sqlite3.connect(":memory:") as db:
            db.executescript("CREATE TABLE tv_alert_events (raw_body TEXT, symbol TEXT);"
                             "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);")
            db.executemany("INSERT INTO tv_alert_events VALUES (?, ?)", [
                ('{"secret":"fixture-only","symbol":"TEST"}', "TEST"),
                ('secret=fixture-only invalid json', "TEST"),
            ])
            migration = (_ROOT / "scripts/db/migrations/0084_redact_tv_alert_raw_body.sql").read_text()
            db.executescript(migration)
            rows = db.execute("SELECT raw_body, symbol FROM tv_alert_events").fetchall()
            assert all("fixture-only" not in raw and symbol == "TEST" for raw, symbol in rows)
            assert json.loads(rows[0][0])["symbol"] == "TEST"
            assert rows[1][0].startswith("[REDACTED PRE-0084")
            db.executescript(migration)
            assert db.execute("SELECT raw_body, symbol FROM tv_alert_events").fetchall() == rows

    def test_tradingview_migration_0085_redacts_nested_secret(self):
        import sqlite3

        with sqlite3.connect(":memory:") as db:
            db.executescript("CREATE TABLE tv_alert_events (raw_body TEXT, symbol TEXT);"
                             "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);")
            db.executemany("INSERT INTO tv_alert_events VALUES (?, ?)", [
                # 0084 only strips a TOP-LEVEL $.secret; this one is nested.
                ('{"payload":{"secret":"fixture-only"},"symbol":"TEST"}', "TEST"),
                # A row 0084 already redacted must not be touched again.
                ('{"secret":"[REDACTED]","symbol":"TEST"}', "TEST"),
            ])
            migration_0084 = (_ROOT / "scripts/db/migrations/0084_redact_tv_alert_raw_body.sql").read_text()
            migration_0085 = (
                _ROOT / "scripts/db/migrations/0085_redact_tv_alert_raw_body_nested_secret.sql"
            ).read_text()
            db.executescript(migration_0084)
            db.executescript(migration_0085)
            rows = db.execute("SELECT raw_body, symbol FROM tv_alert_events").fetchall()
            assert all("fixture-only" not in raw for raw, _symbol in rows)
            assert rows[0][0].startswith("[REDACTED PRE-0085")
            assert rows[1][0] == '{"secret":"[REDACTED]","symbol":"TEST"}'
            db.executescript(migration_0085)
            assert db.execute("SELECT raw_body, symbol FROM tv_alert_events").fetchall() == rows

    def test_tradingview_migration_0086_redacts_nested_secret_behind_redacted_top_level(self):
        import sqlite3

        names = ("0084_redact_tv_alert_raw_body.sql",
                 "0085_redact_tv_alert_raw_body_nested_secret.sql",
                 "0086_redact_tv_alert_raw_body_residual_secret.sql")
        with sqlite3.connect(":memory:") as db:
            db.executescript("CREATE TABLE tv_alert_events (raw_body TEXT, symbol TEXT);"
                             "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);")
            db.executemany("INSERT INTO tv_alert_events VALUES (?, ?)", [
                # Top-level AND nested secret: 0084 rewrites the top level, so
                # 0085's top-level-IS-NULL guard skips the row.
                ('{"secret":"fixture-only","payload":{"secret":"fixture-only"},"symbol":"TEST"}', "TEST"),
                ('{"secret":"fixture-only","note":"secret=fixture-only","symbol":"TEST"}', "TEST"),
                # Fully redacted rows (top level and nested) stay intact.
                ('{"secret":"[REDACTED]","symbol":"TEST"}', "TEST"),
                ('{"secret":"[REDACTED]","payload":{"secret":"[REDACTED]"},"symbol":"TEST"}', "TEST"),
            ])
            for name in names:
                db.executescript((_ROOT / "scripts/db/migrations" / name).read_text())
            rows = db.execute("SELECT raw_body, symbol FROM tv_alert_events").fetchall()
            assert all("fixture-only" not in raw for raw, _symbol in rows)
            assert rows[0][0].startswith("[REDACTED PRE-0086")
            assert rows[1][0].startswith("[REDACTED PRE-0086")
            assert rows[2][0] == '{"secret":"[REDACTED]","symbol":"TEST"}'
            assert rows[3][0] == '{"secret":"[REDACTED]","payload":{"secret":"[REDACTED]"},"symbol":"TEST"}'
            db.executescript((_ROOT / "scripts/db/migrations" / names[2]).read_text())
            assert db.execute("SELECT raw_body, symbol FROM tv_alert_events").fetchall() == rows

    def test_destructive_flex_cleanup_has_recovery_owner(self):
        doc = (_ROOT / "docs/cloud-services.md").read_text()
        section = _section(doc, "Legacy Flex aggregate cleanup")
        for required in ("cleanup_legacy_flex_aggregates", "--help", "--apply", "dry-run",
                         "#restore-runbook", "Stop", "execution", "gross", "operator-only"):
            assert required in section
        rules = {r["id"]: r for r in _load_owners()["rules"]}
        assert "scripts/cleanup_legacy_flex_aggregates.py" in rules["flex-pull"]["globs"]


    def test_flex_gross_rebuild_has_one_recovery_procedure(self):
        doc = (_ROOT / "docs/cloud-services.md").read_text()
        section = _section(doc, "Legacy Flex aggregate cleanup")
        for required in ("rebuild_flex_gross_breakdown", "saved", "execution-level",
                         "target database", "maintenance window", "concurrent journal",
                         "affected rows", "backup", "scratch", "#restore-runbook",
                         "--help", "dry-run", "--apply", "recomputes", "refused",
                         "out of statement period", "post-commit", "partial-table",
                         "Stop", "Escalate"):
            assert required in section, f"Flex recovery owner omits {required}"
        ops = _section((_ROOT / "docs/operations.md").read_text(),
                       "Legacy Flex aggregate gross coverage")
        assert "cloud-services.md#legacy-flex-aggregate-cleanup" in ops
        assert "--apply" not in ops, "Keep mutation instructions in the recovery owner"
        rules = {r["id"]: r for r in _load_owners()["rules"]}
        assert "scripts/rebuild_flex_gross_breakdown.py" in rules["flex-pull"]["globs"]
        source = (_ROOT / "scripts/rebuild_flex_gross_breakdown.py").read_text()
        main = source.split("def main(", 1)[1]
        assert main.index("parser.parse_args") < main.index("db = _connect()")
        assert main.index("plan = plan_rebuild") < main.index("if not args.apply:")
        apply = source.split("def _apply(", 1)[1].split("def main(", 1)[0]
        assert apply.index("db.commit()") < apply.index("verified = 0")


class TestNightlyRecoveryOwnerDrift:
    """DOC-125..127: keep incident decisions at their canonical owner."""

    def test_flex_duplicate_owner_includes_nav_repair_boundary(self):
        doc = (_ROOT / "docs/cloud-services.md").read_text()
        block = doc.split("A duplicate Flex ingest", 1)[1].split("## Legacy Flex", 1)[0]
        assert "missing NAV dates" in block
        assert "cash-flow IDs" in block
        assert "never overwrites" in block
        assert "never replays cash" in block
        assert "incident-runbook.md#flex-pull-activity-nav" in block

    def test_sftp_reset_case_defers_to_delivery_coverage_owner(self):
        doc = (_ROOT / "docs/incident-runbook.md").read_text()
        case = doc.split("## flex-pull-sftp-get-reset", 1)[1].split("\n---", 1)[0]
        assert "cloud-services.md#flex-sftp-pull-radon-flex-pulltimer" in case
        assert "is_transient_sftp_error" not in case
        assert "different query" in case
        assert "unparseable" in case

    def test_nightly_loops_link_the_runner_not_a_retired_wrapper(self):
        doc = (_ROOT / "docs/operations.md").read_text()
        assert 'writable_roots=["$REPO/.git"' not in doc
        assert "../scripts/security_nightly.sh" not in doc
        assert "[docs/runner.md](runner.md)" in doc
        assert "`scripts/runner/hooks/security_pre.sh`" in doc

    def test_provisioning_defers_to_owner_without_retired_override(self):
        """DOC-129: setup recovery must not recommend an ignored trust override."""
        doc = (_ROOT / "docs/operations.md").read_text()
        assert "RADON_PROVENANCE_REMOTE_REF" not in doc
        assert "../cloud/CLAUDE.md#privileged-bootstrap" in doc


class TestExternalProbeDispatchProcedure:
    """DOC-142: the mini dispatcher is an operator install, not a one-line aside."""

    def test_install_and_verify_match_the_plist_and_workflow(self):
        ops = (_ROOT / "docs/operations.md").read_text(encoding="utf-8")
        health = ops.split("## Health monitoring", 1)[1].split("## Service Health", 1)[0]
        assert "### External probe dispatch" in health
        section = health.split("### External probe dispatch", 1)[1]
        workflow = (
            _ROOT / ".github/workflows/external-health-probe.yml"
        ).read_text(encoding="utf-8")
        cron = 'cron: "1-56/5 * * * *"'
        assert cron in workflow
        assert "1-56/5 * * * *" in health
        assert "*/5" not in health
        assert "bash scripts/setup_external_probe_dispatch.sh" in section
        assert "com.radon.external-probe-dispatch.plist" in section
        assert "__HOME__" in section
        assert "external-probe-dispatch.log" in section
        assert "external-probe-dispatch.err" in section
        assert "gh auth status" in section
        assert "StartInterval" in section and "300" in section
        assert "does not cancel an in-flight" in section
        assert 'cancel-in-progress: false' in workflow
        assert "launchctl bootout" in section
        assert "RADON_PROBE_FRESHNESS_TOKEN" in health
        assert "TURSO_DB_URL" in health and "TURSO_AUTH_TOKEN" in health
        script = (_ROOT / "scripts/setup_external_probe_dispatch.sh").read_text(
            encoding="utf-8"
        )
        assert 'sed "s|__HOME__|$HOME|g"' in script
        assert "external-probe-dispatch.{log,err}" in script
        plist = (
            _ROOT / "config/com.radon.external-probe-dispatch.plist"
        ).read_text(encoding="utf-8")
        assert "<integer>300</integer>" in plist
        assert "external-probe-dispatch.log" in plist
        assert "external-probe-dispatch.err" in plist
        assert "gh" in plist and "external-health-probe.yml" in plist


class TestModelLadderByteCapOwner:
    """DOC-144: vision HTTP responses share the text ladder's byte cap."""

    def test_extract_via_vision_cap_is_owned(self):
        rules = {r["id"]: r for r in _load_owners()["rules"]}
        rule = rules["model-ladder"]
        assert rule["globs"] == ["scripts/clients/model_ladder.py"]
        assert rule["owners"] == ["docs/dropbox-research.md"]
        doc = (_ROOT / "docs/dropbox-research.md").read_text(encoding="utf-8")
        ladder = doc.split("### Model ladder (shared HTTP)", 1)[1].split(
            "### Verified host placement", 1
        )[0]
        assert "extract_via_vision" in ladder
        assert "2,000,000" in ladder
        assert "Antigravity" in ladder
        source = (_ROOT / "scripts/clients/model_ladder.py").read_text(encoding="utf-8")
        vision = source.split("def extract_via_vision(", 1)[1].split("\ndef ", 1)[0]
        assert "max_response_bytes: int = 2_000_000" in vision
        call = source.split("def _call_vision_provider(", 1)[1].split("\ndef ", 1)[0]
        assert call.count("max_bytes=max_response_bytes") == 6
        assert "_antigravity_complete" in call


class TestShareRecoveryDoc:
    def test_share_recovery_requires_a_verified_stream_result(self):
        doc = (_ROOT / "docs/incident-runbook.md").read_text()
        case = doc.split("## newsfeed-share-missing-subscription-502", 1)[1].split("\n## ", 1)[0]
        assert "HTTP 200 alone" in case
        assert "`result`" in case and "`error`" in case
        assert "operations.md#encrypted-credential-store-profile-credentials-tab" in case
        for label in ("Prerequisites", "Blast radius", "Diagnosis", "Stop", "Verify", "Rollback", "Escalate"):
            assert f"**{label}:**" in case


class TestNightlyReportingDoc:
    def test_nightly_reporting_scopes_match_the_runner_prompts(self):
        ops = (_ROOT / "docs/operations.md").read_text()
        cycle = next(line for line in ops.splitlines() if "**Cycle shape" in line)
        assert cycle.startswith("**Cycle shape (security and DeepSec).**")
        checkpoint = next(line for line in ops.splitlines() if "**No-op checkpoints" in line)
        listed = re.search(r"runner loops \(([^)]+)\)", checkpoint).group(1)
        loops = {name.strip() for name in listed.split(",")}
        prompts = _ROOT / ".claude/runner-prompts"
        expected = {
            name for name in ("reliability", "testing", "ci-performance", "documentation")
            if "audited-through:" in (prompts / f"{name}.md").read_text()
        }
        assert loops == expected
        assert "runner.md#9-smoke-test-bot-shell" in ops


class TestResearchCutCommandDoc:
    @pytest.mark.parametrize("module", ["worker", "harness", "cut_report"])
    def test_documented_research_commands_resolve_from_repository_root(self, module):
        doc = (_ROOT / "docs/dropbox-research.md").read_text()
        commands = re.findall(r"`([^`]*\bpython[\d.]* -m research\." + module + r"\b[^`]*)`", doc)
        assert commands, f"No documented research.{module} invocation"
        for command in commands:
            argv = shlex.split(command)
            env = {"PATH": os.environ["PATH"]}
            while "=" in argv[0]:
                key, value = argv.pop(0).split("=", 1)
                env[key] = value
            argv[0] = sys.executable
            # --help exits at argparse, before private files, auth or network.
            result = subprocess.run([*argv, "--help"], cwd=_ROOT, env=env, text=True, capture_output=True, timeout=10)
            assert result.returncode == 0, f"{command}: {result.stderr}"
            if module == "cut_report":
                assert "--from-json" in result.stdout
            for flag in (arg for arg in argv if arg.startswith("--")):
                assert flag in result.stdout, f"Undocumented parser flag: {flag}"


class TestCloudModeDocumentation:
    def test_cloud_thin_mode_has_one_owner(self):
        launcher = (_ROOT / "scripts/cloud.sh").read_text()
        dev = (_ROOT / "scripts/dev").read_text()
        assert 'export RADON_DEV_PROFILE="cloud-thin"' in launcher
        thin = dev.split('if [[ "$PROFILE" == "cloud-thin" ]]; then', 1)[1].split("\nfi", 1)[0]
        assert "exec next dev" in thin
        owner = (_ROOT / "docs/cloud-services.md").read_text()
        assert "cloud-thin" in _section(owner, "Mode switch")
        assert "laptop runs only Next.js" in owner
        for path in ("README.md", "CLAUDE.md"):
            text = (_ROOT / path).read_text()
            assert "docs/cloud-services.md#mode-switch" in text
            assert "Next.js + newsfeed" not in text
        assert "not in `setup-vps.sh` yet" not in owner


class TestGrokBinaryRecoveryDocumentation:
    def test_incident_recovery_defers_to_trusted_locked_owner(self):
        cases = (_ROOT / "docs/incident-runbook.md").read_text()
        case = _section(cases, "grok-live-binary-relinked-by-pytest")
        assert "grok-page-responder.md#binary-recovery" in case
        owner = _section((_ROOT / "docs/grok-page-responder.md").read_text(), "Binary recovery")
        for required in ("lkg_binary_problem", "exclusive_lock", "stop", "verify", "rollback", "escalate"):
            assert required in owner.casefold(), required
        upgrade = _section(cases, "grok-upgrade-update-rejects-no-auto-update")
        assert "LKG is still absent" not in upgrade
        assert "timer installs\n  `1.0.44`" not in upgrade


class TestOperatorHoldDesignBoundary:
    """DOC-152: proposed release extensions are not deployed safety gates."""

    def test_proposal_defers_operator_actions_to_current_runbook(self):
        design = (_ROOT / "docs/ibkr-session-release.md").read_text()
        preface = design.split("## 1.", 1)[0]
        assert "[current operator procedure](ib-gateway-recovery.md#runbook-flatten-from-ibkr-mobile-while-the-app-is-down)" in preface
        assert "historical proposal, not deployed guarantees" in preface
        assert "does not set a trading halt" in preface
        assert "does not stop local Gateways" in preface
        for flag in ("--full", "--with-trading", "--force-lease"):
            assert flag in preface
        assert "Do not execute the proposed procedures below" in preface

    def test_mobile_flatten_waits_for_confirmed_release_and_persisted_hold(self):
        doc = (_ROOT / "docs/ib-gateway-recovery.md").read_text()
        procedure = _section(doc, "Runbook: flatten from IBKR Mobile while the app is down")
        release_step = next(line for line in procedure.splitlines() if line.startswith("1. "))
        for required in ("exit status 0", "`RELEASED`", "`radon ib status`", '"held": true'):
            assert required in release_step, required
        for required in ("HOLD NOT WRITTEN", "state unknown", "do not clear", "before reboot"):
            assert required in procedure, required
        assert "does not set a trading halt" in procedure
        for label in ("Symptom", "Prerequisites", "Blast radius", "Diagnosis", "Stop", "Verification", "Rollback", "Escalation"):
            assert f"**{label}:**" in procedure


class TestIncidentPublicationOwner:
    """DOC-155: recovery guidance follows the gate's expanded detectors."""

    def test_pickup_owner_covers_generic_and_environment_credential_refusals(self):
        procedure = _section((_ROOT / "docs/grok-page-responder.md").read_text(),
                             "Open-PR path: the Mac mini picks the branch up")
        for required in ("opaque literal", "credential-named", "verbatim", "pickup process",
                         "../scripts/ir_push_gate.py", "../scripts/credential_redaction.py"):
            assert required in procedure, required
        for label in ("Symptom", "Prerequisites", "Blast radius", "Diagnosis", "Stop", "Verification", "Rollback", "Escalation"):
            assert f"**{label}:**" in procedure

    def test_publication_gate_changes_reach_the_recovery_owner(self):
        rules = _load_owners()["rules"]
        assert _violations(["scripts/ir_push_gate.py"], rules)
        assert _violations(["scripts/ir_push_gate.py", "docs/grok-page-responder.md"], rules) == []


class TestSubscriptionRecoveryBillingOwner:
    """DOC-156: token recovery must not promise automatic prepaid fallback."""

    def test_token_daemon_intro_defers_billing_to_auth_owner(self):
        intro = (_ROOT / "docs/subscription-tokens.md").read_text().split("## Per-provider contract", 1)[0]
        assert "silently demotes" not in intro
        assert "oauth-subscription-auth.md#radon-http-model-ladder-server" in intro
        assert "unavailable" in intro
        module_intro = (_ROOT / "scripts/subscription_tokens.py").read_text().split('"""', 2)[1]
        assert "silently demotes" not in module_intro

    def test_reauth_runbook_states_its_recovery_boundaries(self):
        procedure = _section((_ROOT / "docs/subscription-tokens.md").read_text(),
                             "Operator re-auth runbook")
        for label in ("Symptom", "Prerequisites", "Blast radius", "Diagnosis", "Stop", "Verification", "Rollback", "Escalation"):
            assert f"**{label}:**" in procedure

    def test_vault_mutations_use_the_service_credential_context(self):
        doc = (_ROOT / "docs/subscription-tokens.md").read_text()
        section = _section(doc, "Sealing a freshly created credential")
        commands = re.findall(r"```bash\n(.*?)```", section, re.S)
        assert commands
        assert not any(re.search(r"subscription_tokens\s+--(?:seal|restore)\b", command)
                       for command in commands), "A bare SSH shell lacks the unit's vault context"
        assert "systemctl start radon-subscription-tokens.service" in section
        assert "systemctl status radon-subscription-tokens.service" in section
        assert "different store" in section and "decrypted key" in section
        assert "operations.md#encrypted-credential-store-profile-credentials-tab" in section
        unit = (_ROOT / "cloud/services/radon-subscription-tokens.service").read_text()
        # DS-2026-10-05-05: the vault context is the broker socket, never the key.
        assert "Environment=RADON_SUBSCRIPTION_VAULT_SOCKET=" in unit
        assert "LoadCredentialEncrypted" not in unit
        assert "radon-subscription-vault.service" in section
        assert "-m scripts.subscription_tokens --once" in unit


class TestApiTimerOwner:
    """DOC-157: API instructions should link schedules instead of copying them."""

    def test_api_timer_instructions_defer_to_operator_and_executable_owners(self):
        timers = _section((_ROOT / "scripts/api/CLAUDE.md").read_text(), "Autonomous Timers (Hetzner)")
        assert "| Timer |" not in timers
        assert "radon-cloud/services/" not in timers
        assert "../../docs/operations.md#background-services" in timers
        assert "../../cloud/services/" in timers
        assert "literal env" in timers
        ops = (_ROOT / "docs/operations.md").read_text()
        row = next(line for line in ops.splitlines() if line.startswith("| `radon-refresh.timer` |"))
        assert "../cloud/services/radon-refresh.timer" in row
        assert "60s" not in row


class TestCredentialSetupOwners:
    """DOC-150/151: setup instructions must select the implemented auth path."""

    def test_probe_overview_defers_credential_setup_to_environment_owner(self):
        ops = (_ROOT / "docs/operations.md").read_text()
        health = ops.split("## Health monitoring", 1)[1].split("## Service Health", 1)[0]
        overview, procedure = health.split("### External probe dispatch", 1)
        assert "Repo secrets the workflow reads" not in overview
        assert "Credential placement" in overview
        workflow = (_ROOT / ".github/workflows/external-health-probe.yml").read_text()
        environment = re.search(r"^    environment: (\S+)$", workflow, re.M).group(1)
        assert f"`{environment}` GitHub Environment" in procedure
        for name in set(re.findall(r"secrets\.([A-Z_]+)", workflow)):
            assert f"`{name}`" in procedure
        assert "not in repository secrets" in procedure

    def test_antigravity_setup_uses_the_cli_owner_without_prepaid_exception(self):
        research = (_ROOT / "docs/dropbox-research.md").read_text()
        row = next(line for line in research.splitlines() if line.startswith("| antigravity |"))
        assert "oauth-subscription-auth.md#radon-http-model-ladder-server" in row
        assert "Antigravity CLI" in row
        assert "GEMINI_OAUTH_TOKEN" not in row and "GEMINI_API_KEY" not in row
        owner = _section((_ROOT / "docs/oauth-subscription-auth.md").read_text(), "Radon HTTP model ladder (server)")
        assert "Gemini" not in owner and "GEMINI_API_KEY" not in owner
        assert "no Google API key or OAuth-token path at all, under any flag" in owner
        assert "Weekend bash wrappers" not in owner
        assert "[nightly runner](runner.md)" in owner


class TestRecurringOwnerCoverage:
    """DOC-143/145/146/148: actual paths must trigger their existing owner."""

    @pytest.mark.parametrize("source,owner", [
        ("config/com.radon.external-probe-dispatch.plist", "docs/operations.md"),
        ("scripts/credential_redaction.py", "docs/security-audit-playbook.md"),
        ("scripts/research/worker.py", "docs/dropbox-research.md"),
        ("cloud/services/radon-knowledge-eval.service", "docs/knowledge-embeddings.md"),
    ])
    def test_recurring_contract_cannot_change_without_owner(self, source, owner):
        assert (_ROOT / source).is_file()
        rules = _load_owners()["rules"]
        matching = [rule for rule in rules if any(_matches(source, glob) for glob in rule["globs"])]
        assert matching, f"No documentation owner for {source}"
        assert any(owner in rule["owners"] for rule in matching)
        assert _violations([source], matching), "Mapped source alone must fail ownership"
        assert _violations([source, owner], matching) == []


class TestRunnerRunbookRootWrites:
    """Root never reads, writes or chowns through a path the bot account controls."""

    def test_root_commands_do_not_touch_bot_home_files(self):
        doc = (_ROOT / "docs/runner.md").read_text()
        offenders = [
            line.strip() for line in doc.splitlines()
            if re.search(r"\bsudo\s+(?!-u\s)", line)
            and "/Users/_radonbot" in line
            and re.search(r">|\bcat\b|\bchown\b|\bmv\b", line)
        ]
        assert offenders == []

    def test_secret_copies_write_as_the_bot(self):
        doc = (_ROOT / "docs/runner.md").read_text()
        for target in ("agent-cli/env", ".radon-runner.env", "pushover.env"):
            assert any(
                "sudo -u _radonbot /bin/sh -c" in line and target in line and ">" in line
                for line in doc.splitlines()
            ), target


class TestGatewayDailyCycleDocumentation:
    """DOC-159: an empty IBC field is not a disabled broker daily cycle."""

    @pytest.mark.parametrize("path", [
        "docs/implement.md", "docs/ib_tws_api.md", "docs/ib-connection-troubleshooting.md",
        "scripts/api/CLAUDE.md",
    ])
    def test_local_setup_defers_cycle_semantics_to_recovery_owner(self, path):
        text = (_ROOT / path).read_text()
        assert "ib-gateway-recovery.md#daily-cycle" in text
        assert "`AutoRestartTime` | blank | Disabled" not in text
        assert "`AutoRestartTime=` - disabled" not in text
        assert "Disabled: no RunAtLoad/calendar, AutoRestartTime, or ColdRestartTime" not in text

    def test_cycle_owner_explains_blank_fields_without_copying_schedule(self):
        owner = _section((_ROOT / "docs/ib-gateway-recovery.md").read_text(), "Daily cycle")
        for required in ("blank", "stored", "does not disable", "setup_ibc.sh", "docker-compose.yml", "2FA", "stop", "rollback", "escalate"):
            assert required.casefold() in owner.casefold(), required
        assert "11:45 PM" not in owner and "4:45 PM PT" not in owner


class TestRelayRecoveryDocumentation:
    """DOC-160: actor and mode boundaries live beside broker recovery gates."""

    @pytest.mark.parametrize("path,target", [
        ("scripts/CLAUDE.md", "../docs/ib-gateway-recovery.md#relay-recovery"),
        ("web/CLAUDE.md", "../docs/ib-gateway-recovery.md#relay-recovery"),
        ("docs/ib-connection-troubleshooting.md", "ib-gateway-recovery.md#relay-recovery"),
    ])
    def test_relay_overviews_link_the_owner_without_contradictory_restart_claims(self, path, target):
        text = (_ROOT / path).read_text()
        assert target in text
        for stale in ("never a relay-initiated Gateway restart", "NEVER restarts the IB Gateway", "45s during market hours → restart Gateway"):
            assert stale not in text

    def test_recovery_owner_explains_escalation_and_safe_operator_boundaries(self):
        owner = _section((_ROOT / "docs/ib-gateway-recovery.md").read_text(), "Relay recovery")
        for required in ("shouldRequestGatewayRestart", "docker", "cloud", "launchd", "POST /ib/restart", "operator hold", "lease", "backoff", "stop", "verification", "rollback", "escalate"):
            assert required.casefold() in owner.casefold(), required


class TestGatewayReadinessDocumentation:
    """DOC-161: listening sockets are diagnostic evidence, not readiness."""

    def test_docker_health_defers_machine_inventory_and_requires_authentication(self):
        text = (_ROOT / "docs/ib-gateway-docker.md").read_text()
        health = _section(text, "Healthcheck")
        assert "docker-compose.yml" in health
        assert "does not prove authentication" in health
        assert "ib-gateway-recovery.md#readiness-verification" in health
        assert "healthy**: IB Gateway API is accepting connections" not in health
        assert "2FA pending, login failed" not in health
        assert "4:45 PM PT" not in text

    def test_startup_and_manual_recovery_require_authenticated_health(self):
        root = (_ROOT / "CLAUDE.md").read_text()
        startup = _section(root, "Startup Checklist")
        assert "auth_state: authenticated" in startup
        text = (_ROOT / "docs/ib-connection-troubleshooting.md").read_text()
        assert "# Check: ib_gateway.auth_state=authenticated" in text
        owner = _section((_ROOT / "docs/ib-gateway-recovery.md").read_text(), "Readiness verification")
        for required in ("auth_state=authenticated", "managed_accounts", "unknown", "remote", "stop", "rollback", "escalate"):
            assert required.casefold() in owner.casefold(), required


class TestClientOwnershipDocumentation:
    """DOC-162/163: connection cleanup must preserve order-owner identity."""

    def test_collision_runbook_has_no_static_ids_or_broad_process_kill(self):
        text = (_ROOT / "docs/ib-connection-troubleshooting.md").read_text()
        assert "**Client ID registry**" not in text
        assert "pkill -9" not in text
        assert "find and kill it" not in text
        assert "../scripts/CLAUDE.md#client-id-ranges" in text
        assert "../scripts/CLAUDE.md#order-placement-contract-ib_place_orderpy" in text
        assert "permId" in text

    def test_api_reference_defers_allocation_and_cancel_ownership(self):
        text = (_ROOT / "docs/ib_tws_api.md").read_text()
        assert "Default to `clientId=0`" not in text
        assert "cancel/modify ANY order" not in text
        assert "Can cancel ANY order" not in text
        assert "Can modify ANY order" not in text
        assert "connects as master to handle TWS-placed orders" not in text
        assert "| ID | Script | Purpose |" not in text
        assert "../scripts/CLAUDE.md#client-id-ranges" in text
        assert "../scripts/CLAUDE.md#cancel--modify-scripts-side" in text
        assert "ib_order_manage.py" in text
        assert "original" in text


def _network_procedure(heading: str) -> str:
    text = (_ROOT / "docs/operations.md").read_text()
    start = text.index(f"### {heading}")
    end = text.find("\n### ", start + 1)
    return text[start:end] if end != -1 else text[start:]


class TestNetworkTrustRolloutDocumentation:
    """DOC-164/165: access changes need bounded recovery and proof of denial."""

    @pytest.mark.parametrize("heading", ["Tailnet policy", "Tailnet trust narrowing"])
    def test_procedure_has_recovery_boundaries(self, heading):
        procedure = _network_procedure(heading).casefold()
        for boundary in ("symptom", "prerequisites", "blast radius", "diagnosis",
                         "stop", "verification", "rollback", "escalation"):
            assert boundary in procedure, f"{heading}: missing {boundary}"

    def test_trust_reload_targets_only_the_api(self):
        procedure = _network_procedure("Tailnet trust narrowing")
        assert "`radon restart`" not in procedure
        assert "radon unit restart radon-api.service" in procedure
        assert "operator-radon.sh" in procedure

    def test_enforcement_verifies_denial_without_a_session(self):
        procedure = _network_procedure("Tailnet trust narrowing")
        assert "/health/lite" in procedure and "401" in procedure
        assert "without credentials" in procedure
        assert "HTTP 200" in procedure and '{"status":"ok"}' in procedure
        assert "test_tailnet_trust_narrowing.py" in procedure

    def test_policy_uses_live_inventory_and_machine_owned_grants(self):
        procedure = _network_procedure("Tailnet policy")
        assert "logged out of Tailscale (2026-10-01)" not in procedure
        assert "Remove stale devices (`" not in procedure
        assert "Grants: operator devices" not in procedure
        assert "fresh SSH" in procedure
        assert "record the current machine tags" in procedure
        assert "test_tailnet_policy.py" in procedure


class TestResearchAliasDocumentation:
    """DOC-166: one discovery contract must preserve watched folder aliases."""

    def test_discovery_does_not_claim_all_unpadded_scopes_are_fallback_only(self):
        text = (_ROOT / "docs/dropbox-research.md").read_text()
        assert "the unpadded spelling is listed only when the padded folder is absent" not in text
        discovery = next(line for line in text.splitlines() if line.startswith("- `research.state`:"))
        assert "watched" in discovery and "padded" in discovery
        assert "test_research_ingestion.py" in discovery
        assert "Saved unpadded day-folder cursors remain polled" not in text


class TestResearchDiagnosticDocumentation:
    """DOC-167: extraction detail is retained at health and queue sinks."""

    def test_journal_guidance_does_not_contradict_retained_extraction_detail(self):
        text = (_ROOT / "docs/dropbox-research.md").read_text()
        assert "Journal output contains stage counts/error classes only." not in text
        assert "Extraction `EvidenceError` text is persisted" in text


class TestNightlyInfrastructureOwners:
    """DOC-169..173: source-backed infrastructure and recovery owners."""

    def test_external_services_defers_host_roles_to_topology_owner(self):
        doc = (_ROOT / "docs/external-services.md").read_text()
        row = next(line for line in doc.splitlines() if line.startswith("| **Hetzner Cloud** |"))
        assert "spof-host-split.md" in row
        assert "operations.md#encrypted-credential-store-profile-credentials-tab" in row
        assert "Resolved as `ib-gateway`" not in row
        assert "VPS that hosts FastAPI, IB Gateway" not in row

    def test_gateway_logging_has_one_scope_aware_recovery_owner(self):
        local = (_ROOT / "docs/ib-gateway-docker.md").read_text()
        assert "ib-gateway-recovery.md#gateway-logs" in local
        assert "journald driver" not in local
        owner = _section((_ROOT / "docs/ib-gateway-recovery.md").read_text(), "Gateway logs")
        assert "../cloud/docker-compose.yml" in owner
        assert "../docker/ib-gateway/docker-compose.yml" in owner
        assert "installed" in owner and "recreate" in owner
        assert "journalctl CONTAINER_TAG=ib-gateway" in owner
        assert "radon-docker-gw logs" in owner
        assert "scripts/docker_ib_gateway.sh logs" in owner
        for boundary in ("Symptom", "Prerequisites", "Blast radius", "Diagnosis", "Stop", "Verification", "Rollback", "Escalation"):
            assert f"**{boundary}:**" in owner

    def test_cloud_firewall_recovery_stops_on_indeterminate_mutation(self):
        owner = _network_procedure("Hetzner Cloud Firewalls").split("\n## ", 1)[0]
        assert "hcloud_firewalls.py" in owner and "test_hcloud_firewall_faults.py" in owner
        assert "indeterminate" in owner and "before another apply" in owner
        assert "rules and attachment" in owner
        assert "inventory" in owner
        for boundary in ("Symptom", "Prerequisites", "Blast radius", "Diagnosis", "Stop", "Verification", "Rollback", "Escalation"):
            assert f"**{boundary}:**" in owner

    def test_cloud_overviews_do_not_prescribe_obsolete_recovery(self):
        readme = (_ROOT / "cloud/README.md").read_text()
        assert "generates SSH key, exits" not in readme
        assert "Add the printed SSH key to GitHub" not in readme
        assert "Wipe everything (keeps SSH, firewall, IP)" not in readme
        assert "ssh -L 5900:127.0.0.1:5900 radon@radon-app" not in readme
        assert "ssh root@radon-app 'radon restart'" not in readme
        assert "IBC scheduled/cold restarts are blank" not in readme
        assert "VPS services reach IB Gateway over loopback" not in readme
        assert "operator-reviewed maintenance" not in readme
        recovery = (_ROOT / "docs/ib-gateway-recovery.md").read_text()
        assert "separately reviewed broker maintenance" not in recovery
        assert "../docs/ib-gateway-recovery.md" in readme
        assert "../docs/spof-host-split.md" in readme
        html = (_ROOT / "cloud/radon-cloud-deployment-guide.html").read_text()
        boundary = html.split("<body>", 1)[1].split("<h1>", 1)[0]
        assert "Historical reference" in boundary and "Do not execute" in boundary
        assert "CLAUDE.md" in boundary and "../docs/ib-gateway-recovery.md" in boundary

    def test_cloud_release_owner_does_not_make_required_images_optional(self):
        import yaml

        workflow = yaml.safe_load((_ROOT / ".github/workflows/ci.yml").read_text())
        assert "app-images" in workflow["jobs"]["deploy"]["needs"]
        source = (_ROOT / "cloud/scripts/deploy.sh").read_text()
        assert 'prepull_app_images "$requested_sha"' in source
        owner = (_ROOT / "cloud/CLAUDE.md").read_text()
        assert "not a `ci.yml` deploy `needs`" not in owner
        assert "images optional" not in owner
        assert "Canonical future secrets path" not in owner
        assert "Unit `EnvironmentFile=` is unchanged" not in owner
        for name in ("radon-api", "radon-nextjs"):
            unit = (_ROOT / f"cloud/services/{name}.service").read_text()
            assert "EnvironmentFile=/etc/radon/env" in unit
        assert "app-images" in owner and "required" in owner
        assert "test_ci_deploy_image_reuse.py" in owner
        readme = (_ROOT / "cloud/README.md").read_text()
        assert "CLAUDE.md#deployment-contract" in readme
        assert "Build Bun artifacts and Python wheels in a detached worktree" not in readme

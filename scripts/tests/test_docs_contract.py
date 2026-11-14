"""Docs contract: structure, ownership, ledgers and documented drills.

Durable facts have one owner file. This test fails when the owner map, the
doc indexes or the append-only ledgers drift, when a mapped path changes
without its owner doc (unless the commit message contains
``docs-skip: <reason>``), or when a documented command stops running.

It does not pin wording. A doc fix is verified by these structural checks and
the documentation review, not by a new substring assertion per fix.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import types
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

    def test_readme_defers_catalog_and_index_to_docs(self):
        text = (_ROOT / "README.md").read_text(encoding="utf-8")
        assert "## External services" not in text
        assert "## What's where" not in text
        assert "## Glossary" not in text
        assert "docs/README.md" in text
        assert "docs/external-services.md" in text
        assert "SECURITY.md" in text
        assert "SUPPORT.md" in text

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


# Docs may not claim a key is (or is not) in cloud/config/required-env.txt
# when the file says otherwise (T-163).

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


# docs/operations.md indexes every nightly loop; its fire times are derived
# from each loop env's SCHEDULE_HOUR:MINUTE (DOC-032/033).

_LOOP_ENVS = _ROOT / "scripts" / "runner" / "loops"


def _operations_text() -> str:
    return (_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")


_WEEKDAY_NAME = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")


def _schedule(loop: str) -> str:
    values = {}
    weekdays = ""
    for line in (_LOOP_ENVS / f"{loop}.env").read_text(encoding="utf-8").splitlines():
        if line.startswith("SCHEDULE_WEEKDAYS="):
            weekdays = line.split("=", 1)[1].strip()
        elif line.startswith(("SCHEDULE_HOUR=", "SCHEDULE_MINUTE=")):
            key, value = line.split("=", 1)
            values[key] = int(value)
    clock = f"{values['SCHEDULE_HOUR']:02d}:{values['SCHEDULE_MINUTE']:02d}"
    if not weekdays:
        return clock
    names = "/".join(_WEEKDAY_NAME[int(part)] for part in weekdays.split(","))
    return f"{clock} {names}"


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

    def test_the_pre_run_hook_actually_enforces_the_clone_rail(self):
        text = (_ROOT / "scripts" / "runner" / "hooks" / "security_pre.sh").read_text(encoding="utf-8")
        assert '$HOME/radon-runner/work/$LOOP' in text
        assert "remote.origin.url" in text


# Root ledgers are append-only: entries may only be added against the base
# the change is reviewed against, and a merge may not leave conflict markers or
# reuse a finding heading (DOC-021, T-492). Naming each ledger here also routes
# a ledger-only change to this gate in scripts/ci/path_filter.py (DOC-045).

_LEDGERS = {
    "TEST_LOG.md": r"^\| T-\d{3} \|",
    "TEST_AUDIT.md": r"^\| T-\d+",
    "REMEDIATION_LOG.md": r"^\| T-\d+",
    "RELIABILITY_AUDIT.md": r"^\| R-\d+",
    "RELIABILITY_LOG.md": r"^\| REL-\d+",
    "CI_PERFORMANCE_LOG.md": r"^### CIP-\d+",
}
_MERGE_MARKER = re.compile(r"^(?:<<<<<<<|=======|>>>>>>>)", re.MULTILINE)
_FINDING_HEADING = re.compile(r"^### (T-\d+) —", re.MULTILINE)


def _ledger_base_ref() -> str | None:
    base = (os.environ.get("DOCS_CONTRACT_BASE") or "").strip()
    if base in {"", _ZERO}:
        base = "origin/main"
    try:
        _git("rev-parse", "--verify", base)
    except subprocess.CalledProcessError:
        return None
    return base


def _assert_ledger_integrity(ledger: str, text: str) -> None:
    marker = _MERGE_MARKER.search(text)
    assert marker is None, (
        f"{ledger} contains an unresolved merge marker at line "
        f"{text.count(chr(10), 0, marker.start()) + 1}"
    )
    headings = _FINDING_HEADING.findall(text)
    duplicates = sorted({heading for heading in headings if headings.count(heading) > 1})
    assert not duplicates, f"{ledger} reuses finding heading(s): {', '.join(duplicates)}"


class TestRootLedgers:
    def test_test_log_header_is_present(self):
        text = (_ROOT / "TEST_LOG.md").read_text(encoding="utf-8")
        assert text.startswith("# TEST_LOG.md — testing remediation execution log"), (
            "TEST_LOG.md lost its header: the ledger was overwritten, not appended"
        )

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
            f"{ledger} has {is_now} entries but {base} has {was}: the ledgers "
            "are append-only; restore the rows instead of rewriting history"
        )

    @pytest.mark.parametrize("ledger", sorted(_LEDGERS))
    def test_real_ledger_is_integral(self, ledger):
        path = _ROOT / ledger
        if not path.is_file():
            pytest.skip(f"{ledger} is not in this tree")
        _assert_ledger_integrity(ledger, path.read_text(encoding="utf-8"))

    def test_conflict_marker_in_a_copied_ledger_is_rejected(self):
        with pytest.raises(AssertionError, match="unresolved merge marker"):
            _assert_ledger_integrity("copy.md", "# ledger\n<<<<<<< HEAD\n")

    def test_duplicate_finding_heading_in_a_copied_ledger_is_rejected(self):
        with pytest.raises(AssertionError, match="reuses finding heading.*T-492"):
            _assert_ledger_integrity("copy.md", "### T-492 — first\n### T-492 — second\n")


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


# A doc may say a unit still owes its root install-copy only while the unit
# is absent from cloud/config/installed-units.sha256 (DOC-057).

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


class TestResearchCutCommandDoc:
    @pytest.mark.parametrize("module", ["cut_report"])
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


class TestRecurringOwnerCoverage:
    """DOC-143/145/146/148: actual paths must trigger their existing owner."""

    @pytest.mark.parametrize("source,owner", [
        ("config/com.radon.external-probe-dispatch.plist", "docs/operations.md"),
        ("scripts/credential_redaction.py", "docs/security-audit-playbook.md"),
        ("scripts/research/identify.py", "docs/dropbox-research.md"),
        ("cloud/services/radon-knowledge-eval.service", "docs/knowledge-embeddings.md"),
        ("scripts/clients/tradingview_client.py", "docs/tradingview-mcp-client.md"),
        ("scripts/clients/tradingview_auth.py", "docs/tradingview-mcp-client.md"),
        ("scripts/tradingview_mcp.py", "docs/tradingview-mcp-client.md"),
        ("scripts/api/routes/tradingview.py", "docs/tradingview-mcp-client.md"),
        ("scripts/ir_push_gate.py", "docs/grok-page-responder.md"),
    ])
    def test_recurring_contract_cannot_change_without_owner(self, source, owner):
        assert (_ROOT / source).is_file()
        rules = _load_owners()["rules"]
        matching = [rule for rule in rules if any(_matches(source, glob) for glob in rule["globs"])]
        assert matching, f"No documentation owner for {source}"
        assert any(owner in rule["owners"] for rule in matching)
        assert _violations([source], matching), "Mapped source alone must fail ownership"
        assert _violations([source, owner], matching) == []


class TestVcgSpecIndexLinks:
    """DOC-178: parentheses in the VCG spec filename break unencoded Markdown links."""

    def test_index_and_strategy_links_encode_parentheses(self):
        encoded = "cross_asset_volatility_credit_gap_spec_%28VCG%29.md"
        raw = "](cross_asset_volatility_credit_gap_spec_(VCG).md)"
        for rel in ("docs/README.md", "docs/strategies.md"):
            text = (_ROOT / rel).read_text()
            assert encoded in text, rel
            assert raw not in text, rel


class TestAiCycleCadenceDoc:
    """The Ramp row is derived from the executed registry, not pinned prose."""

    def test_ramp_d7_cadence_is_not_the_monthly_source(self):
        sys.path.insert(0, str(_ROOT))
        from scripts.ai_cycle.collectors import RAMP_PRICE_WINDOW_DAYS
        from scripts.ai_cycle.registry import series_cadence

        assert series_cadence("D7", "tokens.price.blended.anthropic") == "daily"
        assert series_cadence("D7", "tokens.volume.all") == "weekly"
        assert series_cadence("D7", "tokens.spend.all") == "weekly"
        cell = next(
            line for line in (_ROOT / "docs/ai-infrastructure-operations.md").read_text().splitlines()
            if line.startswith("| Ramp AI Index |")
        )
        assert f"{RAMP_PRICE_WINDOW_DAYS}-day" in cell
        assert "daily" in cell
        assert "weekly" in cell


class TestKnowledgeGoldenDocPins:
    """Every golden-set docs pin must match a doc the KB can ingest."""

    def test_golden_docs_patterns_match_a_file(self):
        golden = json.loads(
            (_ROOT / "scripts/knowledge/golden_set.json").read_text(encoding="utf-8")
        )
        docs = [
            str(path.relative_to(_ROOT))
            for path in (_ROOT / "docs").rglob("*.md")
        ]
        missing = []
        for entry in golden["questions"]:
            for want in entry["relevant"]:
                if want["source"] != "docs":
                    continue
                pattern = want["doc_key_pattern"]
                if not pattern.startswith("^docs/"):
                    continue
                rx = re.compile(pattern)
                if not any(rx.search(path) for path in docs):
                    missing.append(entry["id"])
        assert missing == []

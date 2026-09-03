"""Every env var a scheduled unit reads without a default must be in the contract.

T-163: `EQUIBLES_API_KEY` was dropped from `cloud/config/required-env.txt`, the
deploy preflight stopped requiring it, and all five `radon-equibles-*` oneshots
died on `EquiblesAuthError` at every fire — 13F and filing-forensics
permanently empty for every ticker. The only guard was a per-key assertion
added after the fact, so the NEXT key repeats the incident with zero signal.

This is the code->contract direction, shaped like
`scripts/tests/test_watchdog_catalog_parity.py`: enumerate every
`cloud/services/*.service` ExecStart chain, collect the env names its job reads
with no fallback, and require each to be either a required key or a written,
reasoned exemption. Adding a key with neither fails here rather than in
production.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

CLOUD = Path(__file__).resolve().parents[1]
REPO = CLOUD.parent
SERVICES_DIR = CLOUD / "services"
SCRIPTS_DIR = REPO / "scripts"
CONTRACT = CLOUD / "config" / "required-env.txt"

# Names a unit reads with no fallback that deliberately do NOT belong in the
# deploy contract. Each needs a stated reason, so an exemption is a decision
# rather than an oversight. Adding a key here is the explicit alternative to
# adding it to required-env.txt.
EXEMPT: dict[str, str] = {
    # Set by the unit itself, not by the env file: the fleet drop-in
    # radon-.service.d/common.conf and per-unit `Environment=` lines own these.
    "RADON_DB_NO_REPLICA": "set by cloud/services/radon-.service.d/common.conf",
    "RADON_DB_USE_REPLICA": "explicit replica opt-in; unset is the production state (DUR-07)",
    "CREDENTIALS_DIRECTORY": "injected by systemd from LoadCredentialEncrypted; never stored in /etc/radon/env",
    "RADON_SECRET_STORE_PATH": "optional host override; container runtime pins the durable data-volume path",
    "RADON_SECRET_STORE_KEY_FILE": "optional development/rollback fallback; production uses the systemd credential",
    # Test-pollution guards. Their ABSENCE is the production state; setting
    # either in the deploy env would disarm the guard.
    "PYTEST_CURRENT_TEST": "set by pytest; production must never define it",
    "RADON_DB_TEST_WRITE_OK": "test-only override for the db pollution guard",
    "RADON_ALLOW_DB_IN_TESTS": "test-only override for the demo-scan DB guard",
    # Optional feature keys: the job degrades in a defined, logged way without
    # them, so requiring them would fail the preflight on a value nothing needs.
    "FRED_API_KEY": "optional; margin-debt normalization views go null and say so",
    "FRED_KEY": "legacy alias tried after FRED_API_KEY",
    "ROBINHOOD_MCP_TOKEN": "optional read-only failover; unset (with no refresh) skips Robinhood cleanly to Yahoo",
    "ROBINHOOD_MCP_REFRESH_TOKEN": "bootstrap only; the 0600 token file is the writable refresh store and owns the rotated value",
    "ROBINHOOD_MCP_CLIENT_ID": "bootstrap only; public OAuth client_id (auth method none), no secret exists",
    "ROBINHOOD_MCP_TOKEN_FILE": "override for DEFAULT_TOKEN_FILE in clients/robinhood_client.py; points at the writable 0600 refresh store (/etc/radon/rh-mcp.json in production)",
    "ROBINHOOD_MCP_URL": "override for DEFAULT_MCP_URL in clients/robinhood_client.py",
    "MDW_API_KEY": "optional X-API-Key lane; unset means no service principal",
    "RADON_SERVICE_TOKEN": "never configured on prod; unset makes the lane a no-op",
    "MENTHORQ_ARTIFACT_DIR": "debug artifact dump; unset means no artifacts",
    "RADON_CTA_SYNC_SOURCE": "argparse default for --source; the CLI flag is the contract",
    # Feature flags whose unset state is the safe one.
    "RADON_AUTH_DISABLED": "local-dev escape hatch; unset is the production state",
    # TURSO_DEMO_DB_URL / TURSO_DEMO_AUTH_TOKEN are deliberately NOT exempt.
    # R-300 moved them into cloud/config/required-env.txt: radon-demo-mirror's
    # ExecStartPre runs `migrate.py --demo`, whose resolve_target() exits 2 when
    # either is unset, so the unit fails on every fire rather than skipping.
    # Override knobs with an in-code default URL/binary.
    "CBOE_DAILY_PRICES_BASE_URL": "override for _DEFAULT_BASE_URL in clients/cboe_client.py",
    "FINRA_MARGIN_XLSX_URL": "override for _DEFAULT_XLSX_URL in clients/finra_client.py",
    "GROK_BIN": "override for the `grok` binary name on PATH",
    "IB_FLEX_FLOWS_QUERY_ID": "deliberately unset; _flows_query_id() falls back to the NAV id",
}


def contract_keys() -> set[str]:
    """The contract as `cloud/scripts/check-env.py:required_keys()` parses it."""
    return {
        line.strip()
        for line in CONTRACT.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _resolve(rel: str) -> Path | None:
    candidate = REPO / rel.lstrip("/").replace("home/radon/radon/", "")
    if candidate.exists():
        return candidate
    candidate = SCRIPTS_DIR / Path(rel).name
    return candidate if candidate.exists() else None


def _module_path(dotted: str) -> Path | None:
    parts = dotted.split(".")
    module = SCRIPTS_DIR.joinpath(*parts).with_suffix(".py")
    if module.exists():
        return module
    package = SCRIPTS_DIR.joinpath(*parts) / "__init__.py"
    return package if package.exists() else None


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None


def _undefaulted_env_names(path: Path) -> set[str]:
    """Env names the module reads with no fallback value.

    `os.environ["X"]` raises; `os.environ.get("X")` / `os.getenv("X")` with no
    second argument yields None. Both mean the code has nothing to fall back
    on. A read that supplies a default is the caller's own contract and is not
    a deploy-time requirement.
    """
    tree = _parse(path)
    if tree is None:
        return set()
    names: set[str] = set()

    def _literal(node: ast.AST) -> str | None:
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            target = node.value
            if isinstance(target, ast.Attribute) and target.attr == "environ":
                key = _literal(node.slice)
                if key:
                    names.add(key)
            continue
        if not isinstance(node, ast.Call) or node.keywords or len(node.args) != 1:
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr == "getenv":
            key = _literal(node.args[0])
            if key:
                names.add(key)
        elif func.attr == "get":
            owner = func.value
            is_environ = (
                isinstance(owner, ast.Attribute) and owner.attr == "environ"
            ) or (isinstance(owner, ast.Name) and owner.id == "environ")
            key = _literal(node.args[0]) if is_environ else None
            if key:
                names.add(key)
    return names


def _local_imports(path: Path) -> set[Path]:
    """First-level in-repo imports. The key that caused T-163 is read inside
    `clients/equibles_client.py`, not in the ExecStart module itself."""
    tree = _parse(path)
    if tree is None:
        return set()
    dotted: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            dotted.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            dotted.add(node.module)
    return {p for p in (_module_path(name) for name in dotted) if p is not None}


def _job_modules(unit: Path) -> set[Path]:
    """Python modules the unit's ExecStart chain runs.

    A `.sh` ExecStart is a wrapper, so the python it invokes is the real job
    and is followed one level — same rule as the watchdog catalog parity test.
    """
    exec_start = "\n".join(
        line for line in unit.read_text(encoding="utf-8").splitlines()
        if line.startswith("ExecStart")
    )
    seeds: set[Path] = set()
    for rel in re.findall(r"([A-Za-z0-9_./-]+\.(?:py|sh))", exec_start):
        resolved = _resolve(rel)
        if resolved is not None:
            seeds.add(resolved)
    for seed in list(seeds):
        if seed.suffix != ".sh":
            continue
        body = seed.read_text(encoding="utf-8", errors="replace")
        for inner in re.findall(r"(scripts/[A-Za-z0-9_./-]+\.py)", body):
            resolved = _resolve(inner)
            if resolved is not None:
                seeds.add(resolved)
    return {seed for seed in seeds if seed.suffix == ".py"}


def _required_env_by_unit() -> dict[str, set[str]]:
    """env name -> the units whose ExecStart chain reads it with no fallback."""
    out: dict[str, set[str]] = {}
    for unit in sorted(SERVICES_DIR.glob("*.service")):
        names: set[str] = set()
        for module in _job_modules(unit):
            names |= _undefaulted_env_names(module)
            for imported in _local_imports(module):
                names |= _undefaulted_env_names(imported)
        for name in names:
            out.setdefault(name, set()).add(unit.stem)
    return out


class TestTheEnumerationWorks:
    def test_it_reaches_the_key_that_caused_the_incident(self):
        found = _required_env_by_unit()
        assert "EQUIBLES_API_KEY" in found, (
            "the ExecStart walk no longer reaches clients/equibles_client.py, "
            "so this parity test guards nothing"
        )
        assert any(
            unit.startswith("radon-equibles-") for unit in found["EQUIBLES_API_KEY"]
        ), sorted(found["EQUIBLES_API_KEY"])

    def test_it_reaches_a_wrapper_backed_unit(self):
        assert _job_modules(SERVICES_DIR / "radon-flow-refresh.service"), (
            "a `.sh` ExecStart resolved to no python job; the wrapper hop broke"
        )


class TestCodeContractParity:
    def test_every_undefaulted_env_read_is_required_or_exempt(self):
        contract = contract_keys()
        gaps = {
            name: sorted(units)
            for name, units in sorted(_required_env_by_unit().items())
            if name not in contract and name not in EXEMPT
        }
        assert not gaps, (
            "env vars a scheduled unit reads with no fallback, absent from "
            "cloud/config/required-env.txt and with no reasoned EXEMPT entry:\n  "
            + "\n  ".join(f"{name}: {units}" for name, units in gaps.items())
        )

    def test_no_exemption_contradicts_the_contract(self):
        overlap = sorted(EXEMPT.keys() & contract_keys())
        assert not overlap, (
            f"exempted keys that the deploy preflight also requires: {overlap}"
        )

    def test_every_exemption_states_a_reason(self):
        blank = sorted(name for name, reason in EXEMPT.items() if not reason.strip())
        assert not blank, f"exemptions with no stated reason: {blank}"

"""DS-2026-10-28-02: every `pip install` in a docker/ Dockerfile verifies hashes.

The base image's pip vendors a certifi that still trusts the eMudhra/emSign
intermediates (keys recovered 2026-10), so the bootstrap that replaces pip is
exactly the download a forged intermediate could swap. Version floors do not
stop that; pinned sha256 digests do. Every install must run with
`--require-hashes --no-deps` from requirement files whose every line is an
`==` pin carrying a `--hash=sha256:` digest.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOCKER = REPO / "docker"
BOOTSTRAP = DOCKER / "app" / "requirements-bootstrap.txt"
REQUIREMENTS = REPO / "requirements.txt"

# The application dependency install still resolves transitive packages
# without a hash lock. Listed by exact argument vector so any NEW unhashed
# install fails; removing this exemption needs a generated hash lock.
UNHASHED_APP_INSTALLS = {
    ("-r", "requirements.txt", "-r", "scripts/requirements-api.txt"),
}

# pip options whose next token is a value, not a requirement.
VALUE_OPTIONS = {
    "-r", "--requirement", "-c", "--constraint", "-i", "--index-url",
    "--extra-index-url", "-f", "--find-links", "--only-binary", "--no-binary",
}
HASH_RE = re.compile(r"--hash=sha256:[0-9a-f]{64}\b")
PIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?==[A-Za-z0-9.!+-]+$")


def _dockerfiles() -> list[Path]:
    return sorted(p for p in DOCKER.rglob("Dockerfile*") if p.is_file())


def _logical_lines(text: str) -> list[str]:
    return re.sub(r"\\\n", " ", text).splitlines()


def _pip_installs(text: str) -> list[list[str]]:
    """Each `pip install` invocation's argument vector, split at shell operators."""
    found: list[list[str]] = []
    for line in _logical_lines(text):
        if not line.lstrip().startswith("RUN ") or "pip install" not in line:
            continue
        for segment in re.split(r"&&|\|\||;", line):
            tokens = shlex.split(segment)
            for i in range(len(tokens) - 1):
                if tokens[i] == "pip" and tokens[i + 1] == "install":
                    found.append(tokens[i + 2 :])
                    break
    return found


def _requirement_files(args: list[str]) -> list[str]:
    files = []
    for i, arg in enumerate(args):
        if arg in ("-r", "--requirement"):
            files.append(args[i + 1])
        elif arg.startswith("--requirement="):
            files.append(arg.split("=", 1)[1])
    return files


def _copied_source(text: str, dest: str) -> Path | None:
    """Map an in-image requirement path back to the repo file COPY'd there."""
    for line in _logical_lines(text):
        parts = line.split()
        if len(parts) == 3 and parts[0] == "COPY" and not parts[1].startswith("--"):
            src, target = parts[1], parts[2]
            if target.rstrip("/").removeprefix("./") == dest.removeprefix("./"):
                return REPO / src
    return None


def _requirement_entries(path: Path) -> list[str]:
    text = re.sub(r"\\\n", " ", path.read_text(encoding="utf-8"))
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def unhashed_problems(text: str) -> list[str]:
    problems: list[str] = []
    for args in _pip_installs(text):
        if tuple(a for a in args if not a.startswith("--no-cache")) in UNHASHED_APP_INSTALLS:
            continue
        shown = " ".join(args)
        if "--require-hashes" not in args:
            problems.append(f"missing --require-hashes: pip install {shown}")
        if "--no-deps" not in args:
            problems.append(f"missing --no-deps: pip install {shown}")
        files = _requirement_files(args)
        positional = [
            a
            for i, a in enumerate(args)
            if not a.startswith("-") and (i == 0 or args[i - 1] not in VALUE_OPTIONS)
        ]
        if positional:
            problems.append(f"package named on the command line (no hash possible): {positional}")
        if not files:
            problems.append(f"no hashed requirement file: pip install {shown}")
        for name in files:
            source = _copied_source(text, name)
            if source is None or not source.is_file():
                problems.append(f"requirement file {name} is not COPY'd from the repo")
                continue
            for entry in _requirement_entries(source):
                spec = entry.split()[0]
                if not PIN_RE.fullmatch(spec):
                    problems.append(f"{source.name}: not an exact == pin: {spec}")
                if not HASH_RE.search(entry):
                    problems.append(f"{source.name}: no sha256 hash for {spec}")
    return problems


@pytest.mark.parametrize("dockerfile", _dockerfiles(), ids=lambda p: str(p.relative_to(REPO)))
def test_every_pip_install_in_docker_requires_hashes(dockerfile: Path) -> None:
    problems = unhashed_problems(dockerfile.read_text(encoding="utf-8"))
    assert not problems, f"{dockerfile.relative_to(REPO)}: " + "; ".join(problems)


def test_scanner_finds_the_python_image() -> None:
    assert DOCKER / "app" / "Dockerfile.python" in _dockerfiles()


@pytest.mark.parametrize(
    "run",
    [
        'RUN python -m pip install --no-cache-dir --upgrade "pip>=26.2.2" "certifi>=2026.10.19"',
        "RUN pip install requests==2.33.0",
        'RUN python -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch==2.12.1+cpu"',
        "RUN apt-get update && pip install --require-hashes requests==2.33.0",
        "RUN python -m pip install --require-hashes -r /tmp/missing.txt",
    ],
)
def test_scanner_rejects_unhashed_installs(run: str) -> None:
    assert unhashed_problems(run + "\n")


def test_scanner_accepts_a_hashed_bootstrap() -> None:
    text = (
        "COPY docker/app/requirements-bootstrap.txt ./requirements-bootstrap.txt\n"
        "RUN python -m pip install --no-cache-dir --require-hashes --no-deps \\\n"
        "    --only-binary=:all: -r requirements-bootstrap.txt\n"
    )
    assert unhashed_problems(text) == []


def test_bootstrap_pins_the_trust_store_releases_with_digests() -> None:
    entries = {e.split()[0]: e for e in _requirement_entries(BOOTSTRAP)}
    assert set(entries) == {"pip==26.2.2", "certifi==2026.10.19"}
    for entry in entries.values():
        assert len(HASH_RE.findall(entry)) == 1, entry


def test_bootstrap_certifi_matches_requirements_pin() -> None:
    pinned = re.search(r"^certifi==(\S+)$", REQUIREMENTS.read_text(encoding="utf-8"), re.M)
    assert pinned
    assert f"certifi=={pinned.group(1)}" in {e.split()[0] for e in _requirement_entries(BOOTSTRAP)}

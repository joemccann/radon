#!/usr/bin/env python3
"""Hash the files Dockerfile.python can copy into the image.

A non-zero exit is fail-closed: the workflow builds instead of retagging.
Unknown COPY forms and unknown dockerignore patterns both refuse. The hash
covers the Dockerfile and the dockerignore bytes, so an edit to either
misses the identical-image tag and takes the normal build path.
"""

from __future__ import annotations

import hashlib
import stat
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO / "docker" / "app" / "Dockerfile.python"
DOCKERIGNORE = REPO / "docker" / "app" / ".dockerignore"

# Every pattern in docker/app/.dockerignore must be listed here. A new
# pattern fails the hash (and the contract test) until the matcher learns it.
IMPLEMENTED = (
    ".git",
    "data/replica.db",
    "**/.env*",
    "!**/.env.example",
    "!**/.env.local.example",
    "cloud/",
    "docker/ib-gateway",
    "**/.venv",
    "**/node_modules",
    "**/.next",
    "scripts/tests/",
    "scripts/api/tests/",
    "web/tests/",
    "web/e2e/",
    "**/__pycache__",
    "**/*.pyc",
)


class InputHashError(RuntimeError):
    """The image inputs cannot be proven identical. Callers must build."""


def _patterns(path: Path) -> list[str]:
    found: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line not in IMPLEMENTED:
            raise InputHashError(f"dockerignore pattern is not understood: {line}")
        found.append(line)
    return found


def _matches(body: str, path: str) -> bool:
    if body in {".git", "data/replica.db", "docker/ib-gateway"}:
        return path == body or path.startswith(body + "/")
    if body.endswith("/"):
        stem = body[:-1]
        return path == stem or path.startswith(body)
    if body == "**/.env*":
        return Path(path).name.startswith(".env")
    if body in {"!**/.env.example", "!**/.env.local.example"}:
        # Negation bodies are matched by the caller via the raw pattern.
        raise InputHashError(f"negation passed to matcher: {body}")
    if body in {"**/.venv", "**/node_modules", "**/.next"}:
        name = body.split("/")[-1]
        return name in path.split("/")
    if body == "**/__pycache__":
        return "__pycache__" in path.split("/")
    if body == "**/*.pyc":
        return Path(path).name.endswith(".pyc")
    raise InputHashError(f"dockerignore pattern is not understood: {body}")


def path_ignored(path: str, patterns: list[str]) -> bool:
    ignored = False
    for raw in patterns:
        negation = raw.startswith("!")
        body = raw[1:] if negation else raw
        if negation:
            name = {
                "!**/.env.example": ".env.example",
                "!**/.env.local.example": ".env.local.example",
            }.get(raw)
            if name is None:
                raise InputHashError(f"dockerignore pattern is not understood: {raw}")
            matched = Path(path).name == name
        else:
            matched = _matches(body, path)
        if matched:
            ignored = not negation
    return ignored


def copy_sources(dockerfile_text: str) -> list[str]:
    sources: list[str] = []
    stages: set[str] = set()
    for raw in dockerfile_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        instruction = parts[0].upper()
        if instruction == "FROM":
            if len(parts) >= 4 and parts[-2].upper() == "AS":
                stages.add(parts[-1])
            continue
        if instruction == "ADD":
            raise InputHashError(f"ADD cannot be hashed: {line}")
        if instruction != "COPY":
            continue
        if not line.startswith("COPY "):
            raise InputHashError(f"COPY cannot be hashed: {line}")
        if "--from=" in line:
            origin = [part.removeprefix("--from=") for part in parts if part.startswith("--from=")]
            if len(origin) != 1 or origin[0] not in stages:
                raise InputHashError(f"COPY stage cannot be hashed: {line}")
            continue
        if any(token in line for token in ("--chown", "--chmod", "--link", "$", "*", "?", "[")):
            raise InputHashError(f"COPY cannot be hashed: {line}")
        if len(parts) < 3 or any(part.startswith("-") for part in parts[1:]):
            raise InputHashError(f"COPY cannot be hashed: {line}")
        sources.extend(parts[1:-1])
    if not sources:
        raise InputHashError("Dockerfile has no hashable COPY sources")
    return sources


def _collect(repo: Path, source: str, patterns: list[str]) -> dict[str, Path]:
    path = repo / source
    if path.is_symlink():
        raise InputHashError(f"symlink in image inputs: {path}")
    if not path.exists():
        raise InputHashError(f"missing COPY source: {source}")
    found: dict[str, Path] = {}
    # Inspect directories too: rglob does not descend into directory links,
    # so filtering to is_file first silently dropped COPY input links.
    files = [path] if path.is_file() else [path, *sorted(path.rglob("*"))]
    for file in files:
        relative = file.relative_to(repo).as_posix()
        if path_ignored(relative, patterns):
            continue
        if file.is_symlink():
            raise InputHashError(f"symlink in image inputs: {file}")
        if file.is_dir():
            # Git does not track directory modes. An unusual checkout mode
            # cannot be represented by the file digest: build conservatively.
            if stat.S_IMODE(file.stat().st_mode) != 0o755:
                raise InputHashError(f"nonstandard directory mode in image inputs: {file}")
            continue
        if not file.is_file():
            raise InputHashError(f"unsupported file type in image inputs: {file}")
        found[relative] = file
    return found


def input_files_from(dockerfile: Path, dockerignore: Path, repo: Path) -> list[str]:
    patterns = _patterns(dockerignore)
    found: dict[str, Path] = {}
    for source in copy_sources(dockerfile.read_text(encoding="utf-8")):
        found.update(_collect(repo, source, patterns))
    for extra in (dockerfile, dockerignore):
        found[extra.relative_to(repo).as_posix()] = extra
    return sorted(found)


def input_files(repo: Path) -> list[str]:
    return input_files_from(
        repo / "docker" / "app" / "Dockerfile.python",
        repo / "docker" / "app" / ".dockerignore",
        repo,
    )


def hash_files(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        data = files[path]
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def image_input_hash(repo: Path = REPO) -> str:
    paths = input_files(repo)
    # Docker COPY preserves file permissions, so unchanged bytes alone do
    # not prove the same image. Fixed-width mode bytes disambiguate metadata.
    blob = {
        path: stat.S_IMODE((repo / path).stat().st_mode).to_bytes(4, "big")
        + (repo / path).read_bytes()
        for path in paths
    }
    return hash_files(blob)


def main() -> int:
    try:
        print(image_input_hash())
    except InputHashError as exc:
        print(f"python image inputs: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

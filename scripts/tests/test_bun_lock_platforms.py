"""Every bun lockfile carries the macOS (arm64) native builds next to Linux.

`web/bun.lock` was last regenerated on Linux (df890f16) and kept only the
linux-x64 optional builds of rollup, esbuild and unrs-resolver. A frozen
install on the Mac mini then had no `@rollup/rollup-darwin-arm64`, so Vitest
could not start there and the nightly security loop's Vitest stage was
skipped. CI (Linux) never noticed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LOCKS = [REPO / "bun.lock", REPO / "web" / "bun.lock"]
ENTRY = re.compile(r'^    "(?P<name>[^"]+)": \["(?P=name)@(?P<ver>[^"]+)", "[^"]*", \{(?P<meta>.*?)\}, "sha512-', re.M)
# The native binary packages whose per-platform builds this repo installs.
LINUX_X64 = re.compile(r"^(?P<stem>@rollup/rollup|@esbuild|@unrs/resolver-binding)[-/]linux-x64(?:-gnu)?$")


def _entries(text: str) -> dict[str, tuple[str, str]]:
    return {m["name"]: (m["ver"], m["meta"]) for m in ENTRY.finditer(text)}


def _darwin_arm64_name(stem: str) -> str:
    return f"{stem}/darwin-arm64" if stem == "@esbuild" else f"{stem}-darwin-arm64"


def missing_darwin_builds(text: str) -> list[str]:
    entries = _entries(text)
    missing = []
    for name, (ver, _meta) in entries.items():
        m = LINUX_X64.match(name)
        if not m:
            continue
        twin = _darwin_arm64_name(m["stem"])
        got = entries.get(twin)
        if not got or got[0] != ver or '"os": "darwin"' not in got[1] or '"cpu": "arm64"' not in got[1]:
            missing.append(f"{twin}@{ver}")
    return sorted(missing)


@pytest.mark.parametrize("lock", LOCKS, ids=lambda p: str(p.relative_to(REPO)))
def test_every_linux_native_build_has_its_darwin_arm64_twin(lock: Path) -> None:
    assert missing_darwin_builds(lock.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("parent,child", [
    ("rollup", "@rollup/rollup-darwin-arm64"),
    ("esbuild", "@esbuild/darwin-arm64"),
    ("unrs-resolver", "@unrs/resolver-binding-darwin-arm64"),
])
def test_parents_list_the_darwin_build_as_optional(parent: str, child: str) -> None:
    text = (REPO / "web" / "bun.lock").read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if l.startswith(f'    "{parent}": ['))
    assert f'"{child}": ' in line.split('"optionalDependencies"', 1)[1]


def test_a_linux_only_lock_is_caught() -> None:
    lock = (
        '    "@rollup/rollup-linux-x64-gnu": ["@rollup/rollup-linux-x64-gnu@4.59.0", "", '
        '{ "os": "linux", "cpu": "x64" }, "sha512-x"],\n'
    )
    assert missing_darwin_builds(lock) == ["@rollup/rollup-darwin-arm64@4.59.0"]

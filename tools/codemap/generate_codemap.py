"""Generate the whole-repo code-path graph.

Walks every source area (web/, site/, scripts/, cloud/, lib/, tests/, tools/),
resolves TS/JS and Python imports to repo-internal files, and emits:

- tools/codemap/codemap.json     -- the graph (nodes, edges, groups, layout)
- tools/codemap/codemap.data.js  -- same payload as `window.CODEMAP = ...` so
  tools/codemap/index.html works from file:// (Chrome blocks file:// fetch)

Layout is precomputed here (per-directory clusters on a ring, phyllotaxis
inside each cluster) so the WebGL viewer only renders and never simulates.

Usage: python3 tools/codemap/generate_codemap.py
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent

SOURCE_ROOTS = ("web", "site", "scripts", "cloud", "lib", "tests", "tools")

SKIP_DIRS = {
    "node_modules",
    "__pycache__",
    ".git",
    ".venv",
    ".next",
    ".next-build",
    ".next-dev-webpack",
    ".next-seo-audit",
    ".turbo",
    ".pytest_cache",
    ".ruff_cache",
    ".claude",
    ".grok",
    ".agents",
    "coverage",
    "playwright-report",
    "test-results",
    "dist",
    "build",
    "worktrees",
}

SKIP_FILES = {"codemap.data.js"}

JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
PY_EXTS = (".py",)

JS_SPEC_RES = (
    re.compile(r"""\bimport\s+(?:type\s+)?[\w${}*,\s]*?\bfrom\s*["']([^"']+)["']"""),
    re.compile(r"""\bimport\s*["']([^"']+)["']"""),
    re.compile(r"""\bexport\s+(?:type\s+)?[\w${}*,\s]*?\bfrom\s*["']([^"']+)["']"""),
    re.compile(r"""\brequire\(\s*["']([^"']+)["']\s*\)"""),
    re.compile(r"""\bimport\(\s*["']([^"']+)["']\s*\)"""),
)

PY_IMPORT_RE = re.compile(r"^\s*import\s+([\w.]+(?:\s+as\s+\w+)?(?:\s*,\s*[\w.]+(?:\s+as\s+\w+)?)*)", re.M)
PY_FROM_RE = re.compile(r"^\s*from\s+([.\w]+)\s+import\s+([\w.*,\s()]+)", re.M)

TEST_SEGMENTS = {"tests", "e2e", "__tests__", "fuzz"}
TEST_FILE_RE = re.compile(r"(?:^test_.+\.py$|.+\.(?:test|spec)\.[jt]sx?$|^conftest\.py$)")


def is_test_path(rel: str) -> bool:
    parts = rel.split("/")
    if rel.startswith("tests/"):
        return True
    if any(seg in TEST_SEGMENTS for seg in parts[:-1]):
        return True
    return bool(TEST_FILE_RE.match(parts[-1]))


def collect_files(repo: Path) -> list[str]:
    files: list[str] = []
    for root in SOURCE_ROOTS:
        base = repo / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if path.name in SKIP_FILES:
                continue
            if path.suffix not in JS_EXTS and path.suffix not in PY_EXTS:
                continue
            rel_parts = path.relative_to(repo).parts
            if any(part in SKIP_DIRS for part in rel_parts):
                continue
            files.append("/".join(rel_parts))
    return files


def area_of(rel: str) -> str:
    return rel.split("/", 1)[0]


def group_of(rel: str) -> str:
    parts = rel.split("/")
    return "/".join(parts[:2]) if len(parts) > 2 else parts[0]


def js_package_root(rel: str) -> str | None:
    top = area_of(rel)
    return top if top in ("web", "site") else None


def _js_candidates(base: Path) -> list[Path]:
    cands = [base] if base.suffix in JS_EXTS else []
    if base.suffix in (".js", ".mjs", ".cjs"):
        cands += [base.with_suffix(ext) for ext in (".ts", ".tsx")]
    cands += [base.with_name(base.name + ext) for ext in JS_EXTS]
    cands += [base / f"index{ext}" for ext in JS_EXTS]
    return cands


def resolve_js(spec: str, rel: str, repo: Path, index: set) -> str | None:
    """Resolve one import specifier to a repo-relative path, or None if external."""
    if spec.startswith("."):
        base = repo / Path(rel).parent / spec
    elif spec.startswith("@tools/"):
        base = repo / "lib" / "tools" / spec[len("@tools/") :]
    elif spec.startswith("@/"):
        pkg = js_package_root(rel)
        if pkg is None:
            return None
        base = repo / pkg / spec[2:]
    else:
        return None
    try:
        base = base.resolve()
        base.relative_to(repo)
    except (OSError, ValueError):
        return None
    for cand in _js_candidates(base):
        cand_rel = "/".join(cand.relative_to(repo).parts)
        if cand_rel in index:
            return cand_rel
    return None


def py_roots(rel: str) -> list[str]:
    """Import roots mirroring pyproject pythonpath + subsystem sys.path habits."""
    top = area_of(rel)
    if top == "scripts":
        return ["scripts", "scripts/trade_blotter", ""]
    if top == "cloud":
        return ["cloud", "", "scripts"]
    return ["", "scripts", "scripts/trade_blotter"]


def _py_module_candidates(root: str, module: str) -> list[str]:
    slug = module.replace(".", "/")
    prefix = f"{root}/" if root else ""
    return [f"{prefix}{slug}.py", f"{prefix}{slug}/__init__.py"]


def resolve_py_module(module: str, rel: str, index: set) -> str | None:
    for root in py_roots(rel):
        for cand in _py_module_candidates(root, module):
            if cand in index:
                return cand
    return None


def resolve_py_relative(dots: int, module: str, rel: str, index: set) -> str | None:
    base_parts = rel.split("/")[:-1]
    up = dots - 1
    if up > len(base_parts):
        return None
    pkg_parts = base_parts[: len(base_parts) - up]
    slug = "/".join(pkg_parts + module.split(".")) if module else "/".join(pkg_parts)
    for cand in (f"{slug}.py", f"{slug}/__init__.py"):
        if cand in index:
            return cand
    return None


def extract_js_deps(text: str, rel: str, repo: Path, index: set, externals: Counter) -> set:
    deps = set()
    for pattern in JS_SPEC_RES:
        for spec in pattern.findall(text):
            target = resolve_js(spec, rel, repo, index)
            if target and target != rel:
                deps.add(target)
            elif target is None and not spec.startswith("."):
                pkg = spec.split("/")[0] if not spec.startswith("@") else "/".join(spec.split("/")[:2])
                externals[pkg] += 1
    return deps


def extract_py_deps(text: str, rel: str, index: set, externals: Counter) -> set:
    deps = set()
    for clause in PY_IMPORT_RE.findall(text):
        for piece in clause.split(","):
            module = piece.strip().split(" as ")[0].strip()
            if not module:
                continue
            target = resolve_py_module(module, rel, index)
            if target and target != rel:
                deps.add(target)
            elif target is None:
                externals[module.split(".")[0]] += 1
    for module, names in PY_FROM_RE.findall(text):
        dots = len(module) - len(module.lstrip("."))
        bare = module.lstrip(".")
        resolved_pkg = resolve_py_relative(dots, bare, rel, index) if dots else resolve_py_module(bare, rel, index)
        if resolved_pkg and resolved_pkg != rel:
            deps.add(resolved_pkg)
        if resolved_pkg is None and dots == 0:
            externals[bare.split(".")[0]] += 1
        for raw in names.replace("(", "").replace(")", "").split(","):
            name = raw.strip().split(" as ")[0].strip()
            if not name or name == "*" or not name.isidentifier():
                continue
            child = f"{bare}.{name}" if bare else name
            target = resolve_py_relative(dots, child, rel, index) if dots else resolve_py_module(child, rel, index)
            if target and target != rel:
                deps.add(target)
    return deps


GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))
NODE_SPACING = 9.0


def layout(nodes: list[dict], groups: list[dict]) -> None:
    """Ring of directory clusters (contiguous by area), phyllotaxis inside each."""
    for grp in groups:
        grp["r"] = max(NODE_SPACING, NODE_SPACING * math.sqrt(grp["count"]) * 1.15)
    total_span = sum(2.0 * g["r"] * 1.25 for g in groups)
    ring_r = max(200.0, total_span / (2.0 * math.pi))
    angle = 0.0
    for grp in groups:
        half = (2.0 * grp["r"] * 1.25) / total_span * 2.0 * math.pi / 2.0
        angle += half
        grp["x"] = round(math.cos(angle) * (ring_r + grp["r"] * 0.35), 2)
        grp["y"] = round(math.sin(angle) * (ring_r + grp["r"] * 0.35), 2)
        angle += half
    counters = [0] * len(groups)
    for node in nodes:
        grp = groups[node["g"]]
        i = counters[node["g"]]
        counters[node["g"]] += 1
        rr = NODE_SPACING * math.sqrt(i + 0.5)
        theta = i * GOLDEN_ANGLE
        node["x"] = round(grp["x"] + rr * math.cos(theta), 2)
        node["y"] = round(grp["y"] + rr * math.sin(theta), 2)


def build_graph(repo: Path) -> dict:
    rels = collect_files(repo)
    index = set(rels)
    externals: Counter = Counter()

    group_ids: dict[str, int] = {}
    groups: list[dict] = []
    nodes: list[dict] = []
    node_ids: dict[str, int] = {}
    for rel in rels:
        gkey = group_of(rel)
        if gkey not in group_ids:
            group_ids[gkey] = len(groups)
            groups.append({"id": gkey, "area": area_of(rel), "count": 0})
        groups[group_ids[gkey]]["count"] += 1
        node_ids[rel] = len(nodes)
        nodes.append(
            {
                "id": rel,
                "g": group_ids[gkey],
                "lang": Path(rel).suffix.lstrip("."),
                "loc": 0,
                "test": is_test_path(rel),
                "in": 0,
                "out": 0,
            }
        )

    edges: set = set()
    for rel in rels:
        try:
            text = (repo / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        nodes[node_ids[rel]]["loc"] = text.count("\n") + 1
        if rel.endswith(PY_EXTS):
            deps = extract_py_deps(text, rel, index, externals)
        else:
            deps = extract_js_deps(text, rel, repo, index, externals)
        for dep in deps:
            edges.add((node_ids[rel], node_ids[dep]))

    for src, dst in edges:
        nodes[src]["out"] += 1
        nodes[dst]["in"] += 1

    layout(nodes, groups)

    area_counts = Counter(g["area"] for g in groups for _ in range(g["count"]))
    return {
        "meta": {
            "repo": "radon",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "areas": dict(area_counts),
            "external_top": externals.most_common(25),
        },
        "groups": groups,
        "nodes": nodes,
        "edges": sorted(edges),
    }


def main() -> int:
    graph = build_graph(REPO)
    payload = json.dumps(graph, separators=(",", ":"))
    (OUT_DIR / "codemap.json").write_text(payload + "\n", encoding="utf-8")
    (OUT_DIR / "codemap.data.js").write_text(f"window.CODEMAP = {payload};\n", encoding="utf-8")
    meta = graph["meta"]
    print(f"codemap: {meta['node_count']} nodes, {meta['edge_count']} edges -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

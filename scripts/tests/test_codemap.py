"""Tests for the whole-repo code-path map (tools/codemap)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from tools.codemap.generate_codemap import (
    REPO,
    build_graph,
    collect_files,
    extract_js_deps,
    extract_py_deps,
    group_of,
    is_test_path,
    resolve_js,
    resolve_py_module,
    resolve_py_relative,
)

VIEWER = Path(__file__).resolve().parents[2] / "tools" / "codemap" / "index.html"
VIEWER_JS = Path(__file__).resolve().parents[2] / "tools" / "codemap" / "viewer.js"


def _write(repo: Path, rel: str, text: str = "") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _write(tmp_path, "web/app/page.tsx", 'import { usePrices } from "@/lib/usePrices";\nimport "./globals.css";\n')
    _write(tmp_path, "web/lib/usePrices.ts", 'import { protoVersion } from "./proto";\nimport React from "react";\n')
    _write(tmp_path, "web/lib/proto.ts", "export const protoVersion = 1;\n")
    _write(tmp_path, "web/lib/order/index.ts", "export const order = 1;\n")
    _write(tmp_path, "web/components/Panel.tsx", 'export { order } from "@/lib/order";\nimport tool from "@tools/vcg";\n')
    _write(tmp_path, "web/components/dashboard/ClearOverview.tsx", "export const overview = 1;\n")
    _write(tmp_path, "web/tests/panel.test.tsx", 'import { order } from "@/lib/order";\n')
    _write(tmp_path, "lib/tools/vcg.ts", "export default 1;\n")
    _write(tmp_path, "site/app/page.tsx", 'import { seo } from "@/lib/seo";\n')
    _write(tmp_path, "site/lib/seo.ts", "export const seo = 1;\n")
    _write(tmp_path, "scripts/ib_sync.py", "import json\nfrom clients.ib_client import IBClient\n")
    _write(tmp_path, "scripts/clients/__init__.py", "")
    _write(tmp_path, "scripts/clients/ib_client.py", "from . import journal_basis\nimport utils.market_calendar\n")
    _write(tmp_path, "scripts/clients/journal_basis.py", "")
    _write(tmp_path, "scripts/utils/__init__.py", "")
    _write(tmp_path, "scripts/utils/market_calendar.py", "")
    _write(tmp_path, "scripts/tests/test_ib_sync.py", "import ib_sync\n")
    _write(tmp_path, "cloud/tests/test_deploy.py", "from scripts_lib import helper\n")
    _write(tmp_path, "cloud/scripts_lib/__init__.py", "")
    _write(tmp_path, "cloud/scripts_lib/helper.py", "")
    _write(tmp_path, "tests/test_money.py", "import math\n")
    _write(tmp_path, "tools/codemap/generate_codemap.py", "import json\n")
    _write(tmp_path, "tools/codemap/codemap.data.js", "window.CODEMAP = {};\n")
    _write(tmp_path, "web/node_modules/react/index.js", "ignored\n")
    _write(tmp_path, "web/.claude/worktrees/x.ts", "export const skipped = 1;\n")
    return tmp_path


def _index(repo: Path) -> set:
    return set(collect_files(repo))


class TestCollection:
    def test_skips_node_modules(self, repo: Path) -> None:
        files = collect_files(repo)
        assert "web/app/page.tsx" in files
        assert not any("node_modules" in f for f in files)

    def test_skips_agent_worktrees_and_generated_payload(self, repo: Path) -> None:
        files = collect_files(repo)
        assert "web/.claude/worktrees/x.ts" not in files
        assert "tools/codemap/codemap.data.js" not in files
        assert "tools/codemap/generate_codemap.py" in files

    def test_group_of(self) -> None:
        assert group_of("web/lib/order/index.ts") == "web/lib"
        assert group_of("scripts/ib_sync.py") == "scripts"
        assert group_of("tools/codemap/generate_codemap.py") == "tools/codemap"

    def test_is_test_path(self) -> None:
        assert is_test_path("web/tests/panel.test.tsx")
        assert is_test_path("scripts/tests/test_ib_sync.py")
        assert is_test_path("tests/test_money.py")
        assert is_test_path("web/e2e/margin.spec.ts")
        assert is_test_path("scripts/api/tests/conftest.py")
        assert not is_test_path("web/lib/usePrices.ts")
        assert not is_test_path("scripts/ib_sync.py")


class TestJsResolution:
    def test_relative(self, repo: Path) -> None:
        assert resolve_js("./proto", "web/lib/usePrices.ts", repo, _index(repo)) == "web/lib/proto.ts"

    def test_alias_web(self, repo: Path) -> None:
        assert resolve_js("@/lib/usePrices", "web/app/page.tsx", repo, _index(repo)) == "web/lib/usePrices.ts"

    def test_alias_site_scoped_to_site(self, repo: Path) -> None:
        assert resolve_js("@/lib/seo", "site/app/page.tsx", repo, _index(repo)) == "site/lib/seo.ts"
        assert resolve_js("@/lib/seo", "web/app/page.tsx", repo, _index(repo)) is None

    def test_tools_alias(self, repo: Path) -> None:
        assert resolve_js("@tools/vcg", "web/components/Panel.tsx", repo, _index(repo)) == "lib/tools/vcg.ts"

    def test_index_resolution(self, repo: Path) -> None:
        assert resolve_js("@/lib/order", "web/components/Panel.tsx", repo, _index(repo)) == "web/lib/order/index.ts"

    def test_external_is_none(self, repo: Path) -> None:
        assert resolve_js("react", "web/lib/usePrices.ts", repo, _index(repo)) is None

    def test_js_suffix_remaps_to_ts(self, repo: Path) -> None:
        assert resolve_js("./proto.js", "web/lib/usePrices.ts", repo, _index(repo)) == "web/lib/proto.ts"

    def test_extract_export_from_and_alias(self, repo: Path) -> None:
        text = (repo / "web/components/Panel.tsx").read_text()
        deps = extract_js_deps(text, "web/components/Panel.tsx", repo, _index(repo), Counter())
        assert deps == {"web/lib/order/index.ts", "lib/tools/vcg.ts"}


class TestPyResolution:
    def test_module_via_scripts_root(self, repo: Path) -> None:
        assert resolve_py_module("clients.ib_client", "scripts/ib_sync.py", _index(repo)) == (
            "scripts/clients/ib_client.py"
        )

    def test_package_init(self, repo: Path) -> None:
        assert resolve_py_module("clients", "scripts/ib_sync.py", _index(repo)) == "scripts/clients/__init__.py"

    def test_relative_from_dot(self, repo: Path) -> None:
        assert resolve_py_relative(1, "journal_basis", "scripts/clients/ib_client.py", _index(repo)) == (
            "scripts/clients/journal_basis.py"
        )

    def test_cloud_scoped_root(self, repo: Path) -> None:
        assert resolve_py_module("scripts_lib.helper", "cloud/tests/test_deploy.py", _index(repo)) == (
            "cloud/scripts_lib/helper.py"
        )

    def test_stdlib_is_unresolved(self, repo: Path) -> None:
        assert resolve_py_module("json", "scripts/ib_sync.py", _index(repo)) is None

    def test_extract_from_import_submodule(self, repo: Path) -> None:
        deps = extract_py_deps(
            "from clients import journal_basis\n", "scripts/ib_sync.py", _index(repo), Counter()
        )
        assert "scripts/clients/journal_basis.py" in deps
        assert "scripts/clients/__init__.py" in deps


class TestGraph:
    def test_build_graph_shape_and_edges(self, repo: Path) -> None:
        graph = build_graph(repo)
        ids = {n["id"]: i for i, n in enumerate(graph["nodes"])}
        edges = set(map(tuple, graph["edges"]))
        assert (ids["web/app/page.tsx"], ids["web/lib/usePrices.ts"]) in edges
        assert (ids["scripts/ib_sync.py"], ids["scripts/clients/ib_client.py"]) in edges
        assert (ids["scripts/clients/ib_client.py"], ids["scripts/utils/market_calendar.py"]) in edges
        assert graph["meta"]["node_count"] == len(graph["nodes"])
        assert graph["meta"]["edge_count"] == len(graph["edges"])
        assert "react" in dict(graph["meta"]["external_top"])
        assert "tools" in graph["meta"]["areas"]

    def test_degrees_and_test_flags(self, repo: Path) -> None:
        graph = build_graph(repo)
        by_id = {n["id"]: n for n in graph["nodes"]}
        assert by_id["web/lib/usePrices.ts"]["in"] == 1
        assert by_id["web/app/page.tsx"]["out"] == 1
        assert by_id["scripts/tests/test_ib_sync.py"]["test"] is True
        assert by_id["scripts/ib_sync.py"]["test"] is False

    def test_layout_finite_and_clustered(self, repo: Path) -> None:
        graph = build_graph(repo)
        for node in graph["nodes"]:
            assert isinstance(node["x"], float) and isinstance(node["y"], float)
        for grp in graph["groups"]:
            assert grp["r"] > 0
        for node in graph["nodes"]:
            grp = graph["groups"][node["g"]]
            dist = ((node["x"] - grp["x"]) ** 2 + (node["y"] - grp["y"]) ** 2) ** 0.5
            assert dist <= grp["r"] * 2.5

    def test_json_serializable(self, repo: Path) -> None:
        graph = build_graph(repo)
        payload = json.dumps(graph, separators=(",", ":"))
        assert json.loads(payload)["meta"]["node_count"] == graph["meta"]["node_count"]

    def test_no_self_edges(self, repo: Path) -> None:
        graph = build_graph(repo)
        assert all(src != dst for src, dst in graph["edges"])


class TestLiveRepo:
    def test_current_workstation_surfaces_are_in_the_graph(self) -> None:
        graph = build_graph(REPO)
        ids = {n["id"] for n in graph["nodes"]}
        assert "web/components/dashboard/ClearOverview.tsx" in ids
        assert "web/components/ClearBrandMark.tsx" in ids
        assert "web/app/clear.css" not in ids
        assert "scripts/api/server.py" in ids
        assert graph["meta"]["node_count"] > 2817
        assert graph["meta"]["edge_count"] > 5502
        assert set(graph["meta"]["areas"]) >= {"web", "scripts", "site", "cloud", "lib", "tests", "tools"}


class TestViewerChrome:
    def test_clear_light_shell_and_controls(self) -> None:
        html = VIEWER.read_text(encoding="utf-8")
        js = VIEWER_JS.read_text(encoding="utf-8")
        assert 'data-theme="light"' in html
        assert "--bg-canvas: #ffffff" in html
        assert "--radius-lg: 10px" in html
        assert "#0a0f14" not in html
        assert "#05AD98" not in html
        assert 'id="theme-toggle"' in html
        assert 'id="search"' in html
        assert 'id="gl"' in html
        assert "Code path" in html
        assert "radon" in html
        assert "getBoundingClientRect" in js
        assert "codemap.data.js" in html
        assert "localStorage" in js
        assert "filters-extra" in html
        assert "compact" in js
        assert "—" not in html
        assert "–" not in html

"""Path-filter classification for CI test gates."""

from __future__ import annotations

from pathlib import Path

from ci.path_filter import classify, write_output


def test_web_only_skips_python() -> None:
    python, web = classify(["web/lib/foo.ts", "web/tests/bar.test.tsx"])
    assert (python, web) == (False, True)


def test_scripts_only_skips_web() -> None:
    python, web = classify(["scripts/ib_sync.py", "cloud/scripts/deploy.sh"])
    assert (python, web) == (True, False)


def test_shared_ci_yaml_runs_both() -> None:
    python, web = classify([".github/workflows/ci.yml"])
    assert (python, web) == (True, True)


def test_docs_only_skips_both_gates() -> None:
    python, web = classify(["docs/cloud-services.md", "tasks/todo.md", "README.md"])
    assert (python, web) == (False, False)


def test_mixed_web_and_python_runs_both() -> None:
    python, web = classify(["web/app/page.tsx", "scripts/ib_sync.py"])
    assert (python, web) == (True, True)


def test_unknown_runtime_path_runs_both() -> None:
    python, web = classify(["config/sudoers.d/radon-deploy"])
    assert (python, web) == (True, True)


def test_empty_range_runs_both() -> None:
    python, web = classify([])
    assert (python, web) == (True, True)


def test_write_output_appends_github_output(tmp_path: Path) -> None:
    target = tmp_path / "github_output"
    write_output(False, True, target)
    assert target.read_text(encoding="utf-8") == "python=false\nweb=true\n"

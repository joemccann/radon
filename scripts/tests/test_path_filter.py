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


def test_scripts_lib_vitest_suites_run_the_web_gate() -> None:
    """REL-070 / R-202: ``scripts/lib/**/*.test.js`` is a vitest-only suite.

    ``vitest.config.ts`` includes it and pytest collects none of it, so routing
    the path to the python gate alone lets the WS relay's stale-tick recovery
    ladder, reconnect gate and backpressure modules ship untested.
    """
    python, web = classify(["scripts/lib/staleDataMachine.js"])
    assert web is True, "scripts/lib/ carries vitest suites; the web gate must run"
    assert python is True, "scripts/lib/ is still under the python tree"


def test_scripts_lib_test_file_runs_the_web_gate() -> None:
    python, web = classify(["scripts/lib/demoMirrorReliability.test.js"])
    assert (python, web) == (True, True)


def test_ordinary_scripts_path_still_skips_the_web_gate() -> None:
    """The scripts/lib carve-out must not drag every python change onto vitest."""
    python, web = classify(["scripts/ib_sync.py", "scripts/clients/uw.py"])
    assert (python, web) == (True, False)

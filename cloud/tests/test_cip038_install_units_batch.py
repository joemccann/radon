"""CIP-038: install-units must read tip blobs in one cat-file batch.

Deploy SSH on main runs 45183187555, 45181898751 and 45179451125 spent
34.1s, 34.7s and 34.9s in install-units while installing nothing
(unchanged=158). Each unit was a separate `git cat-file blob`. The
batch path keeps the same tip, digest, symlink and mismatch gates.
A batch failure falls back to the per-unit read.
"""

from __future__ import annotations

import pathlib
import shlex

from test_install_units import SERVICE_BODY, Sandbox


def _git_wrapper(path: pathlib.Path, log: pathlib.Path, *, fail_batch: bool) -> None:
    fail = "exit 1" if fail_batch else ":"
    path.write_text(
        "#!/bin/bash\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(log))}\n"
        'if [[ " $* " == *" --batch "* ]]; then\n'
        f"  {fail}\n"
        "fi\n"
        'exec /usr/bin/git "$@"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)


def _service_blob_calls(log: pathlib.Path) -> list[str]:
    calls = log.read_text(encoding="utf-8").splitlines()
    return [
        line
        for line in calls
        if "cat-file" in line and "cloud/services/" in line and "--batch" not in line
    ]


def _batch_calls(log: pathlib.Path) -> list[str]:
    return [
        line
        for line in log.read_text(encoding="utf-8").splitlines()
        if "cat-file" in line and "--batch" in line
    ]


def test_unchanged_units_are_read_in_one_batch(tmp_path):
    box = Sandbox(tmp_path)
    for index in range(8):
        name = f"radon-batch-{index}.service"
        box.source(name, SERVICE_BODY)
        box.installed(name, SERVICE_BODY)
    log = tmp_path / "git-calls.log"
    _git_wrapper(tmp_path / "git", log, fail_batch=False)
    box.env["RADON_TEST_GIT"] = str(tmp_path / "git")

    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "unchanged=8" in result.stdout
    assert box.systemctl_calls() == []
    assert _batch_calls(log), log.read_text(encoding="utf-8")
    assert _service_blob_calls(log) == []


def test_batch_failure_falls_back_to_per_unit_cat_file(tmp_path):
    box = Sandbox(tmp_path)
    box.source("radon-credit-spread.service", SERVICE_BODY)
    box.installed("radon-credit-spread.service", SERVICE_BODY)
    log = tmp_path / "git-calls.log"
    _git_wrapper(tmp_path / "git", log, fail_batch=True)
    box.env["RADON_TEST_GIT"] = str(tmp_path / "git")

    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "unchanged=1" in result.stdout
    assert "batch blob read failed" in result.stderr
    assert _service_blob_calls(log), log.read_text(encoding="utf-8")


def test_changed_unit_is_promoted_from_the_batch_body(tmp_path):
    box = Sandbox(tmp_path)
    name = "radon-credit-spread.service"
    box.source(name, SERVICE_BODY)
    box.installed(name, SERVICE_BODY + "# stale\n")
    log = tmp_path / "git-calls.log"
    _git_wrapper(tmp_path / "git", log, fail_batch=False)
    box.env["RADON_TEST_GIT"] = str(tmp_path / "git")

    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "updated=1" in result.stdout
    assert (box.unit_dir / name).read_text() == SERVICE_BODY
    assert _batch_calls(log), log.read_text(encoding="utf-8")
    assert _service_blob_calls(log) == []


def test_missing_tip_blob_skips_only_that_unit(tmp_path):
    box = Sandbox(tmp_path)
    box.source("radon-credit-spread.service", SERVICE_BODY)
    box.record("radon-absent.service", SERVICE_BODY)

    result = box.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert (box.unit_dir / "radon-credit-spread.service").read_text() == SERVICE_BODY
    assert not (box.unit_dir / "radon-absent.service").exists()
    assert "radon-absent.service (not-at-main-tip)" in result.stdout

"""Beta stack (beta.radon.run) is retired and its host artifacts removed
(2026-10-11). Nothing beta may return: no provisioning tree, unit, Caddy
site, sudoers fragment, drift carve-out, or the beta-named swapfile. Host
swap is the canonical `/swapfile` that setup-vps.sh provisions.
"""

import importlib.util
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLOUD = ROOT / "cloud"
SETUP = CLOUD / "scripts" / "setup-vps.sh"


def _load_drift_audit():
    spec = importlib.util.spec_from_file_location(
        "drift_audit_beta_sunset", CLOUD / "scripts" / "drift_audit.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_beta_provisioning_script_is_gone():
    assert not (ROOT / "deploy" / "beta" / "setup-beta.sh").exists()


def test_beta_sudoers_dropin_is_gone():
    assert not (CLOUD / "config" / "sudoers.d" / "radon-beta").exists()


def test_root_and_web_do_not_ship_npm_lockfiles():
    assert not (ROOT / "package-lock.json").exists()
    assert not (ROOT / "web" / "package-lock.json").exists()
    assert (ROOT / "bun.lock").is_file()
    assert (ROOT / "web" / "bun.lock").is_file()
    assert (ROOT / "site" / "package-lock.json").is_file()


def test_no_beta_unit_files_ship():
    assert sorted(p.name for p in (CLOUD / "services").rglob("*beta*")) == []


def test_caddy_has_no_beta_site():
    for path in (CLOUD / "caddy").rglob("*"):
        if path.is_file():
            assert "beta.radon.run" not in path.read_text(encoding="utf-8", errors="ignore"), path


def test_setup_vps_inventories_no_beta_unit_or_swap():
    text = SETUP.read_text(encoding="utf-8")
    assert "radon-beta" not in text
    assert "swapfile-beta" not in text


def test_drift_allowlist_has_no_beta_entry_or_note():
    assert "beta" not in (CLOUD / "config" / "drift-allowlist.conf").read_text().lower()


def test_unit_manifests_have_no_beta():
    for name in ("installed-units.sha256", "auto-sync-units.txt"):
        assert "beta" not in (CLOUD / "config" / name).read_text().lower(), name


def test_no_cloud_config_or_script_names_the_beta_swapfile():
    for path in list((CLOUD / "config").rglob("*")) + list((CLOUD / "scripts").rglob("*")):
        if path.is_file():
            assert "swapfile-beta" not in path.read_text(encoding="utf-8", errors="ignore"), path


# -- drift audit: beta leftovers are drift again, not "known-untracked" --------


def test_drift_audit_has_no_beta_carve_out():
    da = _load_drift_audit()
    assert not hasattr(da, "classify_untracked_unit")
    assert not hasattr(da, "classify_untracked_sudoers")
    assert "radon-beta" not in (CLOUD / "scripts" / "drift_audit.py").read_text()


def test_reappearing_beta_unit_is_untracked_drift(tmp_path, monkeypatch):
    da = _load_drift_audit()
    live = tmp_path / "systemd"
    live.mkdir()
    (live / "radon-beta-api.service").write_text("[Service]\nExecStart=/bin/true\n")
    repo = tmp_path / "repo"
    (repo / "services").mkdir(parents=True)
    monkeypatch.setattr(da, "SYSTEMD_DIR", live)
    monkeypatch.setattr(da, "REPO", repo)
    monkeypatch.setattr(da, "resolve_host_role", lambda: "combined")

    drifts: list[dict] = []
    known: list[str] = []
    da._check_units(drifts, known)

    assert known == []
    assert [d["id"] for d in drifts] == ["untracked-unit:radon-beta-api.service"]


def test_reappearing_beta_sudoers_is_untracked_drift(tmp_path, monkeypatch):
    da = _load_drift_audit()
    live = tmp_path / "sudoers.d"
    live.mkdir()
    (live / "radon-beta").write_text("radon ALL=(root) NOPASSWD: /bin/true\n")
    monkeypatch.setattr(da, "SUDOERS_DIR", live)
    monkeypatch.setattr(da, "REPO", tmp_path / "empty-repo")

    drifts: list[dict] = []
    known: list[str] = []
    da._check_sudoers(drifts, known)

    assert known == []
    assert [d["id"] for d in drifts] == ["untracked-sudoers:radon-beta"]


# -- canonical host swap --------------------------------------------------------


def _swap_harness(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "calls.log"
    log.write_text("")
    active = tmp_path / "active"
    active.write_text("")
    stubs = {
        # fallocate -l SIZE PATH
        "fallocate": f'printf "fallocate %s\\n" "$*" >> {log}\n: > "$3"',
        "mkswap": f'printf "mkswap %s\\n" "$*" >> {log}',
        "swapon": (
            f'if [ "$1" = "--show=NAME" ]; then cat {active}; exit 0; fi\n'
            f'printf "swapon %s\\n" "$*" >> {log}\nprintf "%s\\n" "$1" >> {active}'
        ),
    }
    for name, body in stubs.items():
        path = fake_bin / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
    fstab = tmp_path / "fstab"
    fstab.write_text("/dev/sda1 / ext4 defaults 0 1\n")
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RADON_SETUP_SOURCE_ONLY": "1",
        "RADON_SWAPFILE": str(tmp_path / "swapfile"),
        "RADON_FSTAB": str(fstab),
    }
    return log, env


def _run_ensure_swapfile(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f"set -euo pipefail\nsource {SETUP}\nensure_swapfile"],
        env=env,
        capture_output=True,
        text=True,
    )


def test_setup_main_provisions_swap():
    text = SETUP.read_text(encoding="utf-8")
    main = re.search(r"^main\(\) \{\n(.+?)\n\}", text, re.DOTALL | re.MULTILINE).group(1)
    assert "\n  ensure_swapfile\n" in main
    assert "RADON_SWAPFILE:-/swapfile}" in text
    assert "RADON_SWAP_SIZE:-2G}" in text


def test_ensure_swapfile_creates_enables_and_persists_once(tmp_path):
    log, env = _swap_harness(tmp_path)
    swapfile = env["RADON_SWAPFILE"]
    entry = f"{swapfile} none swap sw 0 0"

    first = _run_ensure_swapfile(env)
    assert first.returncode == 0, first.stderr + first.stdout
    assert log.read_text().splitlines() == [
        f"fallocate -l 2G {swapfile}",
        f"mkswap {swapfile}",
        f"swapon {swapfile}",
    ]
    assert oct(Path(swapfile).stat().st_mode & 0o777) == "0o600"
    assert Path(env["RADON_FSTAB"]).read_text().splitlines().count(entry) == 1

    log.write_text("")
    second = _run_ensure_swapfile(env)
    assert second.returncode == 0, second.stderr + second.stdout
    assert log.read_text() == "", "active swap must not be re-made"
    assert Path(env["RADON_FSTAB"]).read_text().splitlines().count(entry) == 1


def test_ensure_swapfile_refuses_a_symlinked_path(tmp_path):
    log, env = _swap_harness(tmp_path)
    victim = tmp_path / "victim"
    victim.write_text("keep")
    Path(env["RADON_SWAPFILE"]).symlink_to(victim)

    result = _run_ensure_swapfile(env)
    assert result.returncode != 0
    assert victim.read_text() == "keep"
    assert "mkswap" not in log.read_text()

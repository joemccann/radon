"""Beta stack was never finished. The provisioning tree and npm lockfiles it
kept alive must not return.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_beta_provisioning_script_is_gone():
    assert not (ROOT / "deploy" / "beta" / "setup-beta.sh").exists()


def test_beta_sudoers_dropin_is_gone():
    assert not (ROOT / "cloud" / "config" / "sudoers.d" / "radon-beta").exists()


def test_root_and_web_do_not_ship_npm_lockfiles():
    assert not (ROOT / "package-lock.json").exists()
    assert not (ROOT / "web" / "package-lock.json").exists()
    assert (ROOT / "bun.lock").is_file()
    assert (ROOT / "web" / "bun.lock").is_file()
    assert (ROOT / "site" / "package-lock.json").is_file()

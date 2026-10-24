"""Shared fixtures for radon-cloud test suite."""

import os
import pathlib
import sys

import pytest

# T-484: make sibling test helpers (_bash_toolchain) importable regardless of
# pytest's rootdir-driven sys.path handling.
_HERE = str(pathlib.Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# A host-wide git hook (core.hooksPath in the operator's global config, such
# as the fix/*-only push guard) reached every `git push` these suites make
# into their own tmp remotes and errored 41 fixtures per nightly run on the
# Mac. CI has no global git config; give the suite the same blank slate.
# setdefault keeps an explicit outer override.
os.environ.setdefault("GIT_CONFIG_GLOBAL", os.devnull)
os.environ.setdefault("GIT_CONFIG_SYSTEM", os.devnull)

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def root():
    return ROOT


@pytest.fixture
def services_dir(root):
    return root / "services"


@pytest.fixture
def scripts_dir(root):
    return root / "scripts"


@pytest.fixture
def caddy_dir(root):
    return root / "caddy"

"""Shared fixtures for radon-cloud test suite."""

import pathlib
import sys

import pytest

# T-484: make sibling test helpers (_bash_toolchain) importable regardless of
# pytest's rootdir-driven sys.path handling.
_HERE = str(pathlib.Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

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

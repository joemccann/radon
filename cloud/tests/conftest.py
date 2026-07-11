"""Shared fixtures for radon-cloud test suite."""

import pathlib

import pytest

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

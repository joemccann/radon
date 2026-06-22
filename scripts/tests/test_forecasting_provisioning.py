"""FU1: scipy must be provisioned for the forecasting venv.

F9 (scripts/vol_surface.py) imports `scipy.optimize.least_squares`, but scipy
was not declared in any requirements file the forecasting provisioning script
installs from. Without it, the VPS forecasting venv has no scipy and vol_surface
fails at import time.

Pure text assertions only — no shell execution, no network, no pip.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FC_REQS = REPO_ROOT / "requirements-forecasting.txt"
PROVISION_SCRIPT = REPO_ROOT / "scripts" / "forecasting" / "provision_venv.sh"
VOL_SURFACE = REPO_ROOT / "scripts" / "vol_surface.py"

# Matches a pinned (==) scipy requirement line, e.g. "scipy==1.17.1".
PINNED_SCIPY = re.compile(r"^\s*scipy\s*==\s*\d+\.\d+", re.MULTILINE)


def test_forecasting_requirements_file_exists():
    assert FC_REQS.is_file(), f"missing forecasting requirements file: {FC_REQS}"


def test_forecasting_requirements_pin_scipy():
    text = FC_REQS.read_text()
    assert PINNED_SCIPY.search(text), (
        "requirements-forecasting.txt must declare a pinned scipy "
        "(e.g. scipy==1.17.1) so the VPS forecasting venv installs it"
    )


def test_provision_script_reads_forecasting_requirements():
    """scipy is reachable by the provisioner via the forecasting reqs file."""
    text = PROVISION_SCRIPT.read_text()
    assert "requirements-forecasting.txt" in text, (
        "provision_venv.sh must install from requirements-forecasting.txt "
        "so the pinned scipy entry actually lands in the venv"
    )


def test_vol_surface_imports_scipy():
    """Sanity: the dependency that motivates this pin really uses scipy."""
    text = VOL_SURFACE.read_text()
    assert "from scipy" in text or "import scipy" in text, (
        "scripts/vol_surface.py is expected to import scipy"
    )

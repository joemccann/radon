import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("module", ["trade_blotter.flex_query", "trade_blotter.blotter_service"])
def test_flex_module_imports_without_requests_dependency(module):
    probe = (
        "import sys; sys.modules['requests'] = None; "
        f"import {module} as m; assert callable(m.FlexQueryFetcher)"
    )
    import_roots = os.pathsep.join([str(SCRIPTS_DIR), str(SCRIPTS_DIR / "trade_blotter")])
    run = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=SCRIPTS_DIR,
        env={**os.environ, "PYTHONPATH": import_roots},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 0, run.stderr

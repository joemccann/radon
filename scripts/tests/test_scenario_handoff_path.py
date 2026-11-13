"""The scenario analysis -> report handoff defaults to the operator's checkout.

/tmp is shared: the analysis writes portfolio details there world-readable and
follows whatever another local account planted at the fixed name.
"""

import ast
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parent


def _string_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_handoff_path_lives_in_repo_data_dir():
    from scenario_analysis import HANDOFF_PATH

    assert HANDOFF_PATH == REPO / "data" / "scenario_analysis.json"
    shared_tmp = {Path("/tmp").resolve(), Path(tempfile.gettempdir()).resolve()}
    assert not any(p in HANDOFF_PATH.resolve().parents for p in shared_tmp)


def test_writer_and_report_default_to_the_shared_constant():
    for name in ("scenario_analysis.py", "scenario_report.py"):
        literals = _string_constants(SCRIPTS / name)
        assert not any("/tmp/" in s for s in literals), f"{name} still hardcodes a /tmp path"

    report = (SCRIPTS / "scenario_report.py").read_text()
    assert "from scenario_analysis import HANDOFF_PATH" in report
    assert 'os.environ.get("RADON_SCENARIO_JSON", str(HANDOFF_PATH))' in report

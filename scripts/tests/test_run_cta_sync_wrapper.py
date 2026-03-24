from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_run_cta_sync_preserves_literal_env_values(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    scripts_dir = repo_dir / "scripts"
    web_dir = repo_dir / "web"
    data_dir = repo_dir / "data" / "menthorq_cache"
    logs_dir = repo_dir / "logs"
    bin_dir = tmp_path / "bin"
    output_path = tmp_path / "captured-env.json"

    scripts_dir.mkdir(parents=True)
    web_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    logs_dir.mkdir(parents=True)
    bin_dir.mkdir(parents=True)

    wrapper_src = Path(__file__).resolve().parents[1] / "run_cta_sync.sh"
    shutil.copy2(wrapper_src, scripts_dir / "run_cta_sync.sh")
    (scripts_dir / "run_cta_sync.sh").chmod((scripts_dir / "run_cta_sync.sh").stat().st_mode | stat.S_IXUSR)

    literal_user = "cta-user@example.com"
    literal_pass = r"Abc$HOME!xyz%42"

    (repo_dir / ".env").write_text(
        f"MENTHORQ_USER={literal_user}\nMENTHORQ_PASS={literal_pass}\n",
        encoding="utf-8",
    )
    (web_dir / ".env").write_text("", encoding="utf-8")

    (scripts_dir / "cta_sync_service.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import json",
                "import os",
                "from pathlib import Path",
                f"Path({str(output_path)!r}).write_text(",
                "    json.dumps({",
                "        'MENTHORQ_USER': os.environ.get('MENTHORQ_USER'),",
                "        'MENTHORQ_PASS': os.environ.get('MENTHORQ_PASS'),",
                "    }),",
                "    encoding='utf-8',",
                ")",
            ]
        ),
        encoding="utf-8",
    )

    _write_executable(
        bin_dir / "python3.13",
        "\n".join(
            [
                "#!/bin/bash",
                "if [ \"$1\" = \"-\" ]; then",
                "  cat >/dev/null",
                "  exit 0",
                "fi",
                "exec /usr/bin/env python3 \"$@\"",
            ]
        ),
    )

    env = {
        **os.environ,
        "RADON_PYTHON_BIN": str(bin_dir / "python3.13"),
    }
    result = subprocess.run(
        ["bash", str(scripts_dir / "run_cta_sync.sh"), "--source", "test"],
        cwd=repo_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    captured = json.loads(output_path.read_text(encoding="utf-8"))
    assert captured["MENTHORQ_USER"] == literal_user
    assert captured["MENTHORQ_PASS"] == literal_pass

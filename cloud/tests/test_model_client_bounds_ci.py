"""A client bundle that outgrows its pinned disk bound fails CI, not a deploy."""
from __future__ import annotations

import importlib.util
import inspect
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/ci/check_model_client_bounds.py"
WORKFLOW = REPO / ".github/workflows/app-images.yml"


def checker():
    spec = importlib.util.spec_from_file_location("check_model_client_bounds", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_engine(tmp_path: Path, *, linux_files: int = 3, linux_size: int = 10) -> str:
    """Emit tar archives shaped like the gateway image's two exports."""
    darwin = io.BytesIO()
    files = {**{f"bin/{name}": b"macho" for name in ("claude", "codex", "grok", "rg")},
             **{f"model_gateway/{name}": b"pass\n" for name in ("__init__.py", "client.py", "cli.py", "cli_bridge.py")},
             "package.json": json.dumps({"dependencies": {"@anthropic-ai/claude-code": "2.1.311", "@openai/codex": "0.160.1", "@xai-official/grok": "1.0.70"}}).encode()}
    with tarfile.open(fileobj=darwin, mode="w") as archive:
        for name, raw in files.items():
            info = tarfile.TarInfo(name); info.size = len(raw); archive.addfile(info, io.BytesIO(raw))
    linux = io.BytesIO()
    with tarfile.open(fileobj=linux, mode="w") as archive:
        for number in range(linux_files):
            info = tarfile.TarInfo(f"file{number}"); info.size = linux_size
            archive.addfile(info, io.BytesIO(b"x" * linux_size))
    (tmp_path / "darwin.tar").write_bytes(darwin.getvalue())
    (tmp_path / "linux.tar").write_bytes(linux.getvalue())
    engine = tmp_path / "engine"
    engine.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "if sys.argv[1] != 'run': raise SystemExit(0)\n"
        f"name = {str(tmp_path / 'darwin.tar')!r} if '/opt/radon-darwin' in sys.argv else {str(tmp_path / 'linux.tar')!r}\n"
        "sys.stdout.buffer.write(open(name, 'rb').read())\n"
    )
    engine.chmod(0o755)
    return str(engine)


def test_checker_reports_measured_sizes_within_bounds(tmp_path):
    result = checker().measure(fake_engine(tmp_path), "image", str(tmp_path))
    assert result["linux"]["members"] == 3 and result["linux"]["payload_bytes"] == 30
    assert result["linux"]["max_payload_bytes"] == checker().load_model_clients().LINUX_MAX_PAYLOAD_BYTES
    assert result["darwin"]["bytes"] == (tmp_path / "darwin.tar").stat().st_size


def test_checker_fails_when_linux_members_exceed_pin(tmp_path, monkeypatch):
    module = checker()
    real = module.load_model_clients
    def tight():
        api = real(); api.LINUX_MAX_MEMBERS = 2; return api
    monkeypatch.setattr(module, "load_model_clients", tight)
    assert module.main(["--engine", fake_engine(tmp_path), "--image", "image", "--scratch", str(tmp_path)]) == 1


@pytest.mark.parametrize("name", ["LINUX_MAX_PAYLOAD_BYTES", "LINUX_MAX_STREAM_BYTES", "DARWIN_MAX_EXPORT_BYTES"])
def test_checker_fails_when_a_byte_pin_is_exceeded(tmp_path, monkeypatch, name):
    module = checker()
    real = module.load_model_clients
    def tight():
        api = real(); setattr(api, name, 20); return api
    monkeypatch.setattr(module, "load_model_clients", tight)
    assert module.main(["--engine", fake_engine(tmp_path), "--image", "image", "--scratch", str(tmp_path)]) == 1


def test_cli_succeeds_on_a_bundle_within_bounds(tmp_path):
    result = subprocess.run([sys.executable, "-I", str(SCRIPT), "--engine", fake_engine(tmp_path), "--image", "image"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["linux"]["members"] == 3


def test_app_images_enforces_bounds_on_the_exact_built_gateway_image():
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["gateway-image"]["steps"]
    names = [step.get("name", "") for step in steps]
    build = names.index("Build and publish model gateway image")
    reuse_bounds = names.index("Reuse verified client disk bounds")
    check = names.index("Enforce pinned model client disk bounds")
    assert build < reuse_bounds < check
    step = steps[check]
    # Gateway reuse re-tags an image that already passed this check.
    # CIP-026 also skips when a pbounds-<payload> receipt exists.
    assert steps[build]["if"] == "steps.reuse-gateway.outputs.hit != 'true'"
    assert step["if"] == (
        "steps.reuse-gateway.outputs.hit != 'true'"
        " && steps.reuse-bounds.outputs.hit != 'true'"
    )
    assert steps[build]["with"]["load"] == "true"
    assert "scripts/ci/check_model_client_bounds.py" in step["run"]
    assert '--image "ghcr.io/joemccann/radon-model-gateway:${{ github.sha }}"' in step["run"]
    assert "continue-on-error" not in step


def test_checker_uses_ci_export_budget_but_production_default_is_unchanged(tmp_path, monkeypatch):
    module = checker()
    api = module.load_model_clients()
    assert api.EXPORT_DEADLINE_SECONDS == 60
    assert inspect.signature(api.export_from_image).parameters["deadline_seconds"].default == 60
    seen = []
    real = api.export_from_image

    def spy(*args, **kwargs):
        seen.append(kwargs.get("deadline_seconds"))
        return real(*args, **kwargs)

    api.export_from_image = spy
    monkeypatch.setattr(module, "load_model_clients", lambda: api)
    module.measure(fake_engine(tmp_path), "image", str(tmp_path))
    assert seen == [module.CI_EXPORT_DEADLINE_SECONDS] * 2
    assert module.CI_EXPORT_DEADLINE_SECONDS == 300
    assert module.CI_EXPORT_DEADLINE_SECONDS > api.EXPORT_DEADLINE_SECONDS
    seen.clear()
    assert module.main([
        "--engine", fake_engine(tmp_path), "--image", "image", "--scratch", str(tmp_path),
        "--export-deadline", "120",
    ]) == 0
    assert seen == [120.0, 120.0]


def test_pin_hint_is_only_for_size_failures(tmp_path, monkeypatch, capsys):
    module = checker()
    api = module.load_model_clients()
    monkeypatch.setattr(module, "load_model_clients", lambda: api)

    def export_error(exc: BaseException) -> str:
        def _export(*_args, **_kwargs):
            raise exc
        api.export_from_image = _export
        assert module.main(["--engine", "engine", "--image", "image", "--scratch", str(tmp_path)]) == 1
        return capsys.readouterr().err

    timeout_err = export_error(TimeoutError("client export deadline"))
    assert "TimeoutError: client export deadline" in timeout_err
    assert "Raise the pins" not in timeout_err
    assert "Raise the pins" not in export_error(ValueError("client export failed"))
    size_err = export_error(ValueError("client export exceeds bounds"))
    assert "ValueError: client export exceeds bounds" in size_err
    assert "Raise the pins in cloud/scripts/model_clients.py" in size_err

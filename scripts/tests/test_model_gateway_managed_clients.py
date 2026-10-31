"""Legacy coding maintenance cannot mutate or bypass CI-owned model clients."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import pytest
import grok_runtime

ROOT = Path(__file__).resolve().parents[2]


def test_bootstrap_is_status_only_and_never_touches_provider_secrets(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "NVIDIA_API_KEY": "nvapi-fixture", "CEREBRAS_API_KEY": "csk-fixture"}
    result = subprocess.run(["/bin/bash", str(ROOT / "scripts/agent_cli_bootstrap.sh")], env=env, capture_output=True, text=True)
    assert not (tmp_path / ".radon/agent-cli/env").exists()
    assert not (tmp_path / ".fx/settings.json").exists()
    assert "nvapi-fixture" not in result.stdout + result.stderr
    body = (ROOT / "scripts/agent_cli_bootstrap.sh").read_text()
    assert "model_gateway/cli.py" in body
    assert "NVIDIA_API_KEY" not in body and "CEREBRAS_API_KEY" not in body


def fixture_release(tmp_path):
    root = tmp_path / "clients"; root.mkdir()
    release = root / ("a" * 40); (release / "bin").mkdir(parents=True)
    hashes = {}
    for name in ("grok", "node"):
        path = release / "bin" / name; path.write_text("fixture-" + name); path.chmod(0o555)
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    helper_hashes = {}
    directory = release / "lib/model_gateway"; directory.mkdir(parents=True)
    for name in ("__init__.py", "client.py", "cli.py", "cli_bridge.py"):
        path = directory / name; path.write_text("# fixture"); path.chmod(0o444)
        helper_hashes["model_gateway/" + name] = hashlib.sha256(path.read_bytes()).hexdigest()
    directory.chmod(0o555); directory.parent.chmod(0o555)
    proof = {"version": 1, "release_sha": release.name, "cli_versions": {"grok": "1.0.41"}, "sha256": hashes, "helper_sha256": helper_hashes}
    manifest = release / "manifest.json"; manifest.write_text(json.dumps(proof)); manifest.chmod(0o444)
    (release / "bin").chmod(0o555); release.chmod(0o555)
    (root / "current").symlink_to(release.name)
    return root, release, proof


def test_managed_manifest_checks_hash_versions_and_immutable_modes(tmp_path):
    root, release, proof = fixture_release(tmp_path)
    assert grok_runtime.managed_manifest(root, owner_uid=os.getuid())["cli_versions"]["grok"] == "1.0.41"
    target = release / "bin/grok"; target.chmod(0o755)
    with pytest.raises(grok_runtime.GrokRuntimeError, match="immutable"):
        grok_runtime.managed_manifest(root, owner_uid=os.getuid())
    target.write_text("changed"); target.chmod(0o555)
    with pytest.raises(grok_runtime.GrokRuntimeError, match="hash"):
        grok_runtime.managed_manifest(root, owner_uid=os.getuid())


def test_managed_manifest_refuses_selector_escape(tmp_path):
    root, release, proof = fixture_release(tmp_path)
    (root / "current").unlink(); (root / "current").symlink_to("../escape")
    with pytest.raises(grok_runtime.GrokRuntimeError):
        grok_runtime.managed_manifest(root, owner_uid=os.getuid())


def test_managed_runtime_uses_gateway_metadata_and_never_executes_models(monkeypatch):
    monkeypatch.setattr(grok_runtime, "managed_manifest", lambda *a, **k: {"cli_versions": {"grok": "1.0.41"}, "release_sha": "a" * 40})
    monkeypatch.setattr(grok_runtime, "gateway_request", lambda *a, **k: {"models": [{"id": "grok-4.7", "provider": "xai"}, {"id": "grok-4.6", "provider": "xai"}, {"id": "grok-image", "provider": "xai"}]})
    result = grok_runtime.resolve_latest(grok_bin="/opt/radon-model-clients/current/bin/grok", runner=lambda *a: pytest.fail("no native provider metadata probe"))
    assert not result.refused and result.model == "grok-4.7"
    assert result.source == "gateway-manifest"


def test_native_model_client_updater_is_retired(monkeypatch):
    spec = importlib.util.spec_from_file_location("managed_toolchain", ROOT / "scripts/nightly_toolchain_update.py")
    toolchain = importlib.util.module_from_spec(spec); spec.loader.exec_module(toolchain)
    monkeypatch.setattr(toolchain, "run", lambda *a, **k: pytest.fail("CI owns model CLI mutations"))
    assert toolchain.update_native() == [{"tool": "model-clients", "status": "managed-by-gateway-ci"}]

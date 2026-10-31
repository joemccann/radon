"""Pinned operator CLI promotion never executes or extracts untrusted host paths."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

CLOUD = Path(__file__).resolve().parents[1]
SCRIPT = CLOUD / "scripts/model_clients.py"
SHA = "a" * 40


def module():
    spec = importlib.util.spec_from_file_location("model_clients", SCRIPT)
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def payload(*, unsafe: str | None = None, link: bool = False) -> io.BytesIO:
    result = io.BytesIO()
    files = {
        "package.json": json.dumps({"dependencies": {"@anthropic-ai/claude-code": "2.1.311", "@openai/codex": "0.160.1", "@xai-official/grok": "1.0.70"}}).encode(),
        "antigravity.json": b'{"version":"1.3.1"}',
        "node": b"image node payload",
        **{f"bin/{name}": b"image CLI payload" for name in ("claude", "codex", "grok", "agy")},
        **{f"model_gateway/{name}": b"pass\n" for name in ("__init__.py", "client.py", "cli.py", "cli_bridge.py")},
    }
    if unsafe:
        files[unsafe] = b"refuse me"
    with tarfile.open(fileobj=result, mode="w") as archive:
        for name, raw in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(raw)
            entry.mode = 0o755 if name.startswith("bin/") or name == "node" else 0o644
            archive.addfile(entry, io.BytesIO(raw))
        if link:
            entry = tarfile.TarInfo("escape")
            entry.type = tarfile.SYMTYPE
            entry.linkname = "../../escape"
            archive.addfile(entry)
    result.seek(0)
    return result


def test_promotes_only_complete_pinned_payload_and_records_version_hash_proof(tmp_path):
    root = tmp_path / "clients"
    installed = module().install_from_tar(payload(), root, SHA, owner_uid=os.getuid())
    assert installed == root / SHA
    assert (root / "current").resolve() == installed
    proof = json.loads((installed / "manifest.json").read_text())
    assert proof["release_sha"] == SHA
    assert proof["cli_versions"]["grok"] == "1.0.70"
    assert len(proof["sha256"]["grok"]) == 64
    assert len(proof["helper_sha256"]["model_gateway/cli.py"]) == 64
    assert (installed / "lib/model_gateway/cli.py").is_file()
    assert not (installed / "lib/model_gateway/engine.py").exists()
    assert (installed / "bin/node").read_bytes() == b"image node payload"
    assert not (installed / "bin/grok").stat().st_mode & 0o222


@pytest.mark.parametrize("unsafe", ["../escape", "/tmp/escape", "../../bin/grok"])
def test_refuses_archive_traversal_and_retains_previous_activation(tmp_path, unsafe):
    root = tmp_path / "clients"
    old = module().install_from_tar(payload(), root, SHA, owner_uid=os.getuid())
    with pytest.raises((ValueError, tarfile.FilterError)):
        module().install_from_tar(payload(unsafe=unsafe), root, "b" * 40, owner_uid=os.getuid())
    assert (root / "current").resolve() == old
    assert not (tmp_path / "escape").exists()
    assert not (root / ("b" * 40)).exists()


def test_refuses_escaping_archive_symlinks(tmp_path):
    with pytest.raises((ValueError, tarfile.FilterError)):
        module().install_from_tar(payload(link=True), tmp_path / "clients", SHA, owner_uid=os.getuid())


def test_refuses_symlinked_installation_root(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    root = tmp_path / "clients"
    root.symlink_to(other, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        module().install_from_tar(payload(), root, SHA, owner_uid=os.getuid())
    assert list(other.iterdir()) == []


@pytest.mark.parametrize("sha", ["latest", "../escape", "a" * 39, "g" * 40])
def test_refuses_any_release_identity_except_an_exact_sha(tmp_path, sha):
    with pytest.raises(ValueError):
        module().install_from_tar(payload(), tmp_path / "clients", sha, owner_uid=os.getuid())


def test_root_helper_promotes_clients_before_starting_the_gateway():
    helper = (CLOUD / "scripts/deploy-root-helper.sh").read_text()
    for verb in ("restart-managed", "recover"):
        body = helper.split(f"  {verb})\n", 1)[1].split("    ;;", 1)[0]
        assert body.index("install-model-clients") < body.index('"${CORE_SERVICES[@]}"')
    bootstrap = (CLOUD / "scripts/bootstrap-control-plane.sh").read_text()
    assert "scripts/model_clients.py" in bootstrap
    assert "/usr/local/lib/radon/model_clients.py" in bootstrap


@pytest.mark.parametrize('damage',['writable','symlink'])
def test_existing_client_release_requires_immutable_internal_directories(tmp_path,damage):
    api=module();root=tmp_path/'clients';installed=api.install_from_tar(payload(),root,SHA,owner_uid=os.getuid())
    installed.chmod(0o755)
    if damage=='writable':(installed/'lib/model_gateway').chmod(0o777)
    else:
        (installed/'lib').chmod(0o755)
        (installed/'lib/model_gateway').rename(installed/'lib/original')
        (installed/'lib/model_gateway').symlink_to(installed/'lib/original')
        (installed/'lib').chmod(0o555)
    installed.chmod(0o555)
    try:
        with pytest.raises(ValueError):api.install_from_tar(payload(),root,SHA,owner_uid=os.getuid())
    finally:
        for folder,_,_ in os.walk(installed,followlinks=False):os.chmod(folder,0o700)


def test_release_retention_bounds_old_payloads_without_pruning_recently_demoted_sessions(tmp_path):
    import time
    api=module();root=tmp_path/'clients'
    for number in range(1,8):api.install_from_tar(payload(),root,f'{number:040x}',owner_uid=os.getuid())
    # An app-only deploy burst must leave all still-recent native sessions.
    assert len([p for p in root.iterdir() if len(p.name)==40])==7
    for number in range(1,8):
        old=time.time()-20000+number;os.utime(root/f'{number:040x}',(old,old))
    api.install_from_tar(payload(),root,f'{8:040x}',owner_uid=os.getuid())
    assert {p.name for p in root.iterdir() if len(p.name)==40}=={f'{n:040x}' for n in range(4,9)}
    assert (root/'current').resolve()==root/f'{8:040x}'

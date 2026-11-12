"""Clients are exported before teardown and only selected inside the restart window.

2026-11-11: install-model-clients exported a changed gateway payload (~90s)
inside restart-managed, after stop-clean had stopped radon-nextjs, so
app.radon.run served 502 for the whole export.
"""
import inspect
import json
import os
import subprocess
from pathlib import Path

import pytest

from test_model_clients_darwin import payload as darwin_payload
from test_model_clients_install import SHA, module, payload

CLOUD = Path(__file__).resolve().parents[1]
DEPLOY = CLOUD / "scripts/deploy.sh"
IMAGE = "ghcr.io/joemccann/radon-model-gateway:" + SHA
IMAGE_ID = "sha256:" + ("ab" * 32)
OTHER_ID = "sha256:" + ("cd" * 32)
PREVIOUS = "e" * 40


def _ids(mapping):
    def run(argv, **_kwargs):
        class Result:
            returncode = 1
            stdout = ""
            stderr = ""

        if argv[1:4] == ["image", "inspect", "--format"] and "{{.Id}}" in argv[4]:
            tag = argv[-1].rsplit(":", 1)[-1]
            if tag in mapping:
                Result.returncode = 0
                Result.stdout = mapping[tag] + "\n"
        elif argv[1] == "images":
            Result.returncode = 0
        return Result()

    return run


OWNER_FUNCS = (
    "_trusted_root", "_immutable_tree", "_prune_releases", "_prune_stale", "_link_identical_release",
    "install_from_tar", "publish_darwin_bundle", "_identical_darwin_donor", "_verify_darwin_export",
    "_verify_linux_release", "_clone_verified_release", "reuse_identical_image_clients",
    "activate_staged_clients",
)


def _as_test_owner(api, monkeypatch):
    """Production pins root (uid 0); the isolated tmp roots belong to this uid."""
    for name in OWNER_FUNCS:
        original = getattr(api, name)
        signature = inspect.signature(original)

        def wrapped(*args, __original=original, __signature=signature, **kwargs):
            bound = __signature.bind(*args, **kwargs)
            bound.apply_defaults()
            if bound.arguments.get("owner_uid") == 0:
                bound.arguments["owner_uid"] = os.getuid()
            return __original(*bound.args, **bound.kwargs)

        monkeypatch.setattr(api, name, wrapped)


@pytest.fixture
def host(tmp_path, monkeypatch):
    api = module()
    root = tmp_path / "clients"
    exports = tmp_path / "exports"
    (exports / "releases").mkdir(parents=True)
    root.mkdir()
    monkeypatch.setattr(api, "ROOT", root)
    monkeypatch.setattr(api, "EXPORT_ROOT", exports)
    monkeypatch.setattr(api.os, "geteuid", lambda: 0)
    monkeypatch.setattr(api, "check_model_space", lambda: {"filesystems": []})
    _as_test_owner(api, monkeypatch)
    # The serving release is selected before the deploy starts.
    api.install_from_tar(payload(), root, PREVIOUS)
    api.publish_darwin_bundle(darwin_payload(), exports / "releases", PREVIOUS)
    return api, root, exports


def _exporter(api, monkeypatch, calls):
    def export(engine, image, stream, darwin=False):
        calls.append("darwin" if darwin else "linux")
        stream.write((darwin_payload() if darwin else payload()).read())
        stream.seek(0)

    monkeypatch.setattr(api, "export_from_image", export)


def test_stage_exports_without_selecting_the_new_release(host, monkeypatch):
    api, root, exports = host
    calls = []
    _exporter(api, monkeypatch, calls)
    monkeypatch.setattr(api.subprocess, "run", _ids({SHA: IMAGE_ID}))
    assert api.main(["stage", "/usr/bin/podman", IMAGE, SHA]) == 0
    assert calls == ["linux", "darwin"]
    assert os.readlink(root / "current") == PREVIOUS
    assert json.loads((root / SHA / "manifest.json").read_text())["image_id"] == IMAGE_ID
    assert (exports / "releases" / SHA / "bundle.tar").is_file()


def test_install_selects_a_staged_release_without_exporting(host, monkeypatch):
    api, root, _exports = host
    calls = []
    _exporter(api, monkeypatch, calls)
    monkeypatch.setattr(api.subprocess, "run", _ids({SHA: IMAGE_ID}))
    assert api.main(["stage", "/usr/bin/podman", IMAGE, SHA]) == 0
    calls.clear()
    monkeypatch.setattr(api, "export_from_image", lambda *_a, **_k: pytest.fail("export inside the outage"))
    assert api.main(["/usr/bin/podman", IMAGE, SHA]) == 0
    assert os.readlink(root / "current") == SHA


def test_retagged_image_after_staging_still_exports(host, monkeypatch):
    """The staged receipt names the old image id: no fast path."""
    api, root, _exports = host
    calls = []
    _exporter(api, monkeypatch, calls)
    monkeypatch.setattr(api.subprocess, "run", _ids({SHA: IMAGE_ID}))
    assert api.main(["stage", "/usr/bin/podman", IMAGE, SHA]) == 0
    calls.clear()
    monkeypatch.setattr(api.subprocess, "run", _ids({SHA: OTHER_ID}))
    assert api.main(["/usr/bin/podman", IMAGE, SHA]) == 0
    assert calls == ["linux", "darwin"]
    assert os.readlink(root / "current") == SHA


def test_staged_release_missing_darwin_export_is_not_selected_early(host, monkeypatch):
    api, root, _exports = host
    api.install_from_tar(payload(), root, SHA, image_id=IMAGE_ID, activate=False)
    monkeypatch.setattr(api.subprocess, "run", _ids({SHA: IMAGE_ID}))
    assert api.activate_staged_clients("/usr/bin/podman", IMAGE, SHA) is False
    assert os.readlink(root / "current") == PREVIOUS


def test_staged_release_without_image_receipt_is_not_proof(host, monkeypatch):
    api, root, exports = host
    api.install_from_tar(payload(), root, SHA, activate=False)
    api.publish_darwin_bundle(darwin_payload(), exports / "releases", SHA)
    monkeypatch.setattr(api.subprocess, "run", _ids({SHA: IMAGE_ID}))
    assert api.activate_staged_clients("/usr/bin/podman", IMAGE, SHA) is False
    assert os.readlink(root / "current") == PREVIOUS


def test_stage_reuse_clones_without_selecting(host, monkeypatch):
    api, root, exports = host
    api.install_from_tar(payload(), root, "b" * 40, image_id=IMAGE_ID, activate=False)
    api.publish_darwin_bundle(darwin_payload(), exports / "releases", "b" * 40)
    monkeypatch.setattr(api.subprocess, "run", _ids({SHA: IMAGE_ID, "b" * 40: IMAGE_ID}))
    monkeypatch.setattr(api, "export_from_image", lambda *_a, **_k: pytest.fail("export"))
    assert api.main(["stage", "/usr/bin/podman", IMAGE, SHA]) == 0
    assert os.readlink(root / "current") == PREVIOUS
    assert api.main(["/usr/bin/podman", IMAGE, SHA]) == 0
    assert os.readlink(root / "current") == SHA


@pytest.mark.parametrize("args", [
    ["stage", "/usr/bin/podman", IMAGE, "A" * 40],
    ["stage", "/usr/bin/podman", "ghcr.io/joemccann/radon-model-gateway:" + "f" * 40, SHA],
    ["stage", "/bin/sh", IMAGE, SHA],
])
def test_stage_cli_takes_only_an_exact_gateway_sha(host, args):
    assert host[0].main(args) == 64


def _deploy_shell(tmp_path, sudo_body):
    calls = tmp_path / "calls"
    shell = f'''set -euo pipefail
source {DEPLOY}
sudo() {{ printf '%s\\n' "$*" >> {calls}; {sudo_body} }}
timeout() {{ shift; "$@"; }}
stage_model_clients {SHA}
printf 'teardown\\n' >> {calls}
'''
    result = subprocess.run(["bash", "-c", shell], capture_output=True, text=True)
    return result, calls.read_text().splitlines()


def test_deploy_stages_clients_with_the_exact_sha(tmp_path):
    result, lines = _deploy_shell(tmp_path, "return 0;")
    assert result.returncode == 0, result.stderr
    assert lines[-2] == f"-n /usr/local/sbin/radon-app-runtime stage-model-clients {SHA}"
    assert lines[-1] == "teardown"


def test_deploy_staging_refusal_does_not_block_the_release(tmp_path):
    result, lines = _deploy_shell(tmp_path, '[[ "$1" == -n && "$2" == -l ]] && return 0; return 78;')
    assert result.returncode == 0, result.stderr
    assert lines[-1] == "teardown"


def test_deploy_without_the_grant_skips_staging(tmp_path):
    result, lines = _deploy_shell(tmp_path, "return 1;")
    assert result.returncode == 0, result.stderr
    assert not any(line.startswith("-n /usr/local/sbin/radon-app-runtime stage") for line in lines)
    assert lines[-1] == "teardown"


def test_deploy_stages_before_any_teardown():
    main = DEPLOY.read_text()
    assert main.index('stage_model_clients "$requested_sha"') < main.index('restart_services "$requested_sha" "$prev_commit"')
    assert main.index('prepull_app_images "$requested_sha"\n') < main.index('stage_model_clients "$requested_sha"')


def test_stage_verb_is_granted_with_an_exact_sha_shape_only():
    sudoers = (CLOUD / "config/sudoers.d/radon-deploy").read_text()
    assert "/usr/local/sbin/radon-app-runtime stage-model-clients [0-9a-f]*" in sudoers
    assert "stage-model-clients *" not in sudoers
    runtime = (CLOUD / "scripts/radon-app-runtime.sh").read_text()
    assert "stage-model-clients)\n    [[ $# -eq 2 ]] || usage" in runtime
    assert '"$PYTHON" -I /usr/local/lib/radon/model_clients.py stage "$DOCKER" "$image" "$target"' in runtime

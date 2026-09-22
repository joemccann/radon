"""GPU deployment guardrails, exercised without Docker, GPUs or host writes."""

import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SPEC = importlib.util.spec_from_file_location(
    "gpu_runtime_under_test", Path(__file__).parents[1] / "gpu" / "runtime.py"
)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


@pytest.fixture
def config():
    return {
        "tailscale_ip": "100.101.102.103",
        "max_model_len": 8192,
        "gpu_memory_utilization": 0.8,
        "adapters": {},
    }


@pytest.fixture
def pins():
    return {
        "image": "vllm/vllm-openai@sha256:" + "a" * 64,
        "platform": "linux/amd64",
        "model": "Qwen/Qwen2.5-1.5B-Instruct",
        "model_revision": "b" * 40,
        "minimum_driver_major": 580,
    }


@pytest.mark.parametrize("address", ["0.0.0.0", "127.0.0.1", "10.0.0.1", "8.8.8.8", "::", "::1"])
def test_binding_requires_tailnet_ipv4(config, pins, address):
    config["tailscale_ip"] = address
    with pytest.raises(ValueError):
        runtime.validate(config, pins)


@pytest.mark.parametrize(
    "field,value",
    [
        ("image", "vllm/vllm-openai:latest"),
        ("image", "other/vllm@sha256:" + "a" * 64),
        ("image", "vllm/vllm-openai@sha256:" + "z" * 64),
        ("model_revision", "main"),
        ("model_revision", "b" * 39),
        ("platform", "linux/arm64"),
        ("model", "untrusted/model"),
        ("minimum_driver_major", 579),
        ("minimum_driver_major", True),
    ],
)
def test_invalid_runtime_pins_rejected(config, pins, field, value):
    pins[field] = value
    with pytest.raises(ValueError):
        runtime.validate(config, pins)


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_model_len", True),
        ("max_model_len", 1023),
        ("max_model_len", 32769),
        ("gpu_memory_utilization", True),
        ("gpu_memory_utilization", 0.91),
        ("gpu_memory_utilization", float("nan")),
        ("adapters", []),
    ],
)
def test_invalid_configuration_limits_rejected(config, pins, field, value):
    config[field] = value
    with pytest.raises(ValueError):
        runtime.validate(config, pins)


@pytest.mark.parametrize(
    "adapters",
    [
        {"radon-tagger": "../tagger/v1"},
        {"radon-tagger": "/tmp/tagger/v1"},
        {"radon-tagger": "tagger/../../v1"},
        {"radon-tagger": "tagger/current"},
        {"radon-tagger": "tagger/v0"},
        {"radon-tagger": "distill/v1"},
        {"other": "tagger/v1"},
        {"radon-tagger": 1},
    ],
)
def test_adapter_names_and_paths_are_constrained(config, pins, adapters):
    config["adapters"] = adapters
    with pytest.raises(ValueError):
        runtime.validate(config, pins)


def test_configuration_fields_are_closed(config, pins):
    config["docker_args"] = "--privileged"
    with pytest.raises(ValueError):
        runtime.validate(config, pins)
    del config["docker_args"]
    del config["tailscale_ip"]
    with pytest.raises(ValueError):
        runtime.validate(config, pins)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "HF_TOKEN=" + "h" * 32,
        "VLLM_API_KEY=short",
        "VLLM_API_KEY=" + "a" * 32 + "\nVLLM_API_KEY=" + "b" * 32,
        "VLLM_API_KEY='" + "a" * 32 + "'",
        "VLLM_API_KEY=$(touch /tmp/unwanted)",
        "VLLM_API_KEY=" + "a" * 32 + "\nOTHER=value",
        "VLLM_API_KEY=" + "a" * 257,
        "VLLM_API_KEY=" + "a" * 32 + "\nHF_TOKEN=" + "a" * 19,
    ],
)
def test_invalid_or_duplicate_secrets_rejected(text):
    with pytest.raises(ValueError):
        runtime.validate_secrets(text)


def test_literal_secrets_accepted():
    runtime.validate_secrets("# local service key\n\nVLLM_API_KEY=" + "a" * 32)
    runtime.validate_secrets("VLLM_API_KEY=" + "a" * 32 + "\nHF_TOKEN=hf_" + "b" * 30)


def test_command_is_private_pinned_nonroot_and_secret_free(config, pins):
    argv = runtime.command(config, pins, 2000, 0)
    for flag, value in [
        ("--user", "2000:0"),
        ("--cap-drop", "ALL"),
        ("--security-opt", "no-new-privileges"),
        ("--network", "host"),
        ("--host", config["tailscale_ip"]),
        ("--port", "8350"),
        ("--revision", pins["model_revision"]),
        ("--tokenizer-revision", pins["model_revision"]),
    ]:
        assert argv[argv.index(flag) + 1] == value
    assert pins["image"] in argv
    assert "--read-only" in argv and "--pull=never" in argv
    assert "--privileged" not in argv and "--api-key" not in argv
    assert "--lora-modules" not in argv
    assert any("dst=/adapters,readonly" in part for part in argv)


def test_adapters_added_as_individual_argv_values(config, pins):
    config["adapters"] = {"radon-tagger": "tagger/v2", "radon-distill": "distill/v1"}
    argv = runtime.command(config, pins, 2000, 0)
    assert argv[argv.index("--lora-modules") + 1 :] == [
        "radon-distill=/adapters/distill/v1",
        "radon-tagger=/adapters/tagger/v2",
    ]


@pytest.fixture
def host(monkeypatch, tmp_path, config):
    state = tmp_path.resolve() / "state"
    state.mkdir()
    monkeypatch.setattr(runtime, "STATE", state)
    output = Mock(
        side_effect=lambda argv, **kwargs: config["tailscale_ip"] + "\n" if "tailscale" in argv[0] else "580.126.09\n"
    )
    run = Mock(return_value=SimpleNamespace(returncode=0))
    trusted = Mock()
    monkeypatch.setattr(runtime.subprocess, "check_output", output)
    monkeypatch.setattr(runtime.subprocess, "run", run)
    monkeypatch.setattr(runtime, "trusted", trusted)
    return SimpleNamespace(state=state, output=output, run=run, trusted=trusted)


def test_preflight_checks_assigned_ip_driver_and_local_image(host, config, pins):
    runtime.preflight(config, pins)
    assert host.output.call_count == 2
    host.run.assert_called_once_with(
        ["/usr/bin/docker", "image", "inspect", pins["image"]],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def test_unassigned_private_address_is_rejected(host, config, pins):
    host.output.side_effect = None
    host.output.return_value = "100.101.102.104\n"
    with pytest.raises(ValueError, match="not assigned"):
        runtime.preflight(config, pins)
    host.run.assert_not_called()


@pytest.mark.parametrize("driver", ["", "579.1\n", "580.1\n570.5\n"])
def test_missing_or_old_driver_is_rejected(host, config, pins, driver):
    host.output.side_effect = [config["tailscale_ip"], driver]
    with pytest.raises(ValueError, match="driver"):
        runtime.preflight(config, pins)
    host.run.assert_not_called()


def test_missing_nvidia_smi_is_not_ignored(host, config, pins):
    host.output.side_effect = [config["tailscale_ip"], FileNotFoundError("nvidia-smi")]
    with pytest.raises(FileNotFoundError):
        runtime.preflight(config, pins)
    host.run.assert_not_called()


def test_image_inspect_failure_is_not_ignored(host, config, pins):
    host.run.side_effect = subprocess.CalledProcessError(1, "docker")
    with pytest.raises(subprocess.CalledProcessError):
        runtime.preflight(config, pins)


def make_adapter(host, config):
    config["adapters"] = {"radon-tagger": "tagger/v1"}
    adapter = host.state / "adapters/tagger/v1"
    adapter.mkdir(parents=True)
    (adapter / "adapter_config.json").write_text("{}")
    (adapter / "adapter_model.safetensors").write_bytes(b"fixture")
    return adapter


def test_complete_adapter_can_start(host, config, pins):
    adapter = make_adapter(host, config)
    runtime.preflight(config, pins)
    host.run.assert_called_once()
    assert [call.args[0] for call in host.trusted.call_args_list] == [
        adapter / "adapter_config.json",
        adapter / "adapter_model.safetensors",
    ]


def test_untrusted_adapter_is_rejected(host, config, pins):
    make_adapter(host, config)
    host.trusted.side_effect = ValueError("untrusted path")
    with pytest.raises(ValueError, match="untrusted path"):
        runtime.preflight(config, pins)
    host.run.assert_not_called()


def test_missing_adapter_weight_is_rejected(host, config, pins):
    adapter = make_adapter(host, config)
    (adapter / "adapter_model.safetensors").unlink()
    with pytest.raises(ValueError, match="incomplete"):
        runtime.preflight(config, pins)
    host.run.assert_not_called()


@pytest.mark.parametrize("target", ["version", "ancestor", "adapter_config.json", "adapter_model.safetensors"])
def test_adapter_symlinks_are_rejected(host, config, pins, target):
    adapter = make_adapter(host, config)
    source = adapter if target == "version" else adapter.parent if target == "ancestor" else adapter / target
    destination = host.state / "redirected"
    source.rename(destination)
    source.symlink_to(destination, target_is_directory=destination.is_dir())
    with pytest.raises(ValueError):
        runtime.preflight(config, pins)
    host.run.assert_not_called()


@pytest.mark.parametrize(
    "failure", ["symlink", "owner", "writable_file", "writable_parent", "secret_readable", "directory"]
)
def test_trusted_rejects_unsafe_files_and_ancestors(monkeypatch, failure):
    path = Path("/safe/config")

    def info(entry):
        mode = stat.S_IFREG | 0o600 if entry == path else stat.S_IFDIR | 0o755
        uid = 0
        if entry == path:
            if failure == "symlink":
                mode = stat.S_IFLNK | 0o777
            if failure == "owner":
                uid = 1000
            if failure == "writable_file":
                mode |= 0o020
            if failure == "secret_readable":
                mode |= 0o040
            if failure == "directory":
                mode = stat.S_IFDIR | 0o700
        if entry == path.parent and failure == "writable_parent":
            mode |= 0o002
        return SimpleNamespace(st_mode=mode, st_uid=uid)

    monkeypatch.setattr(Path, "lstat", info)
    monkeypatch.setattr(Path, "stat", info)
    with pytest.raises(ValueError):
        runtime.trusted(path, secret=True)


def test_trusted_accepts_root_owned_private_file(monkeypatch):
    path = Path("/safe/config")

    def info(entry):
        return SimpleNamespace(st_mode=(stat.S_IFREG | 0o600) if entry == path else (stat.S_IFDIR | 0o755), st_uid=0)

    monkeypatch.setattr(Path, "lstat", info)
    monkeypatch.setattr(Path, "stat", info)
    runtime.trusted(path, secret=True)


@pytest.fixture
def installed(monkeypatch, config, pins):
    contents = {
        Path("/etc/radon/gpu-host"): "radon-slm\n",
        runtime.CONFIG: json.dumps(config),
        runtime.PINS: json.dumps(pins),
        runtime.SECRETS: "VLLM_API_KEY=" + "a" * 32,
    }
    monkeypatch.setattr(Path, "read_text", lambda path: contents[path])
    monkeypatch.setattr(runtime.os, "geteuid", lambda: 0)
    trusted = Mock()
    preflight = Mock()
    run = Mock(return_value=SimpleNamespace(returncode=0))
    execute = Mock()
    monkeypatch.setattr(runtime, "trusted", trusted)
    monkeypatch.setattr(runtime, "preflight", preflight)
    monkeypatch.setattr(runtime.subprocess, "run", run)
    monkeypatch.setattr(runtime.os, "execv", execute)
    monkeypatch.setattr(runtime.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=2000, pw_gid=0))
    return SimpleNamespace(contents=contents, trusted=trusted, preflight=preflight, run=run, execute=execute)


@pytest.mark.parametrize("action", ["check", "pull", "stop", "run"])
def test_installed_actions_are_fixed_and_guarded(installed, monkeypatch, config, pins, action):
    monkeypatch.setattr(sys, "argv", ["runtime.py", action])
    assert runtime.main() == 0
    assert installed.trusted.call_args_list[0].args == (Path("/etc/radon/gpu-host"),)
    if action == "pull":
        installed.run.assert_called_once_with(["/usr/bin/docker", "pull", "--platform", "linux/amd64", pins["image"]])
        installed.preflight.assert_not_called()
    elif action == "stop":
        installed.run.assert_called_once_with(["/usr/bin/docker", "stop", "--time", "60", runtime.NAME])
        installed.preflight.assert_not_called()
    else:
        installed.preflight.assert_called_once_with(config, pins)
        installed.run.assert_not_called()
    if action == "run":
        argv = runtime.command(config, pins, 2000, 0)
        installed.execute.assert_called_once_with(argv[0], argv)
    else:
        installed.execute.assert_not_called()


def test_invalid_installed_marker_blocks_stop(installed, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["runtime.py", "stop"])
    installed.contents[Path("/etc/radon/gpu-host")] = "radon-app"
    assert runtime.main() == 78
    installed.run.assert_not_called()


def test_preflight_failure_never_executes_or_prints_secret(installed, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["runtime.py", "run"])
    installed.preflight.side_effect = subprocess.CalledProcessError(1, "docker")
    assert runtime.main() == 78
    installed.execute.assert_not_called()
    assert "a" * 32 not in capsys.readouterr().err


def test_nonroot_cannot_run_installed_commands(installed, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["runtime.py", "check"])
    monkeypatch.setattr(runtime.os, "geteuid", lambda: 1000)
    with pytest.raises(SystemExit) as exc:
        runtime.main()
    assert exc.value.code == 2
    installed.trusted.assert_not_called()

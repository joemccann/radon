#!/usr/bin/python3
"""Dedicated GPU runtime. Config is data, never a sourced shell environment."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import pwd
import re
import stat
import subprocess
import sys
from pathlib import Path

CONFIG = Path("/etc/radon/slm.json")
PINS = Path("/etc/radon/slm-pins.json")
SECRETS = Path("/etc/radon/slm-secrets.env")
STATE = Path("/var/lib/radon/slm")
NAME = "radon-slm"
TAILNET = ipaddress.ip_network("100.64.0.0/10")


def trusted(path: Path, *, secret: bool = False) -> None:
    """Reject writable/symlinked privileged files and their ancestors."""
    for entry in (path, *path.parents):
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError(f"untrusted path: {entry}")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or (secret and info.st_mode & 0o077):
        raise ValueError(f"invalid file permissions: {path}")


def validate(config: dict, pins: dict) -> None:
    if set(config) != {"tailscale_ip", "max_model_len", "gpu_memory_utilization", "adapters"}:
        raise ValueError("unknown or missing configuration fields")
    address = ipaddress.ip_address(config["tailscale_ip"])
    if address not in TAILNET:
        raise ValueError("bind address must be a Tailscale IPv4 address")
    length = config["max_model_len"]
    if type(length) is not int or not 1024 <= length <= 32768:
        raise ValueError("max_model_len must be between 1024 and 32768")
    memory = config["gpu_memory_utilization"]
    if type(memory) not in (float, int) or not 0.1 <= memory <= 0.9:
        raise ValueError("gpu_memory_utilization must be between 0.1 and 0.9")
    if not re.fullmatch(r"vllm/vllm-openai@sha256:[0-9a-f]{64}", pins["image"]):
        raise ValueError("official vLLM image must be digest-pinned")
    if pins["platform"] != "linux/amd64" or pins["model"] != "Qwen/Qwen2.5-1.5B-Instruct":
        raise ValueError("unsupported platform or base model")
    if not re.fullmatch(r"[0-9a-f]{40}", pins["model_revision"]):
        raise ValueError("model revision must be an immutable commit")
    if type(pins["minimum_driver_major"]) is not int or pins["minimum_driver_major"] < 580:
        raise ValueError("Blackwell CUDA 13 requires driver 580 or later")
    if not isinstance(config["adapters"], dict) or set(config["adapters"]) - {"radon-tagger", "radon-distill"}:
        raise ValueError("only tagger and distill adapters are supported")
    for name, value in config["adapters"].items():
        task = name.removeprefix("radon-")
        if not isinstance(value, str) or not re.fullmatch(rf"{task}/v[1-9][0-9]*", value):
            raise ValueError("adapter paths must identify immutable task/vN directories")


def validate_secrets(text: str) -> None:
    values = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or key not in {"VLLM_API_KEY", "HF_TOKEN"} or key in values:
            raise ValueError("invalid or duplicate secret field")
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,256}", value):
            raise ValueError("secret must be a literal token without quoting")
        values[key] = value
    if len(values.get("VLLM_API_KEY", "")) < 32:
        raise ValueError("VLLM_API_KEY must contain at least 32 characters")


def command(config: dict, pins: dict, uid: int, gid: int) -> list[str]:
    validate(config, pins)
    argv = [
        "/usr/bin/docker",
        "run",
        "--rm",
        "--name",
        NAME,
        "--pull=never",
        "--platform",
        "linux/amd64",
        "--gpus",
        "all",
        "--network",
        "host",
        "--user",
        f"{uid}:{gid}",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--shm-size",
        "2g",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=4g",
        "--env-file",
        str(SECRETS),
        "--env",
        "HOME=/cache",
        "--env",
        "HF_HOME=/cache/huggingface",
        "--env",
        "XDG_CACHE_HOME=/cache",
        "--env",
        "VLLM_CACHE_ROOT=/cache/vllm",
        "--env",
        "VLLM_ALLOW_RUNTIME_LORA_UPDATING=False",
        "--workdir",
        "/cache",
        "--mount",
        f"type=bind,src={STATE}/cache,dst=/cache",
        "--mount",
        f"type=bind,src={STATE}/adapters,dst=/adapters,readonly",
        pins["image"],
        pins["model"],
        "--revision",
        pins["model_revision"],
        "--tokenizer-revision",
        pins["model_revision"],
        "--host",
        config["tailscale_ip"],
        "--port",
        "8350",
        "--dtype",
        "bfloat16",
        "--max-model-len",
        str(config["max_model_len"]),
        "--gpu-memory-utilization",
        str(config["gpu_memory_utilization"]),
        "--enable-lora",
        "--max-lora-rank",
        "16",
        "--max-loras",
        "2",
        "--disable-log-requests",
    ]
    if config["adapters"]:
        argv.append("--lora-modules")
        argv.extend(f"{name}=/adapters/{path}" for name, path in sorted(config["adapters"].items()))
    return argv


def preflight(config: dict, pins: dict) -> None:
    addresses = subprocess.check_output(["/usr/bin/tailscale", "ip", "-4"], text=True).splitlines()
    if config["tailscale_ip"] not in addresses:
        raise ValueError("configured address is not assigned to this Tailscale node")
    drivers = subprocess.check_output(
        ["/usr/bin/nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"], text=True
    ).splitlines()
    if not drivers or any(int(v.split(".")[0]) < pins["minimum_driver_major"] for v in drivers):
        raise ValueError("installed NVIDIA driver does not satisfy the runtime pin")
    for relative in config["adapters"].values():
        adapter = STATE / "adapters" / relative
        if adapter.is_symlink() or adapter.resolve() != adapter:
            raise ValueError("adapter versions must not be symlinks")
        for name in ("adapter_config.json", "adapter_model.safetensors"):
            artifact = adapter / name
            if not artifact.is_file() or artifact.is_symlink():
                raise ValueError(f"incomplete or symlinked adapter: {relative}")
            trusted(artifact)
    subprocess.run(["/usr/bin/docker", "image", "inspect", pins["image"]], check=True, stdout=subprocess.DEVNULL)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "pull", "run", "stop"])
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("run as root on the dedicated GPU host")
    # Fixed paths and verbs: no user-selected env files or Docker arguments.
    try:
        trusted(Path("/etc/radon/gpu-host"))
        if Path("/etc/radon/gpu-host").read_text().strip() != "radon-slm":
            raise ValueError("missing dedicated GPU host marker")
        if args.action == "stop":
            return subprocess.run(["/usr/bin/docker", "stop", "--time", "60", NAME]).returncode
        trusted(CONFIG)
        trusted(PINS)
        trusted(SECRETS, secret=True)
        config = json.loads(CONFIG.read_text())
        pins = json.loads(PINS.read_text())
        validate(config, pins)
        validate_secrets(SECRETS.read_text())
        if args.action == "pull":
            return subprocess.run(["/usr/bin/docker", "pull", "--platform", "linux/amd64", pins["image"]]).returncode
        preflight(config, pins)
        account = pwd.getpwnam("radon")
        argv = command(config, pins, account.pw_uid, account.pw_gid)
        if args.action == "check":
            print("GPU runtime preflight passed; service remains unchanged")
            return 0
        os.execv(argv[0], argv)
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError):
        print(
            "GPU runtime refused: check trusted configuration, secrets, GPU, tailnet, image and adapters",
            file=sys.stderr,
        )
        return 78
    return 0


if __name__ == "__main__":
    sys.exit(main())

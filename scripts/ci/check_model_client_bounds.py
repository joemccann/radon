#!/usr/bin/env python3
"""Fail CI when the exact gateway image's client exports exceed the pinned
production disk bounds.

Production admission (cloud/scripts/model_clients.py check-space) charges
LINUX_MAX_PAYLOAD_BYTES / LINUX_MAX_MEMBERS / LINUX_MAX_STREAM_BYTES and
DARWIN_MAX_EXPORT_BYTES, and the same module refuses any export past them.
This runs the production export code against the freshly built image, so a
bundle that outgrows its pin turns this job red instead of failing a deploy.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL_CLIENTS = ROOT / "cloud/scripts/model_clients.py"
# The production 60 s export budget is a host-side liveness guard, not a disk
# bound; a cold ~1.6 GB export on a shared runner can exceed it.
CI_EXPORT_DEADLINE_SECONDS = 300
_PIN_HINT = (
    "Raise the pins in cloud/scripts/model_clients.py (and the documented "
    "admission figure) only after confirming production disk headroom."
)


def load_model_clients():
    spec = importlib.util.spec_from_file_location("model_clients", MODEL_CLIENTS)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load cloud/scripts/model_clients.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def measure(engine: str, image: str, scratch: str | None = None,
            deadline_seconds: float = CI_EXPORT_DEADLINE_SECONDS) -> dict:
    api = load_model_clients()
    with tempfile.TemporaryFile(dir=scratch) as stream:
        # Enforces LINUX_MAX_STREAM_BYTES exactly as production does.
        api.export_from_image(engine, image, stream, deadline_seconds=deadline_seconds)
        stream_bytes = stream.seek(0, 2)
        stream.seek(0)
        members = payload = 0
        with tarfile.open(fileobj=stream, mode="r|") as archive:
            for member in archive:
                members += 1
                payload += max(0, member.size)
    if members > api.LINUX_MAX_MEMBERS or payload > api.LINUX_MAX_PAYLOAD_BYTES:
        raise ValueError(f"Linux client archive exceeds bounds: {members} members / {payload} bytes")
    with tempfile.TemporaryFile(dir=scratch) as stream:
        # Enforces DARWIN_MAX_EXPORT_BYTES; _darwin_payload enforces members.
        api.export_from_image(engine, image, stream, darwin=True, deadline_seconds=deadline_seconds)
        darwin_bytes = stream.seek(0, 2)
        api._darwin_payload(stream)
    return {
        "linux": {"members": members, "max_members": api.LINUX_MAX_MEMBERS,
                  "payload_bytes": payload, "max_payload_bytes": api.LINUX_MAX_PAYLOAD_BYTES,
                  "stream_bytes": stream_bytes, "max_stream_bytes": api.LINUX_MAX_STREAM_BYTES},
        "darwin": {"bytes": darwin_bytes, "max_bytes": api.DARWIN_MAX_EXPORT_BYTES},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", default="docker")
    parser.add_argument("--image", required=True)
    parser.add_argument("--scratch", default=None)
    parser.add_argument("--export-deadline", type=float, default=CI_EXPORT_DEADLINE_SECONDS)
    args = parser.parse_args(argv)
    try:
        result = measure(args.engine, args.image, args.scratch, args.export_deadline)
    except Exception as exc:  # noqa: BLE001 - every refusal is a CI failure
        detail = f"{type(exc).__name__}: {exc}."
        if isinstance(exc, ValueError) and "exceeds bounds" in str(exc):
            detail += " " + _PIN_HINT
        print(f"::error title=model client disk bounds::{detail}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

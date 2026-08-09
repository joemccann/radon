"""Atomic JSON I/O with SHA-256 checksum verification.

Provides crash-safe writes for JSON payload files via temp file + os.replace()
and embedded checksum integrity verification.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path


def _compute_checksum(data: dict) -> str:
    """Compute SHA-256 hex digest of canonical JSON (no _checksum field)."""
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def atomic_save(path: str, data: dict) -> str:
    """Write JSON atomically via temp file + os.replace(), with SHA-256 checksum.

    The checksum is computed on the payload WITHOUT the _checksum field,
    then embedded as data["_checksum"] in the written file.

    Args:
        path: Destination file path.
        data: Dict to serialize as JSON.

    Returns:
        The SHA-256 hex digest (64-char string).

    Raises:
        TypeError: If data contains non-serializable values.
    """
    # Work on a copy so we don't mutate the caller's dict
    payload = {k: v for k, v in data.items() if k != "_checksum"}
    checksum = _compute_checksum(payload)
    payload["_checksum"] = checksum

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Write to a temp file in the same directory (same filesystem for os.replace)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(target.parent), suffix=".tmp", prefix=".atomic_"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
            # R-031: without fsync, a power loss can persist the rename
            # while the content is still in the page cache — an empty or
            # torn file behind a "successful" atomic save.
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(target))
        dir_fd = os.open(str(target.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        # Clean up temp file on any failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return checksum


def verified_load(path: str) -> dict:
    """Load JSON and verify SHA-256 checksum integrity.

    - If the file has a _checksum field, verifies it matches the payload.
    - If the file has no _checksum (legacy), returns data as-is.
    - The _checksum field is always stripped from the returned dict.

    Args:
        path: File path to load.

    Returns:
        The deserialized dict (without _checksum).

    Raises:
        FileNotFoundError: If the file doesn't exist.
        ValueError: If the checksum doesn't match (data corruption).
        json.JSONDecodeError: If the file is not valid JSON.
    """
    with open(path, "r") as f:
        data = json.load(f)

    stored_checksum = data.pop("_checksum", None)
    if stored_checksum is None:
        # Legacy file without checksum — return as-is
        return data

    expected = _compute_checksum(data)
    if stored_checksum != expected:
        raise ValueError(
            f"Checksum mismatch in {path}: "
            f"stored={stored_checksum}, computed={expected}. "
            f"File may be corrupted."
        )

    return data

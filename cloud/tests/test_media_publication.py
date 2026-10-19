"""Public media publication uses a separate edge-owned storage boundary."""
from __future__ import annotations

import importlib.util
import os
import binascii
import struct
import zlib
from pathlib import Path

import pytest

CLOUD = Path(__file__).resolve().parents[1]
SCRIPT = CLOUD / "scripts" / "media_publish.py"
def png_chunk(kind, payload):
    return len(payload).to_bytes(4, "big") + kind + payload + binascii.crc32(kind + payload).to_bytes(4, "big")


def png_fixture(width=1, height=1):
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", header)
            + png_chunk(b"IDAT", zlib.compress(b"\0\0\0\0\0")) + png_chunk(b"IEND", b""))


PNG = png_fixture()


def publisher():
    spec = importlib.util.spec_from_file_location("media_publish", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def roots(tmp_path):
    source = tmp_path / "staging"
    dest = tmp_path / "public"
    source.mkdir()
    dest.mkdir(mode=0o750)
    return source, dest


def run(module, source, dest, **kwargs):
    return module.publish(source, dest, source_uid=os.getuid(), public_uid=os.getuid(), public_gid=os.getgid(), **kwargs)


def test_caddy_serves_only_published_media():
    caddy = (CLOUD / "caddy" / "Caddyfile").read_text()
    assert "root * /var/lib/radon-public/media" in caddy
    assert "root * /var/lib/radon/media\n" not in caddy


def test_initial_migration_preserves_names_and_bytes(tmp_path):
    m = publisher()
    source, dest = roots(tmp_path)
    (source / "existing-news.png").write_bytes(PNG)
    result = run(m, source, dest)
    assert result["copied"] == 1
    assert (dest / "existing-news.png").read_bytes() == PNG
    assert (dest / "existing-news.png").stat().st_mode & 0o777 == 0o640
    assert run(m, source, dest)["copied"] == 0


@pytest.mark.parametrize("shape", ("symlink", "hardlink", "directory", "fifo", "wrong-content", "unsupported"))
def test_rejects_unapproved_source_entries(tmp_path, shape):
    m = publisher()
    source, dest = roots(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(PNG)
    target = source / ("image.svg" if shape == "unsupported" else "image.png")
    if shape == "symlink": target.symlink_to(outside)
    elif shape == "hardlink": os.link(outside, target)
    elif shape == "directory": target.mkdir()
    elif shape == "fifo": os.mkfifo(target)
    else: target.write_bytes(b"private text" if shape == "wrong-content" else PNG)
    assert run(m, source, dest)["rejected"] == 1
    assert not list(dest.iterdir())


@pytest.mark.parametrize("component", ("source", "destination", "source-ancestor", "destination-ancestor"))
def test_directory_links_are_rejected(tmp_path, component):
    m = publisher()
    source, dest = roots(tmp_path)
    if component in ("source", "destination"):
        original = source if component == "source" else dest
        original.rename(original.with_name(original.name + "-real"))
        original.symlink_to(original.with_name(original.name + "-real"), target_is_directory=True)
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path, target_is_directory=True)
        if component == "source-ancestor": source = alias / source.name
        else: dest = alias / dest.name
    with pytest.raises((OSError, ValueError)):
        run(m, source, dest)


def test_source_mutation_during_read_does_not_publish(tmp_path, monkeypatch):
    m = publisher()
    source, dest = roots(tmp_path)
    path = source / "image.png"
    path.write_bytes(PNG)
    original = m.os.read
    changed = False
    def racing_read(fd, size):
        nonlocal changed
        result = original(fd, size)
        if not changed:
            changed = True
            path.write_bytes(PNG + b"changed")
        return result
    monkeypatch.setattr(m.os, "read", racing_read)
    assert run(m, source, dest)["rejected"] == 1
    assert not list(dest.iterdir())


def test_failed_source_scan_keeps_published_assets(tmp_path):
    m = publisher()
    source, dest = roots(tmp_path)
    (source / "image.png").write_bytes(PNG)
    run(m, source, dest)
    source.rename(tmp_path / "gone")
    with pytest.raises(OSError): run(m, source, dest)
    assert (dest / "image.png").read_bytes() == PNG


def test_explicit_removal_is_mirrored_only_after_successful_scan(tmp_path):
    m = publisher()
    source, dest = roots(tmp_path)
    image = source / "image.png"
    image.write_bytes(PNG)
    run(m, source, dest)
    image.unlink()
    assert run(m, source, dest)["removed"] == 1
    assert not list(dest.iterdir())


def test_bounded_size_rejection_preserves_last_good_asset(tmp_path):
    m = publisher()
    source, dest = roots(tmp_path)
    image = source / "image.png"
    image.write_bytes(PNG)
    run(m, source, dest)
    image.write_bytes(PNG + b"x" * 1024)
    result = run(m, source, dest, max_bytes=100)
    assert result["rejected"] == 1
    assert (dest / "image.png").read_bytes() == PNG


def test_source_swap_after_open_never_reads_replacement(tmp_path, monkeypatch):
    m = publisher()
    source, dest = roots(tmp_path)
    (source / "image.png").write_bytes(PNG)
    victim = tmp_path / "private"
    victim.mkdir()
    (victim / "image.png").write_bytes(b"private material")
    original = m.os.read
    swapped = False
    def swap(fd, size):
        nonlocal swapped
        if not swapped:
            swapped = True
            source.rename(tmp_path / "original-staging")
            source.symlink_to(victim, target_is_directory=True)
        return original(fd, size)
    monkeypatch.setattr(m.os, "read", swap)
    with pytest.raises((OSError, ValueError)):
        run(m, source, dest)
    assert not any(b"private material" in item.read_bytes() for item in dest.iterdir())


def test_destination_link_is_not_followed(tmp_path):
    m = publisher()
    source, dest = roots(tmp_path)
    (source / "image.png").write_bytes(PNG)
    victim = tmp_path / "private"
    victim.write_bytes(b"unchanged")
    (dest / "image.png").symlink_to(victim)
    with pytest.raises((OSError, ValueError)):
        run(m, source, dest)
    assert victim.read_bytes() == b"unchanged"


def test_published_directory_must_not_be_writable_by_other_users(tmp_path):
    m = publisher()
    source, dest = roots(tmp_path)
    dest.chmod(0o777)
    with pytest.raises(ValueError): run(m, source, dest)


def test_unit_is_root_owned_credential_free_and_bounded():
    service = (CLOUD / "services" / "radon-media-publish.service").read_text()
    assert "User=root" in service
    assert "EnvironmentFile=" not in service
    assert "ExecStart=/usr/bin/python3 -I /usr/local/lib/radon/media_publish.py" in service
    assert "ProtectSystem=strict" in service
    assert "ReadWritePaths=/var/lib/radon-public" in service
    assert "TimeoutStartSec=" in service
    assert "PrivateNetwork=true" in service


def test_cached_unchanged_file_does_not_reopen_payload(tmp_path, monkeypatch):
    m = publisher()
    source, dest = roots(tmp_path)
    (source / "image.png").write_bytes(PNG)
    cache = {}
    run(m, source, dest, cache=cache)
    def forbidden(*args): raise AssertionError("unchanged image bytes must not be read")
    monkeypatch.setattr(m.os, "read", forbidden)
    assert run(m, source, dest, cache=cache)["unchanged"] == 1


def test_batched_backfill_resumes_without_recopying_completed_files(tmp_path):
    m = publisher()
    source, dest = roots(tmp_path)
    for n in range(3): (source / f"image-{n}.png").write_bytes(PNG)
    cache = {}
    for remaining in (2, 1, 0):
        result = run(m, source, dest, cache=cache, max_total_bytes=len(PNG) + 1)
        assert result["copied"] == 1
        assert result["deferred"] == remaining
    assert len(list(dest.iterdir())) == 3


def test_source_enumeration_bound_is_checked_before_public_mutation(tmp_path):
    m = publisher()
    source, dest = roots(tmp_path)
    (source / "existing.png").write_bytes(PNG)
    run(m, source, dest)
    for n in range(3): (source / f"image-{n}.png").write_bytes(PNG)
    with pytest.raises(ValueError): run(m, source, dest, max_entries=2)
    assert [p.name for p in dest.iterdir()] == ["existing.png"]


def test_old_source_leaf_survives_initial_backfill_and_refresh(tmp_path):
    m = publisher()
    source, dest = roots(tmp_path)
    old = source / "still-served.png"
    old.write_bytes(PNG)
    old_timestamp = 1_600_000_000
    os.utime(old, (old_timestamp, old_timestamp))
    cache = {}
    assert run(m, source, dest, cache=cache)["copied"] == 1
    published = dest / old.name
    assert published.read_bytes() == PNG
    assert published.stat().st_mtime == old_timestamp
    assert run(m, source, dest, cache=cache)["removed"] == 0
    assert run(m, source, dest)["removed"] == 0
    assert published.read_bytes() == PNG


def test_source_overcount_refuses_before_copies_or_removals(tmp_path):
    m = publisher()
    source, dest = roots(tmp_path)
    existing = source / "existing.png"
    existing.write_bytes(PNG)
    cache = {}
    run(m, source, dest, cache=cache)
    before = {item.name: item.read_bytes() for item in dest.iterdir()}
    previous_cache = dict(cache)
    for n in range(3):
        image = source / f"new-{n}.png"
        image.write_bytes(PNG + bytes([n]))
    with pytest.raises(ValueError, match="count"):
        run(m, source, dest, cache=cache, max_files=2)
    assert {item.name: item.read_bytes() for item in dest.iterdir()} == before
    assert cache == previous_cache


def test_fixed_entrypoint_rejects_unprivileged_or_argument_calls(monkeypatch):
    m = publisher()
    monkeypatch.setattr(m.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(m.sys, "argv", ["media_publish.py"])
    assert m.main() == 64
    monkeypatch.setattr(m.os, "geteuid", lambda: 0)
    monkeypatch.setattr(m.sys, "argv", ["media_publish.py", "--source=/private"])
    assert m.main() == 64


def test_transient_staging_artifacts_are_never_published(tmp_path):
    m = publisher()
    source, dest = roots(tmp_path)
    for name in (".private", "image.png.tmp", "image.png.part", "image.download"):
        (source / name).write_bytes(b"temporary bytes")
    result = run(m, source, dest)
    assert result["rejected"] == 0
    assert not list(dest.iterdir())


@pytest.mark.parametrize("outcomes,expected,attempts", (([75, 75, 0], 0, 3), ([1], 1, 1), ([75] * 20, 75, 20)))
def test_initial_backfill_drains_before_watchers_are_enabled(tmp_path, outcomes, expected, attempts):
    import re
    import shlex
    import subprocess
    import sys
    source = (CLOUD / "scripts" / "deploy-root-helper.sh").read_text()
    function = re.search(r"^prepare_public_media_publication\(\) \{.*?^\}", source, re.M | re.S).group()
    counter = tmp_path / "calls"
    stub = tmp_path / "publisher.py"
    stub.write_text(
        f"from pathlib import Path\nimport sys\np=Path({str(counter)!r})\n"
        "n=int(p.read_text()) if p.exists() else 0\np.write_text(str(n+1))\n"
        f"values={outcomes!r}\nsys.exit(values[min(n,len(values)-1)])\n"
    )
    calls = tmp_path / "systemctl"
    ctl = tmp_path / "ctl"
    ctl.write_text(f"#!/bin/bash\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n")
    ctl.chmod(0o755)
    command = (
        "set -uo pipefail\nHELPER_TEST_MODE=1\n"
        f"SESSION_PYTHON={shlex.quote(sys.executable)}\n"
        f"RADON_TEST_MEDIA_PUBLISHER={shlex.quote(str(stub))}\n"
        f"SYSTEMCTL={shlex.quote(str(ctl))}\n{function}\nprepare_public_media_publication\n"
    )
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
    assert result.returncode == expected, result.stderr
    assert int(counter.read_text()) == attempts
    assert calls.exists() == (expected == 0)
    if calls.exists(): assert "enable --now radon-media-publish.path radon-media-publish.timer" in calls.read_text()


def test_edge_switch_follows_successful_initial_backfill():
    source = (CLOUD / "scripts" / "deploy-root-helper.sh").read_text()
    publish = source.split("\npublish_caddy() {", 1)[1].split("\n}\n", 1)[0]
    assert publish.index("prepare_public_media_publication") < publish.index('mv -f -- "$candidate" "$CADDY_CONFIG"')
    setup = (CLOUD / "scripts" / "setup-vps.sh").read_text().split("\nmain() {", 1)[1]
    assert setup.index("install_media_publication") < setup.index("configure_caddy")


def test_initial_oversize_never_opens_or_reads_payload(tmp_path, monkeypatch):
    m = publisher()
    source, dest = roots(tmp_path)
    (source / "oversized.png").write_bytes(PNG + b"x" * 1024)
    original = m.os.open

    def guarded_open(name, *args, **kwargs):
        assert name != "oversized.png", "initially oversized leaves must not be opened"
        return original(name, *args, **kwargs)

    monkeypatch.setattr(m.os, "open", guarded_open)
    assert run(m, source, dest, max_bytes=len(PNG))["rejected"] == 1
    assert not list(dest.iterdir())


@pytest.mark.parametrize("failure", ["invalid-magic", "read-error", "growth-error"])
def test_failed_read_attempts_consume_the_batch_budget(tmp_path, monkeypatch, failure):
    m = publisher()
    source, dest = roots(tmp_path)
    for n in range(4):
        path = source / f"image-{n}.png"
        path.write_bytes(b"x" * len(PNG) if failure == "invalid-magic" else PNG)
    original = m._read
    attempts = []

    def attempted(fd, limit):
        attempts.append(limit)
        if failure == "read-error":
            m.os.read(fd, limit)
            raise OSError("synthetic read error after bounded I/O")
        if failure == "growth-error":
            m.os.read(fd, limit)
            raise ValueError("synthetic growth after bounded I/O")
        return original(fd, limit)

    monkeypatch.setattr(m, "_read", attempted)
    result = run(m, source, dest, max_total_bytes=2 * (len(PNG) + 1))
    assert attempts == [len(PNG), len(PNG)]
    assert result["rejected"] == 2 and result["deferred"] == 2
    assert not list(dest.iterdir())


def test_destination_comparison_is_reserved_in_the_same_read_budget(tmp_path, monkeypatch):
    m = publisher()
    source, dest = roots(tmp_path)
    for n in range(2):
        (source / f"image-{n}.png").write_bytes(PNG)
    run(m, source, dest)
    original = m._read
    attempts = []

    def counted(fd, limit):
        attempts.append(limit)
        return original(fd, limit)

    monkeypatch.setattr(m, "_read", counted)
    result = run(m, source, dest, max_total_bytes=2 * (len(PNG) + 1))
    assert attempts == [len(PNG), len(PNG)]
    assert result["unchanged"] == 1 and result["deferred"] == 1


@pytest.mark.parametrize("payload", [
    b"\x89PNG\r\n\x1a\nprivate text",
    png_fixture(0, 1), png_fixture(1, 0), png_fixture(4001, 4000),
    PNG[:-1], PNG + b"trailing text", PNG[:40] + bytes([PNG[40] ^ 1]) + PNG[41:],
    b"\xff\xd8\xfflegacy jpeg",
])
def test_rejects_malformed_or_out_of_bounds_canonical_png(tmp_path, payload):
    m = publisher()
    source, dest = roots(tmp_path)
    path = source / "image.png"
    path.write_bytes(PNG)
    run(m, source, dest)
    path.write_bytes(payload)
    result = run(m, source, dest)
    assert result["rejected"] == 1
    assert (dest / path.name).read_bytes() == PNG


def test_noncanonical_extension_is_not_promoted_even_with_png_bytes(tmp_path):
    m = publisher()
    source, dest = roots(tmp_path)
    (source / "image.webp").write_bytes(PNG)
    assert run(m, source, dest)["rejected"] == 1
    assert not list(dest.iterdir())

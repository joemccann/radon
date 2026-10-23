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


@pytest.mark.parametrize("entrypoint", ["helper", "setup"])
@pytest.mark.parametrize("outcomes,expected,attempts", (([75, 75, 0], 0, 3), ([1], 1, 1), ([75] * 20, 75, 20)))
def test_initial_backfill_retires_path_and_drains_before_timer_is_enabled(tmp_path, entrypoint, outcomes, expected, attempts):
    import re
    import shlex
    import subprocess
    import sys
    filename, name = ("deploy-root-helper.sh", "prepare_public_media_publication") if entrypoint == "helper" else ("setup-vps.sh", "install_media_publication")
    source = (CLOUD / "scripts" / filename).read_text()
    function = re.search(r"^" + name + r"\(\) \{.*?^\}", source, re.M | re.S).group()
    if entrypoint == "setup":
        # Only fixed executable data is remapped; execute the actual setup body.
        function = function.replace('/usr/bin/python3 -I /usr/local/lib/radon/media_publish.py', '"$SESSION_PYTHON" -I "$RADON_TEST_MEDIA_PUBLISHER"')
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
        f"SYSTEMCTL={shlex.quote(str(ctl))}\n"
        f"CLOUD_DIR={shlex.quote(str(CLOUD))}\n"
        'systemctl() { "$SYSTEMCTL" "$@"; }\n'
        'stage_from_checkout() { :; }; log_error() { :; }\n'
        f"{function}\n{name}\n"
    )
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
    assert result.returncode == expected, result.stderr
    assert int(counter.read_text()) == attempts
    assert calls.exists()
    operations = [line for line in calls.read_text().splitlines() if line != "daemon-reload"]
    assert operations[0] == "disable --now radon-media-publish.path"
    if expected == 0:
        assert operations[-1] == "enable --now radon-media-publish.timer"
        assert any(line.startswith("reset-failed ") and "radon-media-publish.service" in line for line in operations)
    else:
        assert operations == ["disable --now radon-media-publish.path"]
    assert not any(line.startswith("enable ") and "radon-media-publish.path" in line for line in operations)


@pytest.mark.parametrize("entrypoint", ["helper", "setup"])
def test_backfill_tolerates_garbage_collected_path_unit(tmp_path, entrypoint):
    # systemd drops a disabled, unreferenced path unit from memory, so every
    # later reset-failed on it exits 1; that must not block the edge publish.
    import re
    import shlex
    import subprocess
    import sys
    filename, name = ("deploy-root-helper.sh", "prepare_public_media_publication") if entrypoint == "helper" else ("setup-vps.sh", "install_media_publication")
    source = (CLOUD / "scripts" / filename).read_text()
    function = re.search(r"^" + name + r"\(\) \{.*?^\}", source, re.M | re.S).group()
    if entrypoint == "setup":
        function = function.replace('/usr/bin/python3 -I /usr/local/lib/radon/media_publish.py', '"$SESSION_PYTHON" -I "$RADON_TEST_MEDIA_PUBLISHER"')
    stub = tmp_path / "publisher.py"
    stub.write_text("import sys\nsys.exit(0)\n")
    calls = tmp_path / "systemctl"
    ctl = tmp_path / "ctl"
    ctl.write_text(
        f"#!/bin/bash\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n"
        'if [[ "$*" == *reset-failed* && "$*" == *radon-media-publish.path* ]]; then\n'
        '  echo "Failed to reset failed state of unit radon-media-publish.path: Unit radon-media-publish.path not loaded." >&2\n'
        "  exit 1\nfi\n"
    )
    ctl.chmod(0o755)
    command = (
        "set -uo pipefail\nHELPER_TEST_MODE=1\n"
        f"SESSION_PYTHON={shlex.quote(sys.executable)}\n"
        f"RADON_TEST_MEDIA_PUBLISHER={shlex.quote(str(stub))}\n"
        f"SYSTEMCTL={shlex.quote(str(ctl))}\n"
        f"CLOUD_DIR={shlex.quote(str(CLOUD))}\n"
        'systemctl() { "$SYSTEMCTL" "$@"; }\n'
        'stage_from_checkout() { :; }; log_error() { :; }\n'
        f"{function}\n{name}\n"
    )
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    operations = [line for line in calls.read_text().splitlines() if line != "daemon-reload"]
    assert operations[-1] == "enable --now radon-media-publish.timer"
    assert any(line.startswith("reset-failed ") and "radon-media-publish.service" in line for line in operations)


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


def test_setup_boot_enable_does_not_reactivate_retired_media_path(tmp_path):
    import re
    import shlex
    import subprocess
    source = (CLOUD / "scripts/setup-vps.sh").read_text()
    function = re.search(r"^enable_services\(\) \{.*?^\}", source, re.M | re.S).group()
    calls = tmp_path / "systemctl-calls"
    command = (
        "set -euo pipefail\n"
        "log_info() { :; }; log_success() { :; }\n"
        f"systemctl() {{ printf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}; }}\n"
        "SERVICE_FILES=(radon-media-publish.service radon-media-publish.path radon-media-publish.timer)\n"
        f"CLOUD_DIR={shlex.quote(str(CLOUD))}\n{function}\nenable_services\n"
    )
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    enabled = [line.split()[1:] for line in calls.read_text().splitlines() if line.startswith("enable ")]
    assert any("radon-media-publish.timer" in units for units in enabled)
    assert all("radon-media-publish.path" not in units for units in enabled)


def test_real_systemd_recovery_retires_burst_path_and_keeps_timer_publication():
    """Cloud opt-in exercises the actual service limits, sandbox and helper body."""
    import json
    import re
    import shlex
    import subprocess
    import time
    import uuid

    if os.environ.get("RADON_TEST_REAL_MEDIA_SYSTEMD") != "1":
        pytest.skip("real systemd fixture runs in the mandatory private cloud job")
    assert Path("/proc/1/comm").read_text().strip() == "systemd", "cloud job requires systemd PID 1"
    subprocess.run(["sudo", "-n", "true"], check=True, timeout=10)
    prefix = "radon-media-test-" + uuid.uuid4().hex
    data = Path("/var/lib") / prefix
    unit_dir = Path("/run/systemd/system")
    names = {kind: prefix + "." + kind for kind in ("service", "path", "timer")}

    def ctl(*args, check=True):
        return subprocess.run(["sudo", "-n", "systemctl", *args], check=check,
                              capture_output=True, text=True, timeout=20)

    def root_script(script):
        return subprocess.run(["sudo", "-n", "/usr/bin/python3", "-I", "-"], input=script,
                              text=True, capture_output=True, check=True, timeout=20)

    def state(kind, prop):
        return ctl("show", names[kind], "--property=" + prop, "--value").stdout.strip()

    def count():
        value = data / "public" / "count"
        return int(value.read_text()) if value.exists() else 0

    def until(predicate, seconds, label):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.1)
        pytest.fail(label + ": " + json.dumps({k: {p: state(k, p) for p in
                    ("ActiveState", "SubState", "Result")} for k in names}))

    fixture = ("from pathlib import Path\n"
               f"base=Path({str(data)!r})\n"
               "counter=base/'public'/'count'\n"
               "n=int(counter.read_text()) if counter.exists() else 0\n"
               "pending=counter.with_suffix('.new')\n"
               "pending.write_text(str(n+1)); pending.chmod(0o644); pending.replace(counter)\n"
               "source=base/'staging'/'value'\n"
               "if source.exists():\n"
               "    output=base/'public'/'value'; output.write_text(source.read_text()); output.chmod(0o644)\n")
    units = {}
    for kind in names:
        text = (CLOUD / "services" / ("radon-media-publish." + kind)).read_text()
        text = text.replace("radon-media-publish.service", names["service"])
        text = text.replace("/var/lib/radon/media", str(data / "staging"))
        text = text.replace("/var/lib/radon-public", str(data / "public"))
        text = text.replace("/usr/local/lib/radon/media_publish.py", str(data / "publisher.py"))
        units[kind] = text
    try:
        root_script(f"from pathlib import Path\np=Path({str(data)!r})\np.mkdir(mode=0o755)\n"
                    "(p/'staging').mkdir(mode=0o755)\n(p/'public').mkdir(mode=0o755)\n"
                    f"(p/'publisher.py').write_text({fixture!r})\n" + "".join(
                        f"Path({str(unit_dir / names[kind])!r}).write_text({text!r})\n"
                        for kind, text in units.items()))
        ctl("daemon-reload")
        ctl("enable", "--now", names["path"])
        # Deliberately reproduce production: ten successful fast path starts,
        # then another write trips the retained 10-per-60s service limiter.
        for n in range(10):
            root_script(f"from pathlib import Path\nPath({str(data / 'staging' / ('trigger-'+str(n)))!r}).write_text({str(n)!r})\n")
            until(lambda: count() >= n + 1 and state("service", "ActiveState") == "inactive",
                  5, "legacy path did not complete each successful invocation")
        root_script(f"from pathlib import Path\nPath({str(data / 'staging' / 'trigger-limit')!r}).write_text('limit')\n")
        until(lambda: state("service", "Result") == "start-limit-hit" and
              state("path", "Result") == "unit-start-limit-hit", 5,
              "legacy schedule did not reproduce the real production latch")
        print("REAL_SYSTEMD_LEGACY_REPRODUCED: 10 successes; service=start-limit-hit; path=unit-start-limit-hit", flush=True)
        source = (CLOUD / "scripts/deploy-root-helper.sh").read_text()
        function = re.search(r"^prepare_public_media_publication\(\) \{.*?^\}", source, re.M | re.S).group()
        # The actual helper body runs unchanged. Only its documented test
        # executable and fixed systemctl unit identities are fixture-bound.
        mapping = "case \"$arg\" in " + " ".join(
            f"radon-media-publish.{kind}) mapped+=({shlex.quote(name)});;"
            for kind, name in names.items()) + ' *) mapped+=("$arg");; esac'
        wrapper = data / "ctl"
        wrapper_text = '#!/bin/bash\nset -euo pipefail\nmapped=()\nfor arg; do ' + mapping + '\ndone\nexec /usr/bin/systemctl "${mapped[@]}"\n'
        root_script(f"from pathlib import Path\np=Path({str(wrapper)!r})\np.write_text({wrapper_text!r})\np.chmod(0o755)\n")
        command = ("set -euo pipefail\nHELPER_TEST_MODE=1\nSESSION_PYTHON=/usr/bin/python3\n"
                   f"RADON_TEST_MEDIA_PUBLISHER={shlex.quote(str(data / 'publisher.py'))}\n"
                   f"SYSTEMCTL={shlex.quote(str(wrapper))}\n{function}\nprepare_public_media_publication\n")
        result = subprocess.run(["sudo", "-n", "bash", "-c", command], capture_output=True, text=True, timeout=25)
        assert result.returncode == 0, result.stderr
        assert state("path", "ActiveState") == "inactive", "retired path must be stopped"
        assert state("path", "UnitFileState") == "disabled", "retired path must stay disabled on boot"
        assert state("path", "Result") == "success", "retired path failure must be reset"
        assert state("timer", "ActiveState") == "active"
        after_backfill = 11  # ten systemd starts plus the helper's direct backfill
        until(lambda: state("timer", "LastTriggerUSecMonotonic") not in ("", "0", "0us", "n/a")
              and count() >= after_backfill + 1 and state("service", "ActiveState") == "inactive",
              6, "first actual timer invocation must complete before burst control")
        first = count()
        root_script(f"from pathlib import Path\np=Path({str(data / 'staging' / 'value')!r})\n"
                    "for n in range(100): p.write_text('burst-'+str(n))\n")
        time.sleep(2)
        assert count() == first, "burst writes must not trigger publisher starts"
        until(lambda: count() >= first + 3 and state("service", "ActiveState") == "inactive",
              56, "timer must complete three subsequent publications")
        assert (data / "public" / "value").read_text() == "burst-99"
        assert state("service", "Result") == "success"
        assert state("path", "ActiveState") == "inactive"
        assert state("timer", "ActiveState") == "active"
        print("REAL_SYSTEMD_RECOVERED: retired path disabled/inactive; timer completed 3 later publications; burst ignored", flush=True)
    finally:
        ctl("disable", "--now", names["path"], names["timer"], check=False)
        ctl("stop", names["service"], check=False)
        ctl("reset-failed", *names.values(), check=False)
        root_script("from pathlib import Path\nimport shutil\n" + "".join(
            f"Path({str(unit_dir / name)!r}).unlink(missing_ok=True)\n" for name in names.values()) +
            f"shutil.rmtree({str(data)!r}, ignore_errors=True)\n")
        ctl("daemon-reload")

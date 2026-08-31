"""R-381 / REL-134: the app containers do not get the control plane's state dir.

`radon-app-runtime.sh` mounted `-v "${STATE_DIR}:/var/lib/radon"` — the WHOLE
directory, read-write, into a container running as uid `radon`. That directory
holds `control-plane-manifest.sha256` and `control-plane-ready` (the readiness
gate `deploy.sh` refuses to deploy without), the root deploy transaction journal
at `deploy/active-units`, and the IB 2FA lease. Write permission on the PARENT
is all `unlink`/`rename` needs, so the newsfeed container — headless Chromium,
`--ipc host`, `PLAYWRIGHT_CHROMIUM_SANDBOX=0`, parsing hostile pages — could
delete the readiness marker or rename `deploy/` away.

The one thing an app container genuinely needs to WRITE outside `media/` is the
shared 2FA lease, so the lease moved into its own subdirectory and that
subdirectory is the only other bind.
"""

from __future__ import annotations

import pathlib
import re
import shlex
import sys
from pathlib import PurePosixPath

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_app_runtime import _run  # noqa: E402

CLOUD_ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNTIME = CLOUD_ROOT / "scripts" / "radon-app-runtime.sh"

APP_UNITS = (
    "radon-api.service",
    "radon-monitor.service",
    "radon-relay.service",
    "radon-nextjs.service",
    "radon-newsfeed.service",
)


STATE_DIR = PurePosixPath("/var/lib/radon")
ALLOWED_UNDER_STATE_DIR = frozenset({STATE_DIR / "media", STATE_DIR / "ib-lease"})
MOUNT_DESTINATION_KEYS = ("dst", "destination", "target")


def _source_without_comments() -> str:
    return "\n".join(
        line
        for line in RUNTIME.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _docker_run_argv(log: str) -> list[str]:
    """The fake docker appends `"$*"` per invocation: argv joined by single
    spaces, unquoted. shlex also copes with a fake that logs `%q`-quoted argv."""
    runs = [line for line in log.splitlines() if line.startswith("run ")]
    assert len(runs) == 1, log
    return shlex.split(runs[0])


def _volume_destination(spec: str) -> str:
    """`-v src:dst[:opts]` / `-v name:dst[:opts]` / `-v dst` (anonymous)."""
    fields = spec.split(":")
    return fields[1] if len(fields) > 1 else fields[0]


def _mount_destination(spec: str) -> str:
    """`--mount type=bind,src=...,dst=...` and its `destination=`/`target=` spellings."""
    options = dict(field.partition("=")[::2] for field in spec.split(","))
    for key in MOUNT_DESTINATION_KEYS:
        if key in options:
            return options[key]
    raise AssertionError(f"--mount without a destination: {spec}")


def _bind_destinations(argv: list[str]) -> list[str]:
    """Every container path a `docker run` argv binds, opts and suffixes stripped.

    Handles `-v x`, `-v=x`, `--volume x`, `--volume=x`, `--mount k=v,...`,
    `--mount=k=v,...`.
    """
    destinations: list[str] = []
    args = iter(argv)
    for arg in args:
        flag, has_inline_value, inline_value = arg.partition("=")
        if flag not in ("-v", "--volume", "--mount"):
            continue
        spec = inline_value if has_inline_value else next(args)
        parse = _mount_destination if flag == "--mount" else _volume_destination
        destinations.append(parse(spec))
    return destinations


def _state_dir_exposures(destinations: list[str]) -> list[str]:
    """Binds that hand the container the state dir itself, an ancestor of it,
    or anything under it other than the two sanctioned subdirectories."""
    exposed = []
    for destination in destinations:
        path = PurePosixPath(destination)
        binds_state_dir_or_ancestor = path == STATE_DIR or path in STATE_DIR.parents
        binds_unsanctioned_child = STATE_DIR in path.parents and path not in ALLOWED_UNDER_STATE_DIR
        if binds_state_dir_or_ancestor or binds_unsanctioned_child:
            exposed.append(destination)
    return exposed


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["run", "-v", "/s:/var/lib/radon", "img"], ["/var/lib/radon"]),
        (["run", "-v=/s:/var/lib/radon:rw", "img"], ["/var/lib/radon"]),
        (["run", "--volume", "/s:/var/lib/radon:z", "img"], ["/var/lib/radon"]),
        (["run", "--volume=/s:/var/lib/radon:rslave", "img"], ["/var/lib/radon"]),
        (["run", "-v", "/var/lib/radon", "img"], ["/var/lib/radon"]),
        (["run", "--mount", "type=bind,src=/s,dst=/var/lib/radon", "img"], ["/var/lib/radon"]),
        (["run", "--mount=type=bind,source=/s,destination=/var/lib/radon/", "img"], ["/var/lib/radon/"]),
        (["run", "--mount", "type=bind,target=/var/lib/radon,src=/s,ro=true", "img"], ["/var/lib/radon"]),
        (["run", "-v", "/s:/var/lib", "img"], ["/var/lib"]),
        (["run", "-v", "/s:/var/lib/radon/deploy", "img"], ["/var/lib/radon/deploy"]),
        (["run", "-v", "/s:/var/lib/radon/media", "-v", "/l:/var/lib/radon/ib-lease:rw", "img"], []),
        (["run", "--env", "A=/var/lib/radon", "-v", "/d:/home/radon/radon/data", "img"], []),
    ],
)
def test_the_bind_parser_sees_through_every_suffix_and_spelling(argv, expected) -> None:
    """The regex this replaced demanded whitespace after the path, so `:rw`,
    `:z`, `:rslave` and every `--mount` spelling escaped it."""
    assert _state_dir_exposures(_bind_destinations(argv)) == expected


def test_no_app_unit_binds_the_whole_state_directory(tmp_path_factory) -> None:
    for unit in APP_UNITS:
        tmp_path = tmp_path_factory.mktemp(unit.replace(".", "_"))
        result = _run(tmp_path, ["run", unit])
        assert result.returncode == 0, result.stderr
        log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
        destinations = _bind_destinations(_docker_run_argv(log))
        assert _state_dir_exposures(destinations) == [], (unit, destinations)
        bound = {PurePosixPath(destination) for destination in destinations}
        assert ALLOWED_UNDER_STATE_DIR <= bound, (unit, destinations)


def test_the_source_carries_no_bare_state_bind() -> None:
    source = _source_without_comments()
    state_dir_bind = re.compile(
        r"(?::|\b(?:dst|destination|target)=)/var/lib/radon/?(?=[\"':,\s]|$)"
    )
    assert not state_dir_bind.search(source), state_dir_bind.search(source).group(0)
    assert "/var/lib/radon/ib-lease" in source


def test_the_lease_dir_is_created_before_the_container_starts(tmp_path) -> None:
    """The container cannot mkdir it: the parent is no longer mounted."""
    result = _run(tmp_path, ["run", "radon-api.service"])
    assert result.returncode == 0, result.stderr
    lease = tmp_path / "state" / "ib-lease"
    assert lease.is_dir(), sorted(p.name for p in (tmp_path / "state").iterdir())


def test_the_2fa_lease_lives_in_its_own_subdirectory() -> None:
    """Host units and the api container must resolve the SAME lease path."""
    sys.path.insert(0, str(CLOUD_ROOT.parent / "scripts"))
    from utils import ib_2fa_lock

    assert ib_2fa_lock.DEFAULT_LOCK_PATH == "/var/lib/radon/ib-lease/ib-2fa-push-lock.json"
    assert pathlib.PurePath(ib_2fa_lock.DEFAULT_LOCK_PATH).parent == pathlib.PurePath(
        "/var/lib/radon/ib-lease"
    )

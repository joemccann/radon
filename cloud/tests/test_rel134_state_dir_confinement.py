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
import sys

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


def _source_without_comments() -> str:
    return "\n".join(
        line
        for line in RUNTIME.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def test_no_app_unit_binds_the_whole_state_directory(tmp_path_factory) -> None:
    for unit in APP_UNITS:
        tmp_path = tmp_path_factory.mktemp(unit.replace(".", "_"))
        result = _run(tmp_path, ["run", unit])
        assert result.returncode == 0, result.stderr
        log = result.docker_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
        assert not re.search(r"-v \S+:/var/lib/radon(?:\s|$)", log), (unit, log)
        assert ":/var/lib/radon/media" in log, (unit, log)
        assert ":/var/lib/radon/ib-lease" in log, (unit, log)


def test_the_source_carries_no_bare_state_bind() -> None:
    source = _source_without_comments()
    assert '-v "${STATE_DIR}:/var/lib/radon"' not in source
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

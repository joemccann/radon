"""Stage real shell wrappers with scratch files private to one test."""

import shlex
import shutil
from pathlib import Path


def stage_shell_script(source: Path, destination: Path, scratch: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    # Keep the production control flow intact; redirect only its fixed /tmp
    # scratch paths, which otherwise collide across workers and OS users.
    destination.write_text(
        source.read_text().replace("/tmp/", f"{shlex.quote(str(scratch))}/"),
        encoding="utf-8",
    )
    return destination

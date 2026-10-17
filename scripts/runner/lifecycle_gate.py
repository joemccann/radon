#!/usr/bin/env python3
"""Hold a root-created shared maintenance gate for the entire runner process."""
import fcntl
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    root = Path(__file__).resolve().parent
    gate = root / "maintenance.lock"
    info = gate.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError("untrusted maintenance gate")
    fd = os.open(gate, os.O_RDONLY | os.O_NOFOLLOW)
    opened = os.fstat(fd)
    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
        raise PermissionError("maintenance gate changed")
    fcntl.flock(fd, fcntl.LOCK_SH)
    child = subprocess.Popen(["/bin/bash", str(root / "run_loop.sh"), *arguments], pass_fds=(fd,))
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda signum, frame: child.send_signal(signum))
    try:
        return child.wait()
    finally:
        os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())

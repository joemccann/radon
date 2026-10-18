"""Root inline Python in setup-vps.sh must be isolated.

The control-plane admission check (`python_inline_is_isolated` in
deploy-root-helper.sh) refuses any bundle whose helpers run `python -c` or a
stdin script without `-I`: without it the interpreter prepends the caller's
working directory to sys.path, and setup runs as root from the radon-owned
checkout, so a planted `json.py` executes as root on `import json`.

setup-vps.sh is not in that admission list, and two of its root invocations
were unisolated: `chmod_dir_nofollow`'s stdin script and the seccomp profile
validator, which additionally resolved a bare `python3` through PATH.
"""

from __future__ import annotations

import pathlib
import re

import pytest

CLOUD_ROOT = pathlib.Path(__file__).resolve().parent.parent
SETUP = CLOUD_ROOT / "scripts" / "setup-vps.sh"

# A root interpreter call that reads its program from -c or from stdin.
INLINE = re.compile(r"^[^#\n]*?(?P<call>\bpython3(?:\.13)?\b[^\n|;&]*?\s(?:-c\b|-\s))", re.MULTILINE)


def _inline_calls(text: str) -> list[str]:
    return [match.group("call").strip() for match in INLINE.finditer(text)]


def test_the_file_has_inline_interpreter_calls_to_check():
    assert _inline_calls(SETUP.read_text(encoding="utf-8"))


@pytest.mark.parametrize("call", _inline_calls(SETUP.read_text(encoding="utf-8")))
def test_every_inline_root_interpreter_call_is_isolated(call: str):
    assert " -I" in call, call


@pytest.mark.parametrize("call", _inline_calls(SETUP.read_text(encoding="utf-8")))
def test_no_inline_call_resolves_the_interpreter_through_path(call: str):
    # A bare `python3` is whatever PATH yields for root at that moment.
    assert not re.match(r"python3(\.13)?\s", call), call

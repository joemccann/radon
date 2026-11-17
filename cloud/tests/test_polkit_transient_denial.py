"""Polkit start grants must not authorize StartTransientUnit or an uninstalled unit."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

CLOUD = Path(__file__).resolve().parents[1]
RULES = CLOUD / "config" / "polkit" / "50-radon-services.rules"
SERVICES = CLOUD / "services"
MANAGE = "org.freedesktop.systemd1.manage-units"
TRANSIENT = "Authentication is required to start transient '$(unit)'."
FILE_START = "Authentication is required to start '$(unit)'."

_HARNESS = r"""
const fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const rules = [];
const polkit = {
  Result: { YES: "yes", NO: "no", NOTHING: undefined },
  addRule(fn) { rules.push(fn); },
};
eval(fs.readFileSync(input.path, "utf8"));
function decide(spec) {
  const action = {
    id: spec.id,
    lookup(key) { return Object.prototype.hasOwnProperty.call(spec.lookup, key) ? spec.lookup[key] : undefined; },
  };
  const subject = { user: spec.user };
  for (const rule of rules) {
    const result = rule(action, subject);
    if (result !== undefined) return result;
  }
  return null;
}
process.stdout.write(JSON.stringify(input.cases.map(decide)));
"""


def decide(cases: list[dict]) -> list:
    node = shutil.which("node")
    if not node:
        pytest.fail("node is required to execute the polkit rules")
    payload = json.dumps({"path": str(RULES), "cases": cases})
    proc = subprocess.run(
        [node, "-e", _HARNESS],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.fail(f"polkit harness failed: {proc.stderr or proc.stdout}")
    return json.loads(proc.stdout)


def _action(user: str, unit: str | None, verb: str, message: str | None) -> dict:
    lookup = {"unit": unit, "verb": verb, "polkit.message": message}
    return {"id": MANAGE, "user": user, "lookup": lookup}


def test_uninstalled_llm_index_is_not_granted() -> None:
    granted = decide(
        [
            _action("radon-responder", "radon-llm-index.service", "start", FILE_START),
            _action("radon", "radon-llm-index.service", "start", FILE_START),
        ]
    )
    assert granted == [None, None]
    assert (SERVICES / "radon-llm-index.service").is_file()


def test_transient_start_is_denied_for_both_subjects() -> None:
    results = decide(
        [
            _action("radon", "radon-vol-cone.service", "start", TRANSIENT),
            _action("radon-responder", "radon-vol-cone.service", "start", TRANSIENT),
            _action("radon", "radon-vol-cone.service", "start", ""),
            _action("radon-responder", "radon-vol-cone.service", "start", ""),
            _action(
                "radon",
                "radon-ib-gateway-preheld-restart.service",
                "start",
                TRANSIENT,
            ),
        ]
    )
    assert results == ["no", "no", "no", "no", "no"]


def test_file_backed_start_is_still_granted() -> None:
    results = decide(
        [
            _action("radon-responder", "radon-vol-cone.service", "start", FILE_START),
            _action("radon", "radon-vol-cone.service", "start", FILE_START),
            _action(
                "radon",
                "radon-ib-gateway-preheld-restart.service",
                "start",
                FILE_START,
            ),
            _action("radon-responder", "radon-vol-cone.service", "reset-failed", FILE_START),
        ]
    )
    assert results == ["yes", "yes", "yes", "yes"]


def _granted_rerunnable_units() -> list[str]:
    return re.findall(r'"(radon-[a-z0-9-]+\.service)":\s*true', RULES.read_text())


def test_granted_rerun_units_pull_in_no_other_service() -> None:
    """A responder-account start of a granted oneshot must start only that oneshot.

    Wants=/Requires=/BindsTo= on a granted unit would let the grant subject
    start the named dependency (the API, outside the deploy lock) by proxy.
    """
    pull_in = ("wants", "requires", "requisite", "bindsto", "upholds")
    granted = _granted_rerunnable_units()
    assert len(granted) >= 20
    offenders = []
    for name in granted:
        body = (SERVICES / name).read_text()
        for line in body.splitlines():
            key, sep, value = line.partition("=")
            if not sep or key.strip().lower() not in pull_in:
                continue
            for dep in value.split():
                # A granted oneshot may pull in another granted oneshot
                # (radon-skew2d wants its parent); nothing else.
                if dep.endswith(".service") and dep not in granted:
                    offenders.append(f"{name}: {line.strip()}")
    assert offenders == []

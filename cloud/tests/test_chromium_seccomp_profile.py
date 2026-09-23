"""DS-2026-09-20-04: the newsfeed Chromium seccomp profile.

The profile is the engine default (moby/profiles) plus the four syscalls
Chromium's namespace sandbox needs without CAP_SYS_ADMIN (the addition from
jessfraz/dotfiles chrome.json). The 2016 chrome.json itself is not vendored:
it predates clone3/faccessat2/statx/rseq, so under its EPERM default glibc
fails thread creation and access() on any current image.
"""

from __future__ import annotations

import json
from pathlib import Path

CLOUD = Path(__file__).resolve().parents[1]
PROFILE = CLOUD / "config" / "seccomp" / "chromium.json"

CHROMIUM_SANDBOX_SYSCALLS = {"chroot", "clone", "setns", "unshare"}
# Never unconditionally allowed: host-affecting or kernel-surface syscalls the
# engine default keeps behind a capability the container does not hold.
NEVER_UNCONDITIONAL = {
    "bpf",
    "fanotify_init",
    "finit_module",
    "fsmount",
    "fsopen",
    "init_module",
    "kexec_load",
    "mount",
    "move_mount",
    "open_by_handle_at",
    "open_tree",
    "perf_event_open",
    "pivot_root",
    "reboot",
    "setdomainname",
    "sethostname",
    "syslog",
    "umount2",
    "vhangup",
}


def _profile() -> dict:
    return json.loads(PROFILE.read_text(encoding="utf-8"))


def _unconditional_allows(profile: dict) -> set[str]:
    names: set[str] = set()
    for rule in profile["syscalls"]:
        if rule["action"] != "SCMP_ACT_ALLOW":
            continue
        if rule.get("args") or rule.get("includes") or rule.get("excludes"):
            continue
        names |= set(rule["names"])
    return names


def test_profile_denies_by_default() -> None:
    profile = _profile()
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    arches = {entry["architecture"] for entry in profile["archMap"]}
    assert {"SCMP_ARCH_X86_64", "SCMP_ARCH_AARCH64"} <= arches


def test_profile_grants_the_chromium_namespace_sandbox_syscalls() -> None:
    allowed = _unconditional_allows(_profile())
    assert CHROMIUM_SANDBOX_SYSCALLS <= allowed


def test_chromium_addition_is_one_attributed_rule() -> None:
    rules = [
        rule
        for rule in _profile()["syscalls"]
        if "jessfraz" in rule.get("comment", "")
    ]
    assert len(rules) == 1
    assert set(rules[0]["names"]) == CHROMIUM_SANDBOX_SYSCALLS
    assert rules[0]["action"] == "SCMP_ACT_ALLOW"


def test_profile_keeps_clone3_enosys_so_glibc_falls_back_to_clone() -> None:
    clone3 = [
        rule
        for rule in _profile()["syscalls"]
        if rule["names"] == ["clone3"] and rule["action"] == "SCMP_ACT_ERRNO"
    ]
    assert clone3 and clone3[0]["errnoRet"] == 38


def test_profile_carries_modern_syscalls_the_2016_reference_lacks() -> None:
    allowed = _unconditional_allows(_profile())
    assert {"statx", "rseq", "faccessat2", "close_range", "membarrier", "openat2"} <= allowed


def test_profile_does_not_widen_host_affecting_syscalls() -> None:
    allowed = _unconditional_allows(_profile())
    assert allowed & NEVER_UNCONDITIONAL == set()

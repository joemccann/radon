"""Tailnet policy as code: cloud/tailscale/policy.hujson.

The live tailnet ran an allow-all grant, so any enrolled node reached every
port on the app and broker hosts. The checked-in policy is the reviewed
replacement. These tests parse the HuJSON and pin the properties a careless
edit would break: no allow-all, the ops tag reaches only 8341 on app/broker
and 443 between ops nodes, and the policy carries its own ACL `tests` block
asserting the ops tag cannot reach 8321, 4001, 8330 or 8340.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "tailscale" / "policy.hujson"

TAGS = {"tag:radon-app", "tag:radon-broker", "tag:radon-ops", "tag:radon-gpu"}
SERVER_TAGS = {"tag:radon-app", "tag:radon-broker"}
FORBIDDEN_OPS_PORTS = {"8321", "4001", "8330", "8340"}
OPERATOR_PLACEHOLDER = "__OPERATOR_LOGIN__"


def _strip_hujson(text: str) -> str:
    """HuJSON -> JSON: drop // and /* */ comments and trailing commas."""
    out: list[str] = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.index("*/", i + 2)
            i = end + 2
        else:
            out.append(ch)
            i += 1
    cleaned = "".join(out)
    # Trailing commas: a comma followed only by whitespace then } or ].
    result: list[str] = []
    in_str = False
    for idx, ch in enumerate(cleaned):
        if in_str:
            result.append(ch)
            if ch == '"' and cleaned[idx - 1] != "\\":
                in_str = False
            continue
        if ch == '"':
            in_str = True
        if ch == ",":
            rest = cleaned[idx + 1 :].lstrip()
            if rest[:1] in ("}", "]"):
                continue
        result.append(ch)
    return "".join(result)


@pytest.fixture(scope="module")
def policy() -> dict:
    assert POLICY.is_file(), f"{POLICY} is missing"
    return json.loads(_strip_hujson(POLICY.read_text(encoding="utf-8")))


def _ports(grant: dict) -> list[str]:
    return list(grant.get("ip", []))


def _port_numbers(spec: str) -> str:
    return spec.split(":", 1)[1] if ":" in spec else spec


def test_parser_handles_comments_and_trailing_commas():
    raw = '{\n // c\n "a": "x//y", /* b */ "b": [1, 2,],\n}'
    assert json.loads(_strip_hujson(raw)) == {"a": "x//y", "b": [1, 2]}


def test_tags_are_defined_and_owned_by_the_operator(policy):
    owners = policy["tagOwners"]
    assert set(owners) == TAGS
    for tag in TAGS:
        assert owners[tag] == ["group:operator"], tag


def test_operator_group_is_one_placeholder_login(policy):
    # The repo is public: the real login is substituted at apply time.
    assert policy["groups"] == {"group:operator": [OPERATOR_PLACEHOLDER]}


def test_uses_grants_not_legacy_acls(policy):
    assert "acls" not in policy
    assert policy["grants"], "grants must be non-empty"


def test_no_allow_all(policy):
    for grant in policy["grants"]:
        assert "*" not in grant["src"], grant
        assert "*" not in grant["dst"], grant
        assert "autogroup:internet" not in grant["dst"], grant
        if any(d.startswith("tag:") for d in grant["dst"]):
            for spec in _ports(grant):
                assert _port_numbers(spec) not in ("*", ""), grant
                assert spec != "*", grant


def test_every_grant_is_a_known_shape(policy):
    for grant in policy["grants"]:
        assert set(grant) <= {"src", "dst", "ip"}, grant
        assert grant["ip"], grant
        for spec in grant["ip"]:
            proto, _, port = spec.partition(":")
            if grant["dst"] == ["autogroup:self"]:
                continue
            assert proto in ("tcp", "udp"), grant
            assert port.isdigit(), grant


def test_ops_tag_reaches_only_8341_on_servers_and_443_between_ops(policy):
    ops = [g for g in policy["grants"] if "tag:radon-ops" in g["src"]]
    assert ops, "no grant for tag:radon-ops"
    reach: set[tuple[str, str]] = set()
    for grant in ops:
        assert grant["src"] == ["tag:radon-ops"], grant
        for dst in grant["dst"]:
            for spec in grant["ip"]:
                reach.add((dst, spec))
    assert reach == {
        ("tag:radon-app", "tcp:8341"),
        ("tag:radon-broker", "tcp:8341"),
        ("tag:radon-ops", "tcp:443"),
    }


def test_operator_reaches_ssh_on_every_radon_tag(policy):
    reach = {
        (dst, spec)
        for g in policy["grants"]
        if "group:operator" in g["src"]
        for dst in g["dst"]
        for spec in g["ip"]
    }
    for tag in TAGS:
        assert (tag, "tcp:22") in reach, tag
    assert ("tag:radon-app", "tcp:8321") in reach
    # Nothing on the broker but SSH: the order path is the private net.
    broker = {spec for dst, spec in reach if dst == "tag:radon-broker"}
    assert broker == {"tcp:22"}


def test_no_tailnet_grant_reaches_the_broker_order_ports(policy):
    for grant in policy["grants"]:
        if "tag:radon-broker" in grant["dst"]:
            for spec in grant["ip"]:
                assert _port_numbers(spec) not in ("4001", "8340"), grant


def test_policy_tests_deny_ops_the_service_ports(policy):
    tests = policy["tests"]
    ops_tests = [t for t in tests if t["src"] == "tag:radon-ops"]
    assert ops_tests, "no ACL test with src tag:radon-ops"
    denied = {d for t in ops_tests for d in t.get("deny", [])}
    accepted = {a for t in ops_tests for a in t.get("accept", [])}
    for host in SERVER_TAGS:
        for port in FORBIDDEN_OPS_PORTS:
            assert f"{host}:{port}" in denied, f"{host}:{port}"
        assert f"{host}:8341" in accepted
        assert f"{host}:22" in denied
    assert "tag:radon-ops:443" in accepted


def test_policy_tests_agree_with_the_grants(policy):
    """Every accept is granted, every deny is not: the file is self-consistent."""
    def granted(src: str, target: str) -> bool:
        host, port = target.rsplit(":", 1)
        for grant in policy["grants"]:
            srcs = set(grant["src"])
            if src not in srcs and not (
                src == OPERATOR_PLACEHOLDER and "group:operator" in srcs
            ):
                continue
            if host not in grant["dst"]:
                continue
            if any(_port_numbers(s) == port for s in grant["ip"]):
                return True
        return False

    for case in policy["tests"]:
        for target in case.get("accept", []):
            assert granted(case["src"], target), (case["src"], target)
        for target in case.get("deny", []):
            assert not granted(case["src"], target), (case["src"], target)


def test_no_tailscale_ssh_rules(policy):
    # Tailscale SSH authenticates the source node, not the local user. Every
    # operator-owned node matches group:operator, including the shared Mac
    # mini where the unprivileged runner user lives, so any rule here hands
    # that user the destination login. SSH to Radon hosts is plain sshd with
    # key auth (per-user keys), on the public IP or the tailnet address.
    assert policy.get("ssh") == []

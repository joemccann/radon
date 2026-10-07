"""T-536: observe publisher admission at the fake apt installation boundary.

The real repository installer runs; curl, gpg and install are shell fakes.
Only temporary key transcripts and staging files are read or written.
"""

import json
from pathlib import Path

import pytest

from test_gpu_bootstrap import shell


NVIDIA = "C95B321B61E88C1809C4F759DDCAE044F796ECB0"
TAILSCALE = "2596A99EAAB33821893C0A79458CA832957F5868"
FOREIGN = "0" * 40
NVIDIA_TARGETS = [
    "/usr/share/keyrings/nvidia-container-toolkit.gpg",
    "/etc/apt/sources.list.d/nvidia-container-toolkit.list",
]
TAILSCALE_TARGETS = [
    "/usr/share/keyrings/tailscale-archive-keyring.gpg",
    "/etc/apt/sources.list.d/tailscale.list",
]


def _key(fingerprint: str, kind: str = "pub") -> str:
    return f"{kind}:-:4096:1:AAAA:0:::-:::scESC:\nfpr:::::::::{fingerprint}:\n"


def _install(tmp_path: Path, nvidia: str, tailscale: str):
    records = tmp_path / "records"
    records.mkdir()
    (records / "nvidia").write_text(nvidia)
    (records / "tailscale").write_text(tailscale)
    installs = tmp_path / "installs"
    result = shell(
        r'''
test_repository_stage="$1"
test_repository_records="$2"
test_repository_installs="$3"
curl() {
  while (($#)); do
    if [[ "$1" == -o ]]; then : > "$2"; return; fi
    shift
  done
  return 1
}
gpg() {
  if [[ " $* " == *" --dearmor "* ]]; then
    while (($#)); do
      if [[ "$1" == -o ]]; then : > "$2"; return; fi
      shift
    done
    return 1
  fi
  case "${@: -1}" in
    "$test_repository_stage/nvidia.gpg") cat "$test_repository_records/nvidia" ;;
    "$test_repository_stage/tailscale.gpg") cat "$test_repository_records/tailscale" ;;
    *) return 1 ;;
  esac
}
install() {
  python3 -c 'import json, sys
with open(sys.argv[1], "a") as log:
    log.write(json.dumps(sys.argv[2:]) + "\n")
' "$test_repository_installs" "$@"
}
install_repositories "$test_repository_stage"
''',
        str(tmp_path), str(records), str(installs),
    )
    return result, [json.loads(line) for line in installs.read_text().splitlines()] if installs.exists() else []


def _expected_installs(tmp_path):
    return [
        ["-m", "0644", str(tmp_path / source), target]
        for source, target in zip(
            ("nvidia.gpg", "nvidia.list", "tailscale.gpg", "tailscale.list"),
            NVIDIA_TARGETS + TAILSCALE_TARGETS,
        )
    ]


@pytest.mark.parametrize("vendor", ["nvidia", "tailscale"])
@pytest.mark.parametrize("fault", ["foreign", "mixed", "subkey-only"])
def test_untrusted_keyring_never_reaches_its_apt_install(tmp_path, vendor, fault):
    pinned = NVIDIA if vendor == "nvidia" else TAILSCALE
    untrusted = {
        "foreign": _key(FOREIGN),
        "mixed": _key(pinned) + _key(FOREIGN),
        "subkey-only": _key(pinned, "sub"),
    }[fault]
    result, installs = _install(
        tmp_path,
        untrusted if vendor == "nvidia" else _key(NVIDIA),
        untrusted if vendor == "tailscale" else _key(TAILSCALE),
    )

    assert result.returncode == 78, result.stdout + result.stderr
    assert f"{'NVIDIA' if vendor == 'nvidia' else 'Tailscale'} apt signing key" in result.stderr
    assert installs == ([] if vendor == "nvidia" else _expected_installs(tmp_path)[:2])


@pytest.mark.parametrize("subkeys", [False, True])
def test_each_pinned_publisher_reaches_only_its_repository(tmp_path, subkeys):
    subkey = _key(FOREIGN, "sub") if subkeys else ""
    result, installs = _install(tmp_path, _key(NVIDIA) + subkey, _key(TAILSCALE) + subkey)

    assert result.returncode == 0, result.stderr
    assert installs == _expected_installs(tmp_path)
    assert (tmp_path / "nvidia.list").read_text() == (
        "deb [arch=amd64 signed-by=/usr/share/keyrings/nvidia-container-toolkit.gpg] "
        "https://nvidia.github.io/libnvidia-container/stable/deb/amd64 /\n"
    )
    assert (tmp_path / "tailscale.list").read_text() == (
        "deb [arch=amd64 signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] "
        "https://pkgs.tailscale.com/stable/ubuntu noble main\n"
    )

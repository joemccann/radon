#!/usr/bin/env bash
# Dedicated GPU host only. Never run the app/broker setup script on this host.
set -euo pipefail

die() { printf 'GPU bootstrap: %s\n' "$*" >&2; exit 78; }

usage() {
  cat <<'USAGE'
Usage: bootstrap.sh --role radon-slm --authorized-keys /root/operator.pub \
  --ssh-cidr PUBLIC_IP/32 --public-interface enp7s0

Run as root on a dedicated Ubuntu 24.04 amd64 host. IPv6 uses /128.
The key file must be root-owned, regular, and not group/other writable.
Existing key-only root access remains available for administration.
No model service is started. Enrollment and reboot are explicit later steps.
USAGE
}

validate_config() {
  local role="$1" cidr="$2" interface="$3" keys="$4"
  [[ "$role" == radon-slm ]] || die 'explicit --role radon-slm required'
  [[ "$interface" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,14}$ && "$interface" != lo && "$interface" != tailscale0 ]] || die 'invalid public interface'
  python3 - "$cidr" "$keys" <<'PY'
import ipaddress
import os
from pathlib import Path
import stat
import sys

try:
    network = ipaddress.ip_network(sys.argv[1], strict=True)
    if network.prefixlen != network.max_prefixlen or not network.network_address.is_global:
        raise ValueError("bootstrap SSH requires one public address with /32 or /128")
    path = Path(sys.argv[2])
    if not path.is_absolute():
        raise ValueError("public-key path must be absolute")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError("public-key file must be root-owned, regular, and not writable by group/others")
    for parent in path.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("public-key parent directories must be root-owned and not writable by group/others")
    lines = [line for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        raise ValueError("public-key file is empty")
    allowed = {"ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521"}
    for line in lines:
        fields = line.split()
        if len(fields) < 2 or fields[0] not in allowed:
            raise ValueError("only plain SSH public keys without authorized_keys options are accepted")
except (ValueError, OSError) as exc:
    print(f"GPU bootstrap: {exc}", file=sys.stderr)
    sys.exit(78)
PY
  # ssh-keygen validates the actual key encoding, not merely its prefix.
  local line key_tmp
  key_tmp="$(mktemp)"
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "${line//[[:space:]]/}" || "$line" == \#* ]] && continue
    printf '%s\n' "$line" > "$key_tmp"
    if ! ssh-keygen -l -f "$key_tmp" >/dev/null 2>&1; then
      rm -f "$key_tmp"
      die 'invalid SSH public key'
    fi
  done < "$keys"
  rm -f "$key_tmp"
}

check_host_role() {
  # The optional prefix is used by isolated regression tests; main never sets it.
  local prefix="${1:-}" marker
  for marker in /etc/radon/env /etc/radon/ib-gateway-compose.yml /var/lib/radon/control-plane-ready /home/radon/radon-cloud/.env; do
    [[ ! -e "$prefix$marker" && ! -L "$prefix$marker" ]] || die "existing app/broker marker: $marker"
  done
  for marker in /etc/systemd/system/radon-api.service /etc/systemd/system/radon-nextjs.service /etc/systemd/system/ib-gateway.service; do
    [[ ! -e "$prefix$marker" && ! -L "$prefix$marker" ]] || die "existing app/broker service: $marker"
  done
  [[ ! -L "$prefix/etc/radon/gpu-host" ]] || die 'symlinked GPU role marker'
  if [[ -e "$prefix/etc/radon/gpu-host" ]]; then
    [[ -f "$prefix/etc/radon/gpu-host" && "$(<"$prefix/etc/radon/gpu-host")" == radon-slm ]] || die 'incompatible existing GPU role'
  fi
}

install_repositories() {
  local staging="$1"
  # Vendor HTTPS key downloads feed signed-by repositories, never shell execution.
  curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    https://nvidia.github.io/libnvidia-container/gpgkey -o "$staging/nvidia.asc"
  gpg --batch --yes --dearmor -o "$staging/nvidia.gpg" "$staging/nvidia.asc"
  install -m 0644 "$staging/nvidia.gpg" /usr/share/keyrings/nvidia-container-toolkit.gpg
  printf '%s\n' 'deb [arch=amd64 signed-by=/usr/share/keyrings/nvidia-container-toolkit.gpg] https://nvidia.github.io/libnvidia-container/stable/deb/amd64 /' > "$staging/nvidia.list"
  install -m 0644 "$staging/nvidia.list" /etc/apt/sources.list.d/nvidia-container-toolkit.list
  curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg -o "$staging/tailscale.gpg"
  gpg --batch --show-keys "$staging/tailscale.gpg" >/dev/null
  install -m 0644 "$staging/tailscale.gpg" /usr/share/keyrings/tailscale-archive-keyring.gpg
  printf '%s\n' 'deb [arch=amd64 signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] https://pkgs.tailscale.com/stable/ubuntu noble main' > "$staging/tailscale.list"
  install -m 0644 "$staging/tailscale.list" /etc/apt/sources.list.d/tailscale.list
}

configure_firewall() {
  local cidr="$1" interface="$2"
  # Add the recovery path before enabling UFW. Never reset existing rules.
  ufw allow in on "$interface" from "$cidr" to any port 22 proto tcp comment radon-gpu-bootstrap-ssh
  ufw allow in on tailscale0 to any port 22 proto tcp comment radon-gpu-tailnet-ssh
  ufw allow in on tailscale0 to any port 8350 proto tcp comment radon-gpu-tailnet-inference
  ufw allow in on "$interface" to any port 41641 proto udp comment radon-gpu-tailscale
  # A preexisting broad allow must not expose inference; insert before other rules.
  ufw insert 1 deny in on "$interface" to any port 8350 proto tcp comment radon-gpu-private-inference
  ufw default deny incoming
  ufw default allow outgoing
  ufw --force enable
}

main() {
  local role='' keys='' cidr='' interface='' staging unit
  while (($#)); do
    case "$1" in
      --help|-h) usage; return 0 ;;
      --role|--authorized-keys|--ssh-cidr|--public-interface)
        (($# >= 2)) || die "missing value for $1"
        case "$1" in
          --role) role="$2";; --authorized-keys) keys="$2";;
          --ssh-cidr) cidr="$2";; --public-interface) interface="$2";;
        esac
        shift 2 ;;
      *) die "unknown argument: $1" ;;
    esac
  done
  [[ "$EUID" == 0 ]] || die 'run as root'
  [[ "$(uname -s)" == Linux && "$(dpkg --print-architecture)" == amd64 ]] || die 'Ubuntu amd64 required'
  # Parse OS metadata, never source a shell file.
  python3 - <<'PY'
from pathlib import Path
import sys
values = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
sys.exit(0 if values.get("ID", "").strip('"') == "ubuntu" and values.get("VERSION_ID", "").strip('"') == "24.04" else 78)
PY
  validate_config "$role" "$cidr" "$interface" "$keys"
  ip link show dev "$interface" >/dev/null || die 'public interface does not exist'
  ip -json route get "${cidr%/*}" | python3 -c 'import json,sys; routes=json.load(sys.stdin); sys.exit(0 if routes and all(r.get("dev") == sys.argv[1] for r in routes) else 78)' "$interface" || die 'public interface does not route to bootstrap SSH address'
  check_host_role
  for unit in radon-api.service radon-nextjs.service ib-gateway.service; do
    [[ "$(systemctl show "$unit" --property=LoadState --value)" == not-found ]] || die "existing app/broker unit: $unit"
  done
  if id radon >/dev/null 2>&1; then
    [[ " $(id -nG radon) " != *' docker '* && " $(id -nG radon) " != *' sudo '* ]] || die 'existing radon account has privileged group membership'
    [[ "$(getent passwd radon | cut -d: -f6)" == /home/radon ]] || die 'unexpected radon home directory'
  fi
  [[ ! -L /home/radon && ! -L /home/radon/.ssh && ! -L /home/radon/.ssh/authorized_keys && ! -L /root/.ssh && ! -L /root/.ssh/authorized_keys ]] || die 'symlinked SSH destination'
  python3 - <<'PY'
from pathlib import Path
import stat

targets = (
    "/etc/radon/gpu-host", "/etc/ssh/sshd_config.d/00-radon-gpu.conf",
    "/usr/share/keyrings/nvidia-container-toolkit.gpg", "/usr/share/keyrings/tailscale-archive-keyring.gpg",
    "/etc/apt/sources.list.d/nvidia-container-toolkit.list", "/etc/apt/sources.list.d/tailscale.list",
)
for name in targets:
    path = Path(name)
    for entry in (path, *path.parents):
        if entry.exists() or entry.is_symlink():
            info = entry.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise SystemExit(f"Refusing untrusted GPU bootstrap destination: {entry}")
PY
  staging="$(mktemp -d)"
  trap "rm -rf -- '$staging'" EXIT
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-cache show nvidia-driver-580-open >/dev/null 2>&1 || die 'nvidia-driver-580-open unavailable; enable official noble restricted repository first'
  apt-get install -y ca-certificates curl gnupg openssh-server ufw docker.io linux-headers-generic nvidia-driver-580-open
  install_repositories "$staging"
  apt-get update
  apt-get install -y nvidia-container-toolkit tailscale
  nvidia-ctk runtime configure --runtime=docker
  # Reboot applies the driver and Docker runtime; do not restart running containers.
  if ! id radon >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash radon
  fi
  [[ "$(getent passwd radon | cut -d: -f6)" == /home/radon ]] || die 'unexpected radon home directory'
  [[ ! -L /home/radon && ! -L /home/radon/.ssh && ! -L /home/radon/.ssh/authorized_keys ]] || die 'symlinked radon SSH path'
  install -d -m 0700 -o radon -g radon /home/radon/.ssh
  if [[ -f /home/radon/.ssh/authorized_keys ]]; then
    awk '!seen[$0]++' /home/radon/.ssh/authorized_keys "$keys" > "$staging/radon.keys"
  else
    cp "$keys" "$staging/radon.keys"
  fi
  install -m 0600 -o radon -g radon "$staging/radon.keys" /home/radon/.ssh/authorized_keys
  [[ ! -L /root/.ssh && ! -L /root/.ssh/authorized_keys ]] || die 'symlinked root SSH path'
  install -d -m 0700 -o root -g root /root/.ssh
  if [[ -f /root/.ssh/authorized_keys ]]; then
    awk '!seen[$0]++' /root/.ssh/authorized_keys "$keys" > "$staging/root.keys"
  else
    cp "$keys" "$staging/root.keys"
  fi
  install -m 0600 -o root -g root "$staging/root.keys" /root/.ssh/authorized_keys
  install -d -m 0755 /etc/ssh/sshd_config.d
  printf '%s\n' 'PasswordAuthentication no' 'KbdInteractiveAuthentication no' 'PubkeyAuthentication yes' 'PermitRootLogin prohibit-password' > "$staging/sshd.conf"
  # Validate candidate in addition to current configuration before installing it.
  /usr/sbin/sshd -t
  /usr/sbin/sshd -t -f "$staging/sshd.conf" -h /etc/ssh/ssh_host_ed25519_key
  install -m 0644 "$staging/sshd.conf" /etc/ssh/sshd_config.d/00-radon-gpu.conf
  /usr/sbin/sshd -t
  /usr/sbin/sshd -T > "$staging/sshd.effective"
  grep -qx 'passwordauthentication no' "$staging/sshd.effective" || die 'existing SSH configuration overrides password policy'
  grep -qx 'kbdinteractiveauthentication no' "$staging/sshd.effective" || die 'existing SSH configuration overrides keyboard policy'
  grep -qx 'pubkeyauthentication yes' "$staging/sshd.effective" || die 'existing SSH configuration disables public keys'
  systemctl reload ssh
  configure_firewall "$cidr" "$interface"
  install -d -m 0755 /etc/radon
  printf '%s\n' radon-slm > "$staging/gpu-host"
  install -m 0644 -o root -g root "$staging/gpu-host" /etc/radon/gpu-host
  printf '%s\n' 'Bootstrap complete. Keep this SSH session open and verify a second key login.' \
    'Next: tailscale up --hostname=radon-slm --accept-routes=false --accept-dns=false' \
    'Complete browser enrollment, verify tailnet SSH, then explicitly reboot.' \
    'After reboot verify nvidia-smi and GPU container access before installing inference.'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi

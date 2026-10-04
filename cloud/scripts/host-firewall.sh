#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# host-firewall.sh -- declared ufw ruleset for the app and broker hosts
# (Ops Plane step 2D). OPERATOR-RUN ONLY: deploy never calls this.
#
#   host-firewall.sh --role app|broker            # dry run: print the commands
#   host-firewall.sh --role app|broker --apply    # as root: reset + apply
#
# Sources (env, IPv4 /32 or bare address, comma or space separated):
#   RADON_FW_OPERATOR_SOURCES  operator devices (tailnet laptop, Mac mini,
#                              phone) plus a public recovery address
#   RADON_FW_OPS_SOURCES       future ops-plane hosts (placeholders until
#                              they exist; empty is fine)
#
# ufw is first-match, so the ops allow/deny pair comes before every broad
# allow. --apply runs `ufw --force reset` (the old rules are backed up under
# /etc/ufw by ufw itself), so the result is exactly the printed set.
# Runbook: docs/operations.md "Host firewalls".
#
# radon_fw_rules and radon_fw_valid_sources are mirrored byte-for-byte in
# setup-vps.sh (open_firewall); cloud/tests/test_host_firewall.py pins that.
# ---------------------------------------------------------------------------

radon_fw_valid_sources() {
  local raw="$1" entry addr octet
  local -a out=()
  for entry in ${raw//,/ }; do
    addr="${entry%/32}"
    if [[ ! "$addr" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
      echo "host-firewall: not an IPv4 /32: ${entry}" >&2
      return 2
    fi
    for octet in ${addr//./ }; do
      if (( 10#$octet > 255 )); then
        echo "host-firewall: not an IPv4 /32: ${entry}" >&2
        return 2
      fi
    done
    out+=("$addr")
  done
  if (( ${#out[@]} )); then
    printf '%s\n' "${out[@]}"
  fi
}

# Print the ordered ufw argument lines for ROLE. No line carries a space
# inside an argument, so callers may word-split each line.
radon_fw_rules() {
  local role="$1" operators ops src
  operators="$(radon_fw_valid_sources "${RADON_FW_OPERATOR_SOURCES:-}")" || return 2
  ops="$(radon_fw_valid_sources "${RADON_FW_OPS_SOURCES:-}")" || return 2

  echo "default deny incoming"
  echo "default allow outgoing"
  for src in $ops; do
    echo "allow from ${src} to any port 8341 proto tcp comment radon-ops-agent"
    echo "deny from ${src} comment radon-ops-deny-else"
  done
  case "$role" in
    app)
      # Public SSH stays open: CI deploys over SSH from GitHub-hosted
      # runners (ci.yml appleboy/ssh-action), whose addresses are not fixed.
      echo "allow 22/tcp comment ssh-ci-deploy-and-recovery"
      echo "allow 80/tcp comment caddy-http"
      echo "allow 443/tcp comment caddy-https"
      echo "allow 41641/udp comment tailscale-direct"
      for src in $operators; do
        echo "allow from ${src} to any port 8321 proto tcp comment operator-cloud-thin-api"
      done
      echo "allow from 10.0.0.4 to any port 8321 proto tcp comment radon-broker-health"
      ;;
    broker)
      if [[ -z "$operators" ]]; then
        echo "host-firewall: broker needs RADON_FW_OPERATOR_SOURCES (SSH would be closed)" >&2
        return 2
      fi
      for src in $operators; do
        echo "allow from ${src} to any port 22 proto tcp comment operator-ssh"
      done
      echo "allow from 10.0.0.2 to any port 4001 proto tcp comment app-ib-api"
      echo "allow from 10.0.0.2 to any port 8340 proto tcp comment app-ib-gateway-remote"
      echo "allow 41641/udp comment tailscale-direct"
      ;;
    *)
      echo "host-firewall: unknown role: ${role}" >&2
      return 2
      ;;
  esac
}

radon_fw_main() {
  local role="" apply=0 rules line
  while (( $# )); do
    case "$1" in
      --role) role="${2:-}"; shift 2 ;;
      --apply) apply=1; shift ;;
      --dry-run) apply=0; shift ;;
      *) echo "usage: host-firewall.sh --role app|broker [--dry-run|--apply]" >&2; return 2 ;;
    esac
  done
  rules="$(radon_fw_rules "$role")" || return 2

  if (( ! apply )); then
    echo "# dry run (role=${role}); re-run with --apply as root to install"
    echo "ufw --force reset"
    while IFS= read -r line; do echo "ufw ${line}"; done <<< "$rules"
    echo "ufw --force enable"
    return 0
  fi

  if [[ $EUID -ne 0 && "${RADON_FW_TEST_MODE:-0}" != "1" ]]; then
    echo "host-firewall: --apply must run as root" >&2
    return 1
  fi
  local -a args
  ufw --force reset
  while IFS= read -r line; do
    read -ra args <<< "$line"
    ufw "${args[@]}"
  done <<< "$rules"
  ufw --force enable
  ufw status numbered
}

if [[ "${RADON_FW_SOURCE_ONLY:-0}" != "1" ]]; then
  radon_fw_main "$@"
fi

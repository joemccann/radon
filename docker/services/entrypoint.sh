#!/usr/bin/env bash
# Boot script for radon-services container.
#
# systemd as PID 1 inside Docker requires a few well-known shims so the
# bus is reachable and timers actually fire. We do the minimum — anything
# more elaborate (cgroup tweaks, /sys remounts) is handled by the docker
# run flags we set in docker-compose.yml.

set -euo pipefail

mkdir -p /run/systemd /run/dbus
mkdir -p /app/data /app/data/menthorq_cache /app/data/locks

# Persist the embedded libSQL replica path — same convention as the
# laptop. The volume mount in docker-compose maps host → /app/data.
export RADON_DATA_DIR=/app/data

# Hand off to systemd. It reads /etc/systemd/system/*.timer and starts
# the radon-* targets so the schedulers fire on cadence.
exec /lib/systemd/systemd --system --unit=multi-user.target

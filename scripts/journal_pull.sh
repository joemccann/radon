#!/usr/bin/env bash
# DUR-12: daily off-box journald snapshot — laptop-initiated pull from the VPS.
#
# Run by ~/Library/LaunchAgents/com.radon.journal-pull.plist (daily +
# RunAtLoad). Laptop-initiated per the media-rsync precedent: VPS-push to a
# sleeping laptop fails silently, a laptop pull just runs on next wake.
#
# Pulls `journalctl --since yesterday -o export | gzip` from the VPS into
# data/journal_archive/ (gitignored) and prunes local snapshots older than
# 30 days. Atomic: writes to a .partial and renames only on success, so a
# dropped SSH never leaves a truncated archive masquerading as a good one.
set -euo pipefail

VPS_HOST="${RADON_JOURNAL_VPS:-root@ib-gateway}"
ARCHIVE_DIR="${RADON_JOURNAL_ARCHIVE_DIR:-$(cd "$(dirname "$0")/.." && pwd)/data/journal_archive}"
RETENTION_DAYS=30
SSH_OPTS=(-o ConnectTimeout=15 -o BatchMode=yes)

mkdir -p "$ARCHIVE_DIR"

stamp="$(date +%F)"
out="$ARCHIVE_DIR/journal-$stamp.export.gz"
partial="$out.partial"

if [ -s "$out" ]; then
  echo "[journal-pull] $out already exists; skipping pull"
else
  echo "[journal-pull] pulling journald snapshot from $VPS_HOST"
  ssh "${SSH_OPTS[@]}" "$VPS_HOST" \
    "journalctl --since yesterday -o export | gzip" > "$partial"
  if [ ! -s "$partial" ]; then
    rm -f "$partial"
    echo "[journal-pull] FAILED: empty snapshot from $VPS_HOST" >&2
    exit 1
  fi
  mv "$partial" "$out"
  echo "[journal-pull] wrote $out ($(du -h "$out" | cut -f1))"
fi

find "$ARCHIVE_DIR" -name 'journal-*.export.gz' -mtime "+$RETENTION_DAYS" -delete
echo "[journal-pull] pruned snapshots older than $RETENTION_DAYS days"

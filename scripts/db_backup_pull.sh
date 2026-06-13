#!/usr/bin/env bash
# DUR-13: daily off-box copy of the nightly Turso dumps — laptop-initiated
# pull from the VPS.
#
# Run by ~/Library/LaunchAgents/com.radon.db-backup-pull.plist (daily +
# RunAtLoad). Laptop-initiated per the media-rsync / journal-pull
# precedent: VPS-push to a sleeping laptop fails silently, a laptop pull
# just runs on next wake.
#
# rsyncs backups/db/*.sql.gz from the VPS into data/db_backups/
# (gitignored) and prunes local copies older than 30 days. Deliberately
# NO --delete: a wiped or compromised VPS must not be able to empty the
# off-box copy on the next pull. rsync writes to temp files and renames,
# so a dropped SSH never leaves a truncated dump masquerading as a good
# one. Restore runbook: docs/cloud-services.md "DB backup & restore".
set -euo pipefail

VPS_HOST="${RADON_DB_BACKUP_VPS:-radon@ib-gateway}"
REMOTE_DIR="/home/radon/radon-cloud/backups/db"
LOCAL_DIR="${RADON_DB_BACKUP_DIR:-$(cd "$(dirname "$0")/.." && pwd)/data/db_backups}"
RETENTION_DAYS=30
SSH_OPTS="-o ConnectTimeout=15 -o BatchMode=yes"

mkdir -p "$LOCAL_DIR"

echo "[db-backup-pull] pulling dumps from $VPS_HOST:$REMOTE_DIR"
rsync -az -e "ssh $SSH_OPTS" \
  --include='radon-*.sql.gz' --exclude='*' \
  "$VPS_HOST:$REMOTE_DIR/" "$LOCAL_DIR/"

latest="$(ls -t "$LOCAL_DIR"/radon-*.sql.gz 2>/dev/null | head -1 || true)"
if [ -z "$latest" ] || [ ! -s "$latest" ]; then
  echo "[db-backup-pull] FAILED: no non-empty dump in $LOCAL_DIR after pull" >&2
  exit 1
fi
echo "[db-backup-pull] latest dump: $latest ($(du -h "$latest" | cut -f1))"

find "$LOCAL_DIR" -name 'radon-*.sql.gz' -mtime "+$RETENTION_DAYS" -delete
echo "[db-backup-pull] pruned local dumps older than $RETENTION_DAYS days"

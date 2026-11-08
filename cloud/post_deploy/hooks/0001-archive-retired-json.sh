#!/usr/bin/env bash
# Archive the retired JSON files once no released code reads them, then remove
# them so nothing can mistake a frozen snapshot for current state. trade_log.json
# holds the only full copy of the 2026 trade log rationale, so it is kept in the
# archive rather than deleted. Idempotent: a rerun with nothing left does nothing.
set -euo pipefail

cd "${RADON_DIR:?}/data"
archive_dir="${HOME:-/home/radon}/retired-json"
files=()
for name in portfolio.json trade_log.json orders.json blotter.json reconciliation.json option_session_mark_cache.json; do
  if [ -f "$name" ]; then
    files+=("$name")
  fi
done
if [ "${#files[@]}" -eq 0 ]; then
  exit 0
fi
mkdir -p "$archive_dir"
chmod 700 "$archive_dir"
archive="$archive_dir/retired-json-${RADON_DEPLOY_SHA:?}.tar.gz"
tar -czf "$archive.tmp" "${files[@]}"
tar -tzf "$archive.tmp" >/dev/null
chmod 600 "$archive.tmp"
mv "$archive.tmp" "$archive"
rm -f "${files[@]}"

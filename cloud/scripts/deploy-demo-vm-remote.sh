#!/usr/bin/env bash
# deploy-demo-vm-remote.sh -- runs ON the demo VM, fed over ssh by
# cloud/scripts/deploy-demo-vm.sh (`ssh radon-demo bash -s -- <mode> ... < this`).
#
#   deploy <sha> <tarball>   swap in the git-archive tarball, rebuild, health-gate,
#                            roll back automatically on any gate failure
#   rollback                 restore app.bak + radon-demo-api:rollback
#
# Layout (verified 2026-10-18): /opt/radon-demo/{docker-compose.yml,Dockerfile,
# .env,Caddyfile,app/,app.bak/}. The Dockerfile COPYs app/ into the image, so
# app/data/ (the flow_reports seed the TEST_MODE flow guard serves) must ride
# along on every build. It is copied verbatim into the new tree and never
# written; the previous tree, data included, is kept as app.bak.
set -euo pipefail

DEMO_ROOT="${DEMO_ROOT:-/opt/radon-demo}"
DOCKER="${DOCKER:-docker}"
CURL="${CURL:-curl}"
FLOCK="${FLOCK:-flock}"
IMAGE="${DEMO_IMAGE:-radon-demo-api}"
SERVICE="${DEMO_SERVICE:-api}"
EDGE_HOST="${DEMO_EDGE_HOST:-demo-api.radon.run}"
HEALTH_TIMEOUT_SECS="${DEMO_HEALTH_TIMEOUT_SECS:-120}"
HEALTH_POLL_SECS="${DEMO_HEALTH_POLL_SECS:-3}"
PROD_DB_MARKER="radon-joemccann"
DEMO_DB_MARKER="radon-demo"

APP="$DEMO_ROOT/app"
BAK="$DEMO_ROOT/app.bak"
STAGE="$DEMO_ROOT/.deploy-stage"
FAILED="$DEMO_ROOT/app.failed"

log() { printf '[demo-deploy] %s\n' "$*"; }
die() { printf '[demo-deploy] ERROR: %s\n' "$*" >&2; exit 1; }

compose() { (cd "$DEMO_ROOT" && "$DOCKER" compose "$@"); }
in_api() { compose exec -T "$SERVICE" "$@"; }

acquire_lock() {
  exec 9>"$DEMO_ROOT/.deploy.lock"
  "$FLOCK" -n 9 || die "another demo deploy holds $DEMO_ROOT/.deploy.lock"
}

# Poll the in-container /health until it answers (and, when given, reports
# the expected revision). Loopback inside the container is a trusted caller.
wait_internal_health() {
  local want="${1:-}" deadline body
  deadline=$(( $(date +%s) + HEALTH_TIMEOUT_SECS ))
  while :; do
    if body="$(in_api curl -fsS --max-time 5 http://127.0.0.1:8321/health 2>/dev/null)"; then
      if [[ -z "$want" || "$body" == *"\"revision\":\"$want\""* ]]; then
        return 0
      fi
    fi
    (( $(date +%s) >= deadline )) && return 1
    sleep "$HEALTH_POLL_SECS"
  done
}

# Through Caddy with the real Host header: the TrustedHostMiddleware pin
# (demo-api.radon.run in scripts/api/server.py _ALLOWED_HOSTS) is only
# exercised here; 127.0.0.1 health stays 200 while the edge 400s.
edge() {
  "$CURL" -sS --max-time 10 --resolve "$EDGE_HOST:443:127.0.0.1" "$@"
}

gate_edge_health() {
  local sha="$1" body
  body="$(edge -f "https://$EDGE_HOST/health")" || { log "gate: edge /health failed"; return 1; }
  [[ "$body" == *"\"revision\":\"$sha\""* ]] || { log "gate: edge /health revision != $sha"; return 1; }
}

gate_health_lite_auth() {
  local code token
  code="$(edge -o /dev/null -w '%{http_code}' "https://$EDGE_HOST/health/lite")" || code="000"
  [[ "$code" == "401" ]] || { log "gate: /health/lite without token returned $code, want 401"; return 1; }
  # Token is read from the running container and handed to curl on stdin, so
  # it never lands in argv, ps output, or this log.
  token="$(in_api printenv RADON_SERVICE_TOKEN 2>/dev/null)" || token=""
  [[ -n "$token" ]] || { log "gate: RADON_SERVICE_TOKEN unset in container"; return 1; }
  code="$(printf 'X-Radon-Service-Token: %s\n' "$token" \
    | edge -H @- -o /dev/null -w '%{http_code}' "https://$EDGE_HOST/health/lite")" || code="000"
  unset token
  [[ "$code" == "200" ]] || { log "gate: /health/lite with token returned $code, want 200"; return 1; }
}

gate_isolation() {
  local test_mode db_url
  test_mode="$(in_api printenv RADON_API_TEST_MODE 2>/dev/null)" || test_mode=""
  [[ "$test_mode" == "1" ]] || { log "gate: RADON_API_TEST_MODE != 1 in container"; return 1; }
  db_url="$(in_api printenv TURSO_DB_URL 2>/dev/null)" || db_url=""
  # Values are never printed: only which assertion failed.
  [[ "$db_url" == *"$DEMO_DB_MARKER"* ]] || { log "gate: container TURSO_DB_URL lacks $DEMO_DB_MARKER"; return 1; }
  [[ "$db_url" != *"$PROD_DB_MARKER"* ]] || { log "gate: container TURSO_DB_URL is the PROD database"; return 1; }
}

run_gates() {
  local sha="$1"
  wait_internal_health "$sha" || { log "gate: internal /health never reported revision $sha"; return 1; }
  gate_isolation || return 1
  gate_edge_health "$sha" || return 1
  gate_health_lite_auth || return 1
  log "gates: internal /health, isolation, edge /health, /health/lite auth all green"
}

do_rollback() {
  log "ROLLBACK: restoring $IMAGE:rollback and app.bak"
  "$DOCKER" image inspect "$IMAGE:rollback" >/dev/null 2>&1 || die "no $IMAGE:rollback image to restore"
  [[ -d "$BAK" ]] || die "no $BAK to restore"
  "$DOCKER" tag "$IMAGE:rollback" "$IMAGE:latest"
  rm -rf "$FAILED"
  if [[ -d "$APP" ]]; then mv "$APP" "$FAILED"; fi
  mv "$BAK" "$APP"
  compose up -d --no-build "$SERVICE"
  if wait_internal_health; then
    log "ROLLBACK complete: previous image serving"
  else
    die "ROLLBACK applied but internal /health is not answering"
  fi
}

preflight() {
  local f
  for f in docker-compose.yml Dockerfile .env; do
    [[ -f "$DEMO_ROOT/$f" ]] || die "missing $DEMO_ROOT/$f"
  done
  [[ -d "$APP/data" ]] || die "missing $APP/data (flow_reports seed); refusing to build without it"
  # Read, never print: the .env must already pin the isolation contract.
  grep -Eq "^RADON_API_TEST_MODE=[\"']?1[\"']?$" "$DEMO_ROOT/.env" \
    || die ".env does not set RADON_API_TEST_MODE=1"
  grep -Eq "^TURSO_DB_URL=.*$DEMO_DB_MARKER" "$DEMO_ROOT/.env" \
    || die ".env TURSO_DB_URL is not the $DEMO_DB_MARKER database"
  if grep -Eq "^TURSO_DB_URL=.*$PROD_DB_MARKER" "$DEMO_ROOT/.env"; then
    die ".env TURSO_DB_URL is the PROD database"
  fi
}

stage_tree() {
  local sha="$1" tarball="$2" listing
  # Inspect the member list before extracting anything.
  listing="$(tar -tf "$tarball")" || die "unreadable tarball: $tarball"
  if grep -Eq '(^|/)\._' <<<"$listing"; then die "archive carries AppleDouble (._*) entries"; fi
  if grep -Eq '^(\./)?data(/|$)' <<<"$listing"; then
    die "archive carries a top-level data/; refusing to overwrite the VM seed"
  fi
  rm -rf "$STAGE"
  mkdir -p "$STAGE/app"
  tar -xf "$tarball" -C "$STAGE/app"
  [[ -f "$STAGE/app/scripts/api/server.py" ]] || die "archive lacks scripts/api/server.py"
  [[ -f "$STAGE/app/requirements.txt" ]] || die "archive lacks requirements.txt"
  printf '%s\n' "$sha" > "$STAGE/app/REVISION"
  cp -a "$APP/data" "$STAGE/app/data"
  diff -r "$APP/data" "$STAGE/app/data" >/dev/null || die "data/ copy differs from the live seed"
}

do_deploy() {
  local sha="$1" tarball="$2" live cid image_id
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "sha must be 40 lowercase hex chars"
  [[ -f "$tarball" ]] || die "tarball not found: $tarball"
  preflight

  live="$(cat "$APP/REVISION" 2>/dev/null || true)"
  if [[ "$live" == "$sha" ]] && wait_internal_health "$sha" && gate_isolation && gate_edge_health "$sha"; then
    log "already live: $sha (no-op)"
    rm -f "$tarball"
    return 0
  fi

  stage_tree "$sha" "$tarball"

  cid="$(compose ps -q "$SERVICE")"
  [[ -n "$cid" ]] || die "no running $SERVICE container to snapshot for rollback"
  image_id="$("$DOCKER" inspect --format '{{.Image}}' "$cid")"
  "$DOCKER" tag "$image_id" "$IMAGE:rollback"
  log "tagged current image as $IMAGE:rollback"

  rm -rf "$BAK"
  mv "$APP" "$BAK"
  mv "$STAGE/app" "$APP"
  rmdir "$STAGE" 2>/dev/null || true
  log "swapped app/ (previous tree kept at app.bak)"

  if ! compose build "$SERVICE" || ! compose up -d "$SERVICE" || ! run_gates "$sha"; then
    log "deploy of $sha FAILED a gate"
    do_rollback
    exit 1
  fi
  rm -f "$tarball"
  log "LIVE $sha"
}

main() {
  local mode="${1:-}"
  [[ -d "$DEMO_ROOT" ]] || die "DEMO_ROOT $DEMO_ROOT does not exist"
  case "$mode" in
    deploy)
      [[ $# -eq 3 ]] || die "usage: deploy <sha> <tarball>"
      acquire_lock
      do_deploy "$2" "$3"
      ;;
    rollback)
      acquire_lock
      do_rollback
      ;;
    *) die "usage: $0 deploy <sha> <tarball> | rollback" ;;
  esac
}

main "$@"

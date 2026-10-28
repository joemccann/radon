#!/usr/bin/env bash
# Phase 0 — read-only VPS ground-truth capture. beta.radon.run is retired.
# Runs ON the Hetzner VPS. Captures NO secret values (RHS of secret-pattern
# env keys is redacted). Pure read-only: no systemctl mutations, no writes
# outside stdout.
set +e

sec() { printf '\n\n========== %s ==========\n' "$1"; }

# Redact values for secret-looking env keys; show everything else (ports/URLs/modes).
#
# DS-2026-10-28-07: keying on the text before the FIRST `=` of each physical
# line printed a systemd `Environment="SECRET=..."` directive verbatim (the
# inspected key was `Environment`), and the continuation lines of a quoted
# multi-line value were keyed on nothing and fell through the catch-all. The
# header above states a guarantee, so the key is resolved through a systemd
# `Environment=` wrapper and a quoted value is consumed to its real close.
SECRET_KEY_PATTERN='TOKEN|SECRET|PASSWORD|PASS|APIKEY|API_KEY|_KEY|PUBLISHABLE|JWKS|DSN|WEBHOOK|CLIENT_SECRET'
redact() {
  awk -v secret="$SECRET_KEY_PATTERN" '
    function emit(key, rest) { print key"=<redacted:"length(rest)"chars>" }
    # True while a quoted value opened on an earlier line is still open.
    function unclosed(s, q,   i, c, open) {
      open=0
      for (i=1; i<=length(s); i++) {
        c=substr(s, i, 1)
        if (c == "\\") { i++; continue }
        if (c == q) open=!open
      }
      return open
    }
    carry != "" {
      # A continuation of an already-redacted value: never print it, and stay
      # in carry until a line consumes the closing delimiter. Clearing on the
      # first line that merely held no quote resumed printing a value that
      # was still open.
      if (unclosed($0, carry)) carry=""
      next
    }
    /^[[:space:]]*#/ {print; next}
    /^[[:space:]]*$/ {print; next}
    /=/ {
      eq=index($0, "=")
      key=substr($0, 1, eq-1)
      rest=substr($0, eq+1)
      # systemd and dotenv both tolerate whitespace around the separator, so
      # the key run can end in a space or tab and the value can start after
      # one. Trimming both is what makes the name tests below see the name.
      sub(/^[[:space:]]+/, "", key); sub(/[[:space:]]+$/, "", key)
      sub(/^[[:space:]]+/, "", rest)
      # A systemd directive carries the real assignment inside its value.
      if (toupper(key) ~ /ENVIRONMENT$/) {
        inner=rest
        quote=""
        if (substr(inner, 1, 1) == "\"" || substr(inner, 1, 1) == "'"'"'") {
          quote=substr(inner, 1, 1)
          inner=substr(inner, 2)
        }
        outer=rest
        ieq=index(inner, "=")
        if (ieq > 0) {
          key=key"="substr(inner, 1, ieq-1)
          rest=substr(inner, ieq+1)
          # systemd accepts SEVERAL assignments in one directive, so every
          # name in the remainder is inspected and any secret among them
          # redacts the whole directive value. Checking the first assignment
          # alone printed the rest of the line unchecked.
          hit=0
          scan=inner
          while (match(scan, /[A-Za-z_][A-Za-z0-9_]*=/)) {
            name=substr(scan, RSTART, RLENGTH-1)
            if (toupper(name) ~ secret) { hit=1; break }
            scan=substr(scan, RSTART+RLENGTH)
          }
          if (hit) {
            # Only a redacted value suppresses its continuation lines; a
            # non-secret multi-line directive keeps printing as before.
            if (quote != "" && unclosed(outer, quote)) carry=quote
            emit(key, rest)
            next
          }
          print
          next
        }
      }
      if (toupper(key) ~ secret) {
        if (substr(rest, 1, 1) == "\"" || substr(rest, 1, 1) == "'"'"'") {
          quote=substr(rest, 1, 1)
          if (unclosed(rest, quote)) carry=quote
        }
        emit(key, rest)
        next
      }
      print
      next
    }
    {print}'
}

sec "HOST BASICS"
hostname; whoami; uname -a; cat /etc/os-release 2>/dev/null | grep -E '^(NAME|VERSION)='; uptime
echo "nproc: $(nproc)"; free -h; df -h / /home 2>/dev/null

sec "TAILSCALE"
tailscale status 2>/dev/null | head -20 || echo "tailscale: n/a"
tailscale ip 2>/dev/null

sec "HOME LAYOUT (/home/radon)"
ls -la /home/radon/ 2>/dev/null
echo "--- radon-cloud present? ---"; ls -d /home/radon/radon-cloud 2>/dev/null && echo PRESENT || echo ABSENT
echo "--- radon-beta present? ---"; ls -d /home/radon/radon-beta 2>/dev/null && echo PRESENT || echo ABSENT

sec "GIT STATE (radon-cloud)"
cd /home/radon/radon-cloud 2>/dev/null && {
  echo "branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
  echo "HEAD:   $(git rev-parse --short HEAD 2>/dev/null)"
  echo "remote:"; git remote -v 2>/dev/null
  echo "status (short):"; git status --short 2>/dev/null | head -20
}

sec "DEPLOY SCRIPT — locate"
echo "--- in repo? ---"; ls -la /home/radon/radon-cloud/scripts/deploy*.sh 2>/dev/null || echo "no scripts/deploy*.sh in repo"
echo "--- anywhere under radon-cloud? ---"; find /home/radon/radon-cloud -maxdepth 3 -iname 'deploy*.sh' 2>/dev/null
echo "--- in home? ---"; find /home/radon -maxdepth 2 -iname 'deploy*.sh' 2>/dev/null | head

sec "DEPLOY SCRIPT — contents (first match)"
DS=$(find /home/radon -maxdepth 3 -iname 'deploy*.sh' 2>/dev/null | head -1)
echo "FILE: $DS"
[ -n "$DS" ] && cat "$DS"

sec "SYSTEMD radon UNITS — list"
systemctl list-units 'radon-*' --all --no-pager 2>/dev/null
echo "--- timers ---"
systemctl list-timers 'radon-*' --all --no-pager 2>/dev/null
echo "--- unit files on disk ---"
ls -la /etc/systemd/system/radon-*.service /etc/systemd/system/radon-*.timer 2>/dev/null

sec "SYSTEMD UNIT FILE CONTENTS"
for u in /etc/systemd/system/radon-*.service /etc/systemd/system/radon-*.timer; do
  [ -f "$u" ] || continue
  echo "----- $u -----"
  redact < "$u"
done

sec "PORTS (listening)"
ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null

sec "POLKIT RADON RULE"
for f in /etc/polkit-1/rules.d/*radon*.rules /etc/polkit-1/rules.d/50-radon-services.rules; do
  [ -f "$f" ] || continue
  echo "----- $f -----"; cat "$f"
done
echo "--- sudoers radon (if any) ---"
sudo -n cat /etc/sudoers.d/*radon* 2>/dev/null || ls -la /etc/sudoers.d/ 2>/dev/null | grep -i radon || echo "no radon sudoers visible"

sec "CADDY — service + config location"
systemctl status caddy --no-pager 2>/dev/null | head -8
echo "--- caddy in docker? ---"; docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}' 2>/dev/null | grep -i caddy || echo "no caddy container"
echo "--- candidate Caddyfiles ---"
for f in /etc/caddy/Caddyfile /home/radon/radon-cloud/docker/caddy/Caddyfile /home/radon/radon-cloud/Caddyfile /home/radon/Caddyfile; do
  [ -f "$f" ] && echo "EXISTS: $f ($(wc -l < "$f") lines)"
done
find /home/radon -maxdepth 4 -iname 'Caddyfile' 2>/dev/null

sec "CADDY — LIVE config contents"
# Prefer the file the running caddy was started with.
CADDYFILE=""
if systemctl cat caddy >/dev/null 2>&1; then
  CADDYFILE=$(systemctl cat caddy 2>/dev/null | grep -oE '\-\-config [^ ]+' | awk '{print $2}' | head -1)
fi
[ -z "$CADDYFILE" ] && CADDYFILE=/etc/caddy/Caddyfile
echo "LIVE CADDYFILE GUESS: $CADDYFILE"
[ -f "$CADDYFILE" ] && cat "$CADDYFILE"
echo "--- also dumping repo docker/caddy/Caddyfile for diff ---"
cat /home/radon/radon-cloud/docker/caddy/Caddyfile 2>/dev/null

sec "ENV FILES — surface (values redacted for secrets)"
ENV_FILES=(/etc/radon/env /home/radon/radon-cloud/.env /home/radon/radon-cloud/web/.env)
for f in "${ENV_FILES[@]}"; do
  echo "----- $f -----"
  [ -f "$f" ] && redact < "$f" || echo "(absent)"
done

sec "TURSO / DB references in env (keys only)"
grep -rhiE 'TURSO|LIBSQL|DATABASE_URL|RADON_DB|RADON_MODE|NEXT_PUBLIC_RADON' "${ENV_FILES[@]}" 2>/dev/null | redact

sec "DOCKER (ib-gateway etc.)"
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || echo "docker n/a / no perm"

sec "DNS / what resolves here"
echo "app.radon.run -> $(getent hosts app.radon.run 2>/dev/null | awk '{print $1}')"
echo "media.radon.run -> $(getent hosts media.radon.run 2>/dev/null | awk '{print $1}')"
echo "beta.radon.run -> $(getent hosts beta.radon.run 2>/dev/null | awk '{print $1}' || echo 'NXDOMAIN (expected)')"

sec "CLIENT-ID usage hints (grep configs)"
grep -rhniE 'client_id|clientId|CLIENT_ID' "${ENV_FILES[@]}" 2>/dev/null | redact | head

sec "CAPTURE COMPLETE"
date -u +"%Y-%m-%dT%H:%M:%SZ"

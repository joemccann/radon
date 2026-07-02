# Radon Platform — Security Hardening Proposals

**Prepared for:** Radon operator (solo, live IB trading, real money)
**Scope:** app.radon.run + media.radon.run (5.78.148.38), demo-api.radon.run (5.78.181.75), Vercel frontends, Turso data tier, GitHub→SSH deploy pipeline
**Basis:** 48 adversarially-filtered findings from a repo config audit + live *external* perimeter probing (curl / openssl / dig / nc TCP-connect). No host was SSH'd into and nothing was mutated — all internal-posture claims are inferred from the repo and MUST be confirmed with the read-only Verify-on-Host checklist in §5.
**Method:** 66-agent workflow — 4 live-recon + 5 config-audit agents → refute-first adversarial verification per finding → synthesis. 56 raw → 48 survived (8 rejected). 0 critical, 0 high, 4 medium, 25 low, 19 info.
**Date:** 2026-07-02

---

## 1. Executive Summary

This is a genuinely well-hardened stack, and the audit should be read in that light. The high-value assets are correctly protected: IB Gateway (4001/4002) and VNC (5901) are loopback-bound and firewalled off the public internet; the FastAPI trusted-local bypass is *fail-safe by construction* (it trusts the **absence** of forwarding headers, which Caddy always stamps, so it cannot be spoofed from the internet); the WS relay closed its historic `socket.remoteAddress` bypass; the operator allowlist gates real trading behind Clerk + `ALLOWED_USER_IDS`; the IB Gateway image is SHA-pinned; CI is SHA-pinned with `contents:read` and a deploy job gated to push+main; gitleaks scans full history; and off-box DB backups use a deliberate no-`--delete` policy. **There is no unauthenticated internet-to-order path today.** Do not churn these controls.

The residual risk is almost entirely **blast-radius and single-point-of-failure**, not a live exploitable hole. The three biggest risks are: **(1)** the `willfarrell/autoheal` container mounts the Docker socket read-write while running from an **unpinned** image on the live-trading host — the one root-equivalent supply-chain gap on the most privileged container; **(2)** a **single shared Turso read+write+DDL token** is co-located on laptop, VPS, and CI with no read-only scoping and no per-host revocation — the largest data-tier blast radius; **(3)** **`git push origin main` is arbitrary code execution as `radon` on the live-trading host**, with branch-protection state unverified.

The framing that matters: an attacker's real prize here is **placing or altering live IB orders** and **reading/forging the canonical trade journal**. Every finding below is weighted by how much closer it moves an adversary to those two outcomes. Because the perimeter holds, the realistic path to that prize runs through **operator-endpoint compromise** (laptop or a leaked credential), so the highest-leverage hardening is reducing what a single laptop/credential compromise unlocks.

---

## 2. Attack-Surface Map

| Access point | Exposure | Auth control | Posture |
|---|---|---|---|
| SSH :22 (5.78.148.38 prod) | Public + Tailscale | Key-only, **root login used**, one key for both routes | **Weak** (root + key reuse; must stay open for CI) |
| SSH :22 (5.78.181.75 demo) | Public | Key-only | Adequate |
| Caddy :443 app.radon.run | Public | Clerk middleware + `ALLOWED_USER_IDS` operator allowlist | **Strong** |
| `/api/ib/*` → FastAPI :8321 | Public via Caddy (prefix stripped → full app surface) | Bearer Clerk JWT + `is_trusted_local_request` (fail-safe) + `AUTH_EXEMPT_PATHS` | **Strong** (single chokepoint; broad mount) |
| `/ws*` → relay :8765 | Public via Caddy | 30s single-use UUID4 ticket + forwarding-header trust check | **Strong** |
| media.radon.run :443 | Public | Read-only static, CORS `*`, no upload endpoint (rsync-only writer) | Adequate (no HSTS; immutable cache) |
| demo-api.radon.run :443 | Public (separate VM) | `RADON_SERVICE_TOKEN` bearer (constant-time), TEST_MODE, no IB | Adequate (no HSTS; uvicorn banner; unscoped token) |
| IB Gateway 4001 (live) / 4002 (paper) | **Loopback only** (127.0.0.1) | TWS client-id, `READ_ONLY_API=no` | **Strong binding** (accepted live-mode risk) |
| VNC :5901 | **Loopback only** | VNC password (weak on laptop) | Strong binding |
| Docker socket (autoheal) | Local (no network listener) | none (rw mount), **unpinned image** | **Weak** (supply-chain) |
| Turso libSQL cloud DB | **Cloud (internet-reachable w/ token)** | Single shared read+write+DDL bearer token | **Weak** (no scoping / per-host revocation) |
| GitHub → deploy (SSH) | GitHub-hosted runner → public :22 | SHA-pinned actions, push+main gate, host-key **not pinned** | Adequate (branch-protection unverified) |
| Tailnet (laptop ↔ VPS) | Tailscale WireGuard | `ufw allow in on tailscale0` (flat, no ACL); grants FastAPI Clerk-bypass | Adequate (flat trust; only member is the operator laptop) |

---

## 3. Threat Model — Paths an Adversary Would Actually Attempt

**T1 — Operator laptop compromise → total fleet takeover.** *Likelihood: low. Impact: catastrophic.*
The laptop concentrates: the shared Turso read+write+DDL token, the SSH key that logs in as **root** on the live-trading VPS over the *public* IP (no tailnet needed), the plaintext IB password file, and unencrypted plaintext DB backups. One endpoint compromise = read/write/drop the trade DB from anywhere + root on the trading host + IB credential + full account export. No lateral movement required. This is the dominant real-world path precisely because the network perimeter is strong.

**T2 — Leaked Turso token → trade DB read/write from anywhere.** *Likelihood: low-medium. Impact: high.*
The token is a cloud-reachable bearer credential present in three env locations (laptop, VPS, CI secret) plus every backup. Any single leak (env disclosure, CI secret exfil, errant backup) yields full read of all positions/P&L and, more damagingly, the ability to **forge or DROP `journal` rows** that drive portfolio basis and P&L — directly from the internet, no host foothold. Revocation nukes every host at once because there is one token.

**T3 — Push to `main` → RCE on the live-trading host.** *Likelihood: low. Impact: catastrophic.*
Any commit landing on `main` triggers CI → SSH into Hetzner → `deploy.sh` running as `radon` (with scoped sudo) on the box that owns IB Gateway :4001. A compromised maintainer GitHub credential, a self-approved PR (if reviews aren't required), or a direct push (if `main` is unprotected) becomes code-exec on the trading host → read the Turso token from `.env`, reach :4001, place live trades. Branch-protection state was never inspected; it is the only asserted barrier.

**T4 — Supply-chain compromise of `willfarrell/autoheal` → host root.** *Likelihood: very low. Impact: catastrophic.*
The autoheal image is unpinned. If upstream `:latest` is poisoned and the VPS performs a fresh pull/recreate, attacker code runs with the mounted `docker.sock` = host root on the live-trading box → IB secret, Turso token, live orders. Gated on an upstream event outside the attacker's control and a non-standing re-pull window — hence theoretical, but the impact ceiling is maximal and the container is the most-privileged one in the stack.

**T5 — Config regression re-opens the operator allowlist.** *Likelihood: low. Impact: high.*
`isAuthorizedUser` and the FastAPI mirror are **fail-open when the allowlist is empty**. On the *shared* `pk_live` Clerk instance (frictionless demo signups), if `ALLOWED_USER_IDS` is ever cleared/mis-set during a Vercel env edit, any signed-in user reaches operator pages and, via the trusted-local Next→FastAPI loopback path, real portfolio data and live-order routes. Global (no `user_id`) portfolio/journal tables mean there is no second line of defense. This is the 2026-06-27 incident class; the env var is the only thing standing.

---

## 4. Findings & Hardening Proposals

Severities reflect the adversarial re-rating: nearly everything is Medium-or-below because the perimeter holds and most paths require a precondition (host/laptop/credential compromise) not currently met. That does not make them unimportant — on a live-money system the blast-radius items are exactly where to invest.

### CRITICAL
*None.* No unauthenticated internet-to-order or internet-to-DB path exists in the current configuration.

### HIGH
*None currently exploitable.* The items with catastrophic impact ceilings (autoheal `docker.sock`, shared Turso token, push-to-main RCE) are all gated behind a precondition not met today, so they land at Medium exploitability — but they are the **top remediation priorities** because their impact is total. See the roadmap.

### MEDIUM

**M1 — `willfarrell/autoheal` mounts `docker.sock` rw from an unpinned image on the live-trading host.** *(idx 19, 32, 37)*
Container-escape-equivalent to host root, co-resident with the live gateway (`READ_ONLY_API=no`) and the IB password secret. `ib-gateway` is SHA-pinned; autoheal (`image: willfarrell/autoheal`, no tag/digest) is the lone exception. **Config-vs-reality is unresolved and must be checked on the host:** the **in-tree** `docker/ib-gateway/docker-compose.yml` ships autoheal + the `docker.sock` mount, and repo docs reference it as live — yet the memory lesson `feedback_watchdog_works_dont_deploy_autoheal` says autoheal was *rejected* for the IB gateway because a blind `docker restart` bypasses the `ib_2fa_lock` and stacks 2FA pushes. Whether autoheal is actually running on 5.78.148.38 was **not** verified (no SSH was performed in this audit) — run the `docker ps` / grep check in §5 to settle it. Either way the in-tree compose is a landmine: a `wipe-vps.sh` rebuild or a fresh `docker compose up` from this repo would deploy the unpinned socket-mounting container onto the live-trading host.
**Remediate:** Delete the `autoheal` service, the `autoheal=true` label, and the `docker.sock` mount from the in-tree compose; the watchdog already heals API hangs lock-respectfully. Fix the doc (`RELOGIN...no`). If any autoheal-style sidecar is ever justified, front the socket with `tecnativa/docker-socket-proxy` (restart/inspect only, read-scoped) and **digest-pin** the image the way `ib-gateway` is.
**Effort: S.**

**M2 — Single shared Turso read+write+DDL token, cloud-reachable, on laptop + VPS + CI.** *(idx 22, 29, 46, 48)*
The canonical trade journal + `portfolio_snapshots` (real net-liq/P&L/cash, ~1.4 GB) is reachable from anywhere with one full-privilege bearer token. No read-only variant, no per-host token, no expiry. The health-probe CI job holds full write but needs only read. One token means no per-consumer least-privilege and no per-host revocation.
**Remediate:**
```bash
# read-only for read-mostly consumers (health probe, dashboards, laptop dev):
turso db tokens create radon-joemccann --read-only --expiration 720h
# distinct write token for writers (monitor daemon, ib_sync, journal writers), per host:
turso db tokens create radon-joemccann --expiration 720h
```
Give the external-health-probe its own read-only token. Keep laptop and VPS on **distinct** tokens so either can be revoked without downing the other. Set expiry so they self-rotate. Stop shipping the `eyJ...`-shaped placeholder in `.env.example` — use `<read-only-token>`/`<write-token>` labels so a real token is never pasted over a realistic-looking dummy.
**Effort: M.**

**M3 — `git push origin main` = RCE as `radon` on the live-trading host; branch protection unverified.** *(idx 26)*
CI deploy job SSHes in and runs `deploy.sh` (`git reset --hard`, `pip install`, `npm build`, `systemctl restart radon-*`) on the box with live IB :4001. The `appleboy/ssh-action` step is already correctly SHA-pinned (good — the maintainer models this threat), but the barrier to *landing* a commit on `main` was never inspected.
**Remediate:** Enforce GitHub branch protection on `main`: require ≥1 PR review + CODEOWNERS, required status checks (`secret-scan`, `web-tests`, `py-tests`, `perimeter-smoke`) with `strict=true`, block force-push/deletion, `enforce_admins=true`. Gate the deploy job behind a **GitHub Environment with a required reviewer** so a live-trading host is never auto-deployed without a human gate. Verify:
```bash
gh api repos/:owner/:repo/branches/main/protection
```
**Effort: S.**

### LOW

**L1 — Operator authorization fails OPEN when `ALLOWED_USER_IDS` is unset, on the shared demo-capable Clerk instance.** *(idx 43)*
`isAuthorizedUser` returns `true` on an empty allowlist; FastAPI mirrors it. On prod the var is set and the gate works, but a single blanked/mis-set env edit re-opens real trading + account data to any signed-in demo user (global tables + trusted-local Next→FastAPI path). Currently mitigated because the var is set and test-pinned; the failure mode is a config slip, not a live hole.
**Remediate:** Make authz **fail-closed on production**. Gate on an explicit prod marker (e.g. `RADON_MODE=hetzner` / a `PROD` flag): when set, require `ALLOWED_USER_IDS` non-empty or refuse to serve; treat empty as **deny**. Add a startup assertion in `middleware.ts` and `auth.py` that logs loudly and denies when the prod marker is set but the allowlist is empty. Keep fail-open scoped to dev/CI/demo (which distinguishes the operator app from demo.radon.run by the var's presence — do not key this off `NODE_ENV`, which would break the demo Vercel deploy). **Effort: S.**

**L2 — Public-internet root SSH; one key authorizes both the Tailscale and public routes.** *(idx 23, 27, 28, 39)*
Root login is a routine operator path (`ssh root@ib-gateway`, `journal_pull.sh` default). Auth is key-only (BatchMode confirms no password), so no brute-force path, but public :22 must stay open because GitHub Actions SSHes over the public IP. One stolen laptop key = root on the trading host by either route, with no per-operator audit boundary. The pull-only keys carry no `command=` restriction, so a key meant for `journalctl`/rsync grants a full shell.
**Remediate:** `PermitRootLogin prohibit-password` (ideally `no`); deploy/administer as a non-root `radon` user with scoped sudo/polkit (the fleet already uses polkit for `radon-*` units). Give **CI its own dedicated deploy key/user** distinct from the operator key. Firewall public :22 to the CI runner egress + operator, forcing all other admin over Tailscale. Constrain pull keys: `command="...",no-pty,no-port-forwarding,no-agent-forwarding`. Add `fail2ban`. **Effort: M.**

**L3 — Caddy `handle_path /api/ib/*` strips the prefix and exposes the entire FastAPI surface through one auth chokepoint.** *(idx 17)*
Despite the name, the matcher isn't IB-scoped: `/api/ib/orders/place` → `/orders/place`, `/api/ib/admin/*` → `/admin/*`, `/pi/exec` all reachable. The perimeter holds today (middleware wraps all routes, fails closed), but a single `AUTH_EXEMPT_PATHS` or middleware-ordering mistake makes the whole order/exec/admin surface world-callable in one edit.
**Remediate:** Scope the Caddy matcher to the paths the browser actually needs (explicit allowlist matcher) so the reverse proxy enforces route scope independently of app-layer auth. Verify the live radon-cloud Caddyfile matches the repo copy via `caddy adapt` diff. **Effort: M.**

**L4 — No rate-limiting, request-body-size cap, or edge timeouts at Caddy.** *(idx 18)*
No `rate_limit`, `request_body max_size`, or `servers` timeout block. Clerk's hosted FAPI and the operator allowlist already blunt credential-stuffing (you can't brute-force a set-membership allowlist), so worst case is availability degradation (unbounded bodies, JWKS-refetch churn on a single uvicorn worker) against a solo-operator control plane.
**Remediate:** Add edge `read_header`/`read_body`/`idle` timeouts and `request_body max_size`; add a rate limit on sign-in + `/api/ib/*` (caddy-ratelimit plugin or a Hetzner Cloud Firewall connection limit). **Effort: S.**

**L5 — Live `ib-gateway` container runs a mutable tag on the VPS, not the digest the in-tree compose pins.** *(idx 38)*
Live compose: `ghcr.io/gnzsnz/ib-gateway:10.45.1b`; in-tree correctly pins `@sha256:22d5bf5e...`. A GHCR tag re-push (registry/namespace compromise) plus a pull event would inject code into the process holding the live IB session + password. Docker's default `pull-missing` means routine restarts don't re-fetch, so the path is doubly gated.
**Remediate:** Digest-pin the live radon-cloud compose to the same sha256 the in-tree compose uses; keep the two in sync (or have `deploy.sh` render one canonical file) so the pin cannot drift. **Effort: S.**

**L6 — Weak IB live-trading password (8-char keyboard-walk) stored plaintext at rest.** *(idx 35, 53)*
On the laptop `secrets/ib_password.txt` (gitignored, never committed — good); on the VPS a docker-secret file (the recommended mounted pattern — good). IBKR enforces server-side 2FA so the password alone can't take over the account, but an 8-char `vfr4****` pattern on a live brokerage login is poor hygiene.
**Remediate:** Rotate the IB password to a long random secret now. Keep the docker-secret pattern everywhere (never `env_file` plaintext into the container environment). On the laptop, prefer the OS keychain over a flat file; confirm `0600`. Confirm IBKR withdrawal/bank-instruction changes require separate 2FA. **Effort: S.**

**L7 — Unversioned `deploy.sh` executed by CI with no integrity check.** *(idx 30)*
CI runs `~/radon-cloud/scripts/deploy.sh` as `radon+sudo`; CI never pulls or checksums it and the VPS copy is edited in place, so it never passes the main-repo review gate. Requires a `radon` foothold to abuse (post-compromise persistence, not initial vector), but it evades branch protection.
**Remediate:** Bring `deploy.sh` under review (commit to the reviewed repo, or have CI checkout radon-cloud at a pinned SHA + verify a checksum before executing). Hash-pin Python deps for reproducible `pip install`. Restrict write perms on `~/radon-cloud/scripts` to a deploy-only account. **Effort: M.**

**L8 — Deploy-over-SSH and pull scripts don't pin the VPS host key (TOFU/MITM).** *(idx 31)*
`appleboy/ssh-action` passes no `fingerprint:`; `db_backup_pull.sh`/`journal_pull.sh` use `BatchMode=yes` with no `StrictHostKeyChecking`/pinned `known_hosts`. Most traffic rides Tailscale (already WireGuard-authenticated); the residual is the public-IP fallback + CI-runner→VPS path. Pubkey auth binds signatures to the session, so a rogue server can't steal the key — impact is a poisoned pulled archive or a hijacked session.
**Remediate:** `ssh-keyscan 5.78.148.38` once, pin the result: set the action's `fingerprint:` (as a secret) and add `-o StrictHostKeyChecking=yes -o UserKnownHostsFile=<pinned>` to the pull scripts. **Effort: S.**

**L9 — Flat tailnet grants FastAPI Clerk-bypass + IB reach; no Tailscale ACL, no IB Trusted-IP allowlist.** *(idx 34)*
A tailnet-sourced request with no forwarding headers bypasses Clerk on all routes (intentional, so the cloud-thin laptop can reach FastAPI). Today the only non-VPS tailnet member is the operator laptop, which already holds strictly greater access — so this is defense-in-depth against tailnet growth, not a live gap.
**Remediate:** Add Tailscale ACLs so only the specific laptop node may reach :8321 (and the gateway); set an IB Gateway API Trusted-IPs allowlist (loopback + laptop tailnet IP). Consider requiring a JWT even from the tailnet for order-placement/`/ib/restart`. **Effort: M.**

**L10 — Live FastAPI (:8321) and Next.js (:3000) bind `0.0.0.0`; host `ufw` is the sole barrier.** *(idx 36)*
`ufw` default-deny is verified working (external curls to :8321/:3000 drop). Note the trusted-local check denies any non-loopback/tailnet peer IP regardless, so even a ufw regression would **not** hand order/admin routes to the internet — only the trust-scoped exempt paths. Still, single-layer.
**Remediate:** Bind uvicorn and next-server to `127.0.0.1` (Caddy already proxies from loopback; expose an explicit extra bind for tailnet if wanted). Add a Hetzner Cloud Firewall as an independent second layer. Add a boot/CI assertion that :8321/:3000 are never publicly reachable. **Effort: S.**

**L11 — Off-box nightly DB dumps stored as unencrypted plaintext gzip SQL on VPS + laptop.** *(idx 50)*
Full point-in-time export of every trade + account balance; `gunzip | sqlite3` = the data, no credential. Marginal over the co-located live-DB token (an attacker who can read the dump already holds the token), and macOS FileVault mitigates laptop theft — but encrypt-at-rest is cheap. The good controls (no `--delete`, 48h freshness heartbeat, 30-day prune) are already in place — keep them.
**Remediate:** Pipe `db_backup.py` output through `age`/`gpg` before it lands on disk (laptop pull stays ciphertext), or store on an encrypted volume. Confirm `0600`/`0700` perms. **Effort: S.**

**L12 — Marketing-plate publish path can leak real account financials; the control is a manual pre-deploy memory gate.** *(idx 52)*
`site/CLAUDE.md` records that dashboard plates show REAL figures (net-liq, day P&L, cash) and must be swapped for synthetic before a Vercel push. Operator-controlled self-disclosure, not an attacker path, but irreversible.
**Remediate:** Make it mechanical: a CI/build check that fails if any committed `site/` plate matches real-figure patterns or lacks a `synthetic:true` sidecar; drive plates exclusively from the demo/seeded Turso DB. **Effort: M.**

**L13 — No key-rotation program.** *(idx 49)*
Only rotation guidance in-repo is the manual Turso re-issue; no cadence/owner/expiry for any secret. The single-shared-token design makes rotation a coordinated 3-file edit + restart, which structurally discourages it.
**Remediate:** Adopt short-expiry tokens (Turso `--expiration`, Clerk rotation, Flex refresh) with a documented cadence + named owner; script the multi-file update (`rotate-turso.sh` writing all env files + `radon restart`) so rotation is one command. Record last-rotated dates. Natural companion to M2. **Effort: M.**

**L14 — Secret + access concentration on the laptop.** *(idx 51)*
Turso token + SSH-root-to-VPS (both routes, one key) + IB password + plaintext backups, all on one machine — a single laptop compromise is total (T1). Largely addressed by M2/L2/L11 collectively.
**Remediate:** Read-only Turso token for the laptop (M2); drop laptop SSH from root to scoped `radon` (L2); Tailscale-only key for the private route; encrypt the backup copy (L11); confirm FileVault enabled. **Effort: M.**

**L15 — VNC password hygiene (loopback-only, low impact).** *(idx 40)*
Both VNC binds are `127.0.0.1` (good); reachable only via an SSH tunnel. Laptop `.env` uses weak `radon2026`.
**Remediate:** Keep loopback-only; use a long random VNC password on the laptop, stored outside plaintext `.env`; confirm the VPS `VNC_SERVER_PASSWORD` is strong/non-default. **Effort: S.**

### INFO (hardening niceties + confirmed-working controls)

- **Header hygiene** *(idx 0, 1, 4, 8):* `x-powered-by: Next.js`, `x-middleware-rewrite`/`x-clerk-auth-reason`, `via: 1.1 Caddy`, and demo-api's `server: uvicorn` leak version-less framework fingerprints. Set `poweredByHeader:false`; strip these at Caddy (`header -x-middleware-rewrite`, `header -Server`, `header -via`). No version disclosed anywhere. **Effort: S.**
- **HSTS missing on media + demo-api** *(idx 7, 11):* Add `Strict-Transport-Security: max-age=31536000; includeSubDomains` to those Caddy site blocks (manage from a shared snippet to prevent drift). Impact bounded — media is public read-only, demo-api is server-to-server. **Effort: S.**
- **CSP absent on 404 / Clerk-rewrite / FastAPI JSON responses** *(idx 2):* Nonce-CSP only on rendered pages. Emit a baseline `default-src 'self'; frame-ancestors 'none'` as a Caddy `header` on the app block so error/interstitial surfaces inherit it. `X-Frame-Options: DENY` + `nosniff` already cover clickjacking/MIME everywhere. **Effort: S.**
- **`/ws-ticket/validate` unnecessarily auth-exempt** *(idx 44):* Its only caller (loopback relay) is already trusted-local. Remove it from `AUTH_EXEMPT_PATHS` to shrink the anonymous surface to `{/health, /demo/trial-expiry}`. **Effort: S.**
- **Demo-api error oracle + unscoped `RADON_SERVICE_TOKEN`** *(idx 12, 45):* Collapse missing-vs-invalid to a single generic `401`. Confirm the demo VM's Turso credential is a **separate demo-scoped read-only** token, not the shared prod token. Token is correctly server-only (inert on prod, unset there). **Effort: S.**
- **media immutable-cache + CORS `*`** *(idx 21):* Content-hash paths or shorter `max-age` so replacements get new URLs; enforce an images-only content-type allowlist; constrain rsync writes to the media subtree. Low priority (public read-only images). **Effort: S.**
- **Extend `scrubSecrets()` coverage** *(idx 55):* Apply the same redaction at the FastAPI error boundary and log formatters, not just the Next.js `jsonApiError` string path. **Effort: S.**

**Confirmed-working controls — do NOT churn** *(idx 3, 9, 10, 25, 33, 41, 42, 54):* CORS is a restrictive origin allowlist (`allow_credentials=False`, no ACAO reflected to evil origins); WS relay ticket auth rejects ticketless/bogus upgrades (historic `remoteAddress` bypass closed via forwarding-header check); FastAPI trusted-local is **fail-safe by construction** — internet clients cannot forge X-Forwarded-For to gain trust because Caddy always stamps it and trust requires its *absence*; auth middleware fails closed (503 on missing JWKS); IB/VNC ports loopback-bound; live compose sets `no-new-privileges`, `privileged:false`; CI is SHA-pinned with `contents:read`, deploy gated to push+main, fork PRs run without secrets; gitleaks scans full history with a checksum-pinned binary; no hardcoded secrets in-repo, `.env*` correctly ignored.

---

## 5. Verify-on-Host Checklist (read-only)

The repo audit could not settle config-vs-reality on the following. Run these; they mutate nothing.

**On 5.78.148.38 (prod / live-trading):**
```bash
# Bindings — confirm 8321/3000 not on 0.0.0.0 after L10; IB/VNC loopback
ss -tlnp | grep -E ':(22|3000|4001|4002|5901|8321|8765)\b'
# Firewall — expect default-deny incoming, only 22/80/443 + tailscale0
sudo ufw status verbose
# sshd — want permitrootlogin no|prohibit-password, passwordauthentication no
sudo sshd -T | grep -Ei 'permitrootlogin|passwordauthentication|pubkeyauthentication|listenaddress|kbdinteractive'
# brute-force protection present?
fail2ban-client status sshd 2>/dev/null || echo "NO fail2ban"
# authorized_keys — key count + command= restrictions on pull keys
awk '{print NR": "$1" "$2}' ~radon/.ssh/authorized_keys; grep -c 'command=' ~radon/.ssh/authorized_keys
# autoheal / docker.sock present? (expect clean per live recon — re-confirm)
docker ps --format '{{.Names}}\t{{.Image}}'; grep -n 'autoheal\|docker.sock' ~/radon-cloud/docker-compose.yml
# ib-gateway pinned by digest? (L5)
grep -n 'ib-gateway:' ~/radon-cloud/docker-compose.yml
# IB password not in container env (L6)
docker inspect ib-gateway --format '{{json .Config.Env}}' | grep -i pass && echo "LEAK: password in env" || echo "ok: not in env"
# secret + backup perms (want 600/700)
ls -l ~/radon-cloud/secrets/ ~/radon-cloud/backups/db/ 2>/dev/null
# live Caddyfile matches repo? (L3) — VPS copy is authoritative
caddy adapt --config /etc/caddy/Caddyfile 2>/dev/null | head; grep -n 'handle_path /api/ib' /etc/caddy/Caddyfile
# deploy.sh drift (L7)
ls -l ~/radon-cloud/scripts/deploy.sh; git -C ~/radon-cloud status --short
```

**On 5.78.181.75 (demo VM):**
```bash
ss -tlnp | grep -E ':(22|80|443)\b'
sudo ufw status verbose
grep -i turso /path/to/demo/.env   # confirm demo-scoped read-only token, NOT the prod token (idx 12/45)
```

**GitHub (from laptop):**
```bash
gh api repos/:owner/:repo/branches/main/protection   # expect required PR reviews + strict status checks + enforce_admins + no force-push (M3)
```

**Laptop:**
```bash
fdesetup status                     # FileVault ON (L11/L14)
ls -l docker/ib-gateway/secrets/ib_password.txt data/db_backups/   # perms; plan encryption
```

---

## 6. Prioritized Roadmap

**Quick wins (do this week — all S, high leverage):**
1. **Verify GitHub branch protection on `main` + gate the deploy job behind a required-reviewer Environment** (M3, T3). Highest-impact-per-effort: closes the push-to-main→RCE path.
2. **Digest-pin autoheal (or delete it) + digest-pin the live `ib-gateway` compose** (M1, L5, T4). Delete the autoheal landmine from the in-tree compose and fix the `RELOGIN` doc while you're there.
3. **Mint a read-only Turso token for the health probe and laptop; issue distinct laptop vs VPS write tokens** (M2 first pass, T2). Even partial scoping removes CI's over-privilege and enables per-host revocation.
4. **Make operator authz fail-closed on prod** (L1, T5) — a startup assertion keyed on a prod marker converts a one-env-var slip from "world-open trading" to "locked out."
5. **`PermitRootLogin prohibit-password` + `fail2ban` + a dedicated CI deploy key** (L2). Bind uvicorn/next to loopback (L10). Strip fingerprint headers, add HSTS to media/demo-api, remove `/ws-ticket/validate` from exempt paths (Info batch).

**Medium projects (this month):**
6. **Scoped-token rollout + one-command rotation script** (M2 full + L13): read-only for read consumers, per-host write tokens, expiry, `rotate-turso.sh`.
7. **Firewall public :22 to CI egress + operator, force all other admin over Tailscale; add Tailscale ACLs + IB Trusted-IP allowlist** (L2, L9).
8. **Bring `deploy.sh` under review + host-key pinning for CI/pull SSH** (L7, L8).
9. **Encrypt off-box DB backups at rest** (L11); **rotate the IB password to a long random secret** (L6).
10. **Mechanical marketing-plate PII gate in CI** (L12).

**Larger / optional (consider):**
11. **Split IB Gateway onto its own VM / Tailscale-only host** the way the demo backend already is (idx 20), so an edge compromise on 5.78.148.38 cannot reach the live API on loopback. Biggest structural blast-radius reduction; largest effort — weigh against solo-operator ops overhead.
12. **Scope the Caddy `/api/ib/*` matcher to a vetted path allowlist** (L3) so the reverse proxy enforces route scope independently of app-layer auth.

**Explicitly do nothing to:** the loopback IB/VNC bindings, the forwarding-header WS/FastAPI trust model, the enforced nonce-CSP on rendered pages, the operator allowlist logic (just make its empty-set fail-closed on prod), the SHA-pinned CI actions, and the no-`--delete` backup policy. These are the load-bearing controls keeping the perimeter intact — leave them alone.
# Monorepo migration: fold `radon-cloud` into `radon/cloud`

**Date:** 2026-07-10  
**Status:** Phase 2 cutover complete on production (2026-07-11). Pre-cloud rollback drill still open.
**Goal:** One git SHA for app + production infra so deploy tooling, systemd, Compose, and application code cannot drift.
**Production green SHA at cutover close:** `06e683e5` (control-plane ready, schema-v2 `ok=true`, IB authenticated).

---

## Why

- CI deploys **app** from `radon` but runs **`deploy.sh` from a separate VPS checkout** of `radon-cloud` that is not auto-updated by that deploy.
- Dual IB Gateway compose files, dual Bun pins, dual service lists, dual control-plane contracts.
- Single-operator product: multi-repo overhead without multi-team isolation benefit.

---

## Target layout

```text
radon/                          # sole product repo
  web/
  scripts/
  docker/                       # local/dev IB Gateway recipes
  cloud/                        # former radon-cloud (production infra)
    services/
    scripts/deploy.sh
    docker-compose.yml
    caddy/
    config/
    tests/
  .github/workflows/ci.yml      # tests app + cloud; deploys one SHA
```

**VPS (after cutover):**

| Path | Role |
|---|---|
| `/home/radon/radon` | Monorepo checkout (app + `cloud/`) |
| `/home/radon/radon/cloud` | Infra + deploy scripts (same SHA as app) |
| `/home/radon/.radon-deploy-runners/<sha>.<run>/cloud` | Immutable support bundle; current plus four newest inactive bundles retained |
| `/home/radon/radon-cloud/.env` | Temporary stable secrets location only (`0600`, `radon:radon`) |
| `/var/lib/radon/deploy` | Reboot-durable root topology transition state |
| `/var/lib/radon/control-plane-ready` | Root-published compatibility marker for monorepo deploys |

---

## Phases

### Phase 1 — Import (this change)

1. Copy `radon-cloud` tree into `radon/cloud/` (exclude `.git`, `.venv`, caches, `media/`, `state/`, secrets, and `security-archive/` which remains only in the legacy cloud history).
2. Detect monorepo layout in `cloud/scripts/deploy.sh` defaults.
3. CI contains a readiness-gated monorepo deploy path and keeps the secret file
   at `~/radon-cloud/.env`; the legacy runner remains active until root
   bootstrap publishes compatibility.
4. CI runs `cloud/` pytest alongside app tests.
5. Document; do **not** delete the `radon-cloud` GitHub repo yet.

### Phase 2 — VPS cutover (operator, post-green deploy)

1. Let the readiness-gated compatibility deploy land `cloud/` and restart the
   isolated health producer. It uses the legacy runner only while the root
   readiness marker is absent, then validates that `/status` publishes `ok`
   and `overall_state` without requiring the broker to be healthy.
2. From a root session, install the exact target control-plane bundle without
   restarting any service or IB Gateway:
   ```bash
   cd /home/radon/radon
   bash cloud/scripts/bootstrap-control-plane.sh
   ```
   The script acquires the deploy lock, refuses pending app/Gateway
   transitions, validates and atomically installs root-owned helpers, policies,
   and changed units, performs `systemctl daemon-reload`, verifies hashes, and
   only then publishes `/var/lib/radon/control-plane-ready`.
3. Verify `/usr/local/bin/radon-ib-gateway-control` uses
   `/home/radon/radon/cloud` and the external env path. Do not restart Gateway
   during cutover; a restart can trigger IB 2FA.
4. Confirm the next deploy uses an immutable exact-SHA runner and passes the
   installed-control-plane manifest preflight.
5. Archive `radon-cloud` as read-only only after rollback and probe checks are
   green.

### Phase 3 — Contract collapse (follow-up)

1. Shared image-digest pin tested once for `docker/ib-gateway` + `cloud/docker-compose.yml`.
2. Remove legacy dual-path docs and dead env defaults.

---

## Rollback

### A. Code rollback (before or after push)

```bash
# On a bad monorepo commit:
git revert <monorepo-merge-sha>   # or reset --hard pre-merge if not shared
git push origin main
```

Before the root readiness marker exists, CI falls back to:

```bash
cd ~/radon-cloud && bash scripts/deploy.sh '$SHA'
```

The compatibility wrapper then explicitly restarts `radon-health` and validates
the aggregate payload schema. It never gates on IB/broker state. After root
bootstrap publishes readiness, CI refuses new releases whose SHA predates
`cloud/`; rollback inside an in-flight immutable runner retains a compatibility
copy of `cloud/` so canonical installed unit paths remain valid.

### B. VPS rollback (if monorepo deploy path misbehaves)

1. Ensure legacy checkout is intact:
   ```bash
   ls ~/radon-cloud/scripts/deploy.sh
   test -f ~/radon-cloud/.env
   ```
2. Force next deploy through legacy (temporary CI pin or manual):
   ```bash
   cd ~/radon-cloud
   bash scripts/deploy.sh <known-good-radon-sha>
   ```
3. If the app tree was hard-reset badly, restore from the green marker and
   release artifacts per `docs/operations.md`. Deploy journal operations run
   from the retained immutable runner, so recovery remains available even when
   the previous commit has no `cloud/` tree.

Successful deploys serialize runner retention with extraction, hold an activity
lock on the current runner, keep it plus the four newest other runners, and
remove only inactive older runners. Failed deploys do not prune recovery
evidence; the next successful deploy performs bounded cleanup.

### C. Do not

- Do not delete `~/radon-cloud/.env` during migration.
- Do not symlink the whole `~/radon-cloud` path to the monorepo while `.env`
  lives inside the legacy directory.
- Do not `rm -rf ~/radon-cloud` until at least one readiness-gated monorepo
  deploy and a pre-cloud rollback exercise are green.
- Do not run full `setup-vps.sh` as a live control-plane upgrade; it mutates
  packages, Caddy, firewall, and service state.
- Do not force-push monorepo history rewrites to `main` after the first production deploy from it.

---

## Success criteria

- [x] `cloud/` present in radon with deploy, services, tests
- [x] CI deploy script monorepo-aware with legacy fallback
- [x] Documented rollback path
- [x] Full app pytest + web vitest + cloud pytest green (4054 / 4082 / 556)
- [x] Compatibility deploy publishes the new health schema
- [x] Root bootstrap publishes a verified readiness marker without a Gateway restart
- [x] First immutable-runner deploy succeeds (CI `29157182912`, SHA `06e683e5`)
- [ ] Forced rollback to a pre-cloud SHA restores artifacts and clears its journal

---

## Live cutover notes (2026-07-11)

### Order of operations that worked

1. Deploy a readiness-gated release so `cloud/` and schema-v2 `radon-health` land (broker health advisory).
2. As root, from `/home/radon/radon`: `bash cloud/scripts/bootstrap-control-plane.sh` (no Gateway restart).
3. Recover IBKR 2FA via `/usr/local/bin/radon-ib-gateway-control` only.
4. Confirm `/status` `schema_version=2 ok=true overall_state=up` and pool 3/3 connected.
5. Subsequent deploys use immutable runners under `~/.radon-deploy-runners/` and require Production environment approval.

### Production env contract after cutover

| Key | Value |
|---|---|
| Secrets file | `/home/radon/radon-cloud/.env` mode `0600` |
| `IB_GATEWAY_MODE` | `cloud` |
| `RADON_MODE` | `hetzner` |
| `IB_GATEWAY_COMPOSE_DIR` | `/home/radon/radon/cloud` |
| Readiness marker | `/var/lib/radon/control-plane-ready` |
| Green deploy marker | `/home/radon/.radon-last-green-deploy` |

### Defects that only appeared on the live host

Documented fully in `tasks/lessons.md` (2026-07-11). Short list:

- Bootstrap must preseed shell helpers before `systemd-analyze verify`.
- Gateway control root demotion must `cd` to a radon-readable home; `/root` cwd breaks Compose.
- Deploy preflight must not require radon-readable installed sudoers; root helper owns that contract.
- Output-trace excludes for host `data/**` are mandatory on disk-fallback API routes (VPS data >> local).
- Host needs Bun 1.3.14 on radon PATH; prune must chmod u+w before deleting a-w runners.

---

## Out of scope for Phase 1

- Deleting the `radon-cloud` remote
- Rewriting all historical docs links
- Merging local vs production Compose into one file
- Rotating credentials (T8)

# Monorepo migration: fold `radon-cloud` into `radon/cloud`

**Date:** 2026-07-10  
**Status:** shipped (Phase 1)  
**Goal:** One git SHA for app + production infra so deploy tooling, systemd, Compose, and application code cannot drift.

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
| `/home/radon/radon-cloud/.env` | **Stable secrets location** (symlink or `RADON_DEPLOY_ENV_FILE`) |
| `/home/radon/radon-cloud` | Legacy path; optional symlink to `radon/cloud` |

---

## Phases

### Phase 1 — Import (this change)

1. Copy `radon-cloud` tree into `radon/cloud/` (exclude `.git`, `.venv`, caches, `media/`, `state/`, secrets, and `security-archive/` which remains only in the legacy cloud history).
2. Detect monorepo layout in `cloud/scripts/deploy.sh` defaults.
3. CI deploy prefers monorepo `cloud/scripts/deploy.sh` when present at the release SHA; keeps env file at `~/radon-cloud/.env`.
4. CI runs `cloud/` pytest alongside app tests.
5. Document; do **not** delete the `radon-cloud` GitHub repo yet.

### Phase 2 — VPS cutover (operator, post-green deploy)

1. After first monorepo deploy lands `cloud/` on the box:
   ```bash
   # optional convenience
   ln -sfn /home/radon/radon/cloud /home/radon/radon-cloud-mono
   # keep .env where it is; export RADON_DEPLOY_ENV_FILE=/home/radon/radon-cloud/.env
   ```
2. Re-install systemd units from `cloud/services/` via setup/operator path when units change.
3. Confirm `IB_GATEWAY_COMPOSE_DIR` points at monorepo compose dir.
4. Archive `radon-cloud` repo as read-only with README pointing here.

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

CI falls back to:

```bash
cd ~/radon-cloud && bash scripts/deploy.sh '$SHA'
```

when `cloud/scripts/deploy.sh` is absent at the requested SHA (legacy path).

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
3. If app tree was hard-reset badly, restore from green marker / previous release artifacts per `docs/operations.md` and `cloud/scripts/deploy.sh` journal recovery.

### C. Do not

- Do not delete `~/radon-cloud/.env` during migration.
- Do not `rm -rf ~/radon-cloud` until at least one monorepo deploy is green and units are reinstalled from `cloud/`.
- Do not force-push monorepo history rewrites to `main` after the first production deploy from it.

---

## Success criteria

- [x] `cloud/` present in radon with deploy, services, tests
- [x] CI deploy script monorepo-aware with legacy fallback
- [x] Documented rollback path
- [x] Full app pytest + web vitest + cloud pytest green (4054 / 4082 / 556)
- [ ] First production deploy after push succeeds (operator verify via `gh run list`)

---

## Out of scope for Phase 1

- Deleting the `radon-cloud` remote
- Rewriting all historical docs links
- Merging local vs production Compose into one file
- Rotating credentials (T8)

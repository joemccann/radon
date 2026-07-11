# Radon Cloud

Production infrastructure for Radon lives in the main `radon` monorepo under
`cloud/`. The standalone `radon-cloud` repository and checkout are migration
compatibility only. Application code and production infrastructure must be
tested at one Git SHA.

## Layout

```text
cloud/
  caddy/                     # public edge and reverse proxy
  config/                    # sudoers, polkit, required env contract
  scripts/
    bootstrap-control-plane.sh
    deploy.sh
    deploy-root-helper.sh
    ib-gateway-control.sh
    setup-vps.sh
  services/                  # canonical systemd units and timers
  tests/                     # cloud configuration and recovery tests
  docker-compose.yml         # production IB Gateway container
```

## Canonical Host Paths

- Monorepo checkout: `/home/radon/radon`
- Cloud source: `/home/radon/radon/cloud`
- Immutable deploy support: `/home/radon/.radon-deploy-runners/<sha>.<run>/cloud`
- Temporary stable secret file: `/home/radon/radon-cloud/.env`, mode `0600`,
  owner `radon:radon`
- Durable privileged deploy state: `/var/lib/radon/deploy`
- Control-plane manifest/readiness:
  `/var/lib/radon/control-plane-manifest.sha256` and
  `/var/lib/radon/control-plane-ready`

The legacy directory is not a code source. Do not symlink the whole
`/home/radon/radon-cloud` directory while the secret file lives inside it.
Code paths, working directories, Compose, drift audit, helpers, and units use
the monorepo cloud path. `EnvironmentFile=/home/radon/radon-cloud/.env` is the
single deliberate migration exception.

## Deployment Contract

Pushes to `main` run the root CI workflow. The deploy job:

1. Fetches the exact tested SHA.
2. Before the root readiness marker exists, uses the known legacy runner,
   explicitly restarts `radon-health`, and validates only the `/status` schema
   (`ok` plus `overall_state`). Broker health is advisory and cannot fail this
   compatibility check.
3. After privileged bootstrap, extracts `cloud/` from the exact SHA into a
   retained immutable runner. It never rewrites live `cloud/` before the deploy
   lock is held.
4. Holds an activity lock through deploy, then on success keeps the current
   runner plus the four newest other runners and prunes only inactive older
   bundles under the serialized runner index lock.
5. Validates the external secret file and Compose render without copying
   secrets into the checkout.
6. Verifies the installed root control plane against the root-written manifest
   before any dependency build, service stop, or transition journal write.
7. Builds frozen Bun workspaces and Python wheels in a detached worktree before
   teardown.
8. Fsyncs a durable transition journal, snapshots active services and timers,
   promotes artifacts, restores the prior topology, and runs code-controlled
   gates.
9. Commits the topology transition and green marker only after verification.

The journal helper is loaded from the immutable runner so rollback to a commit
that predates `cloud/` cannot delete its own recovery implementation. Root
topology state is durable across reboot under `/var/lib/radon/deploy`.

## Privileged Bootstrap

`scripts/bootstrap-control-plane.sh` is the only live-host upgrade path for the
root-owned control-plane bundle. Run it as root from the exact target cloud
source. It acquires the deploy lock, refuses pending app or Gateway transitions,
validates candidates, atomically installs helpers/policies/changed units,
performs one `systemctl daemon-reload`, verifies hashes, and publishes the
readiness marker.

It must not start, stop, restart, or enable Radon services, Docker, IB Gateway,
Caddy, polkit, or journald. Do not use the full `setup-vps.sh` as a live upgrade
shortcut; setup also provisions packages, firewall, Caddy, and service state.

## Privilege Boundary

- `/usr/local/sbin/radon-deploy-root` owns fixed deploy lifecycle actions.
- `/usr/local/bin/radon-ib-gateway-control` is the single Gateway container
  lifecycle path and holds the deploy/control lock.
- `/usr/local/bin/radon` is the root-owned validating operator.
- Sudoers permits exact helper/operator invocations, not arbitrary
  `systemctl`, Docker, shell, or file mutation.
- The watchdog may start only the fixed preheld Gateway restart adapter through
  the scoped polkit rule.

Never add a second Gateway restart owner. Docker restart policy remains `no`;
IBC self-restart knobs remain disabled; deploys exclude Gateway-owned units.

## Environment Handling

- `config/required-env.txt` is the shared required-key contract.
- `scripts/check-env.py` validates names, permissions, literal values, and mode
  consistency without sourcing secrets.
- Compose interpolation and service `env_file` both receive the explicit
  external env path through `RADON_COMPOSE_ENV_FILE`.
- `web/.env` contains only `NEXT_PUBLIC_*` build values and is mode `0600`.
  Never copy the complete production env into the web tree.
- Setup validates the stable env before dependency installation or builds.

## Systemd And Drift

Canonical unit files are copied root-owned to `/etc/systemd/system`; they are
not symlinked from the checkout. Unit changes require the non-restarting
control-plane bootstrap or an equivalent reviewed root transaction.

The drift audit runs from `/home/radon/radon/cloud` and compares live Caddy,
Compose, systemd, polkit, sudoers, and installed helpers with this source. It
must never read or report `.env*` contents.

`radon-health.service` remains runtime-isolated from the trading cascade: no
Gateway `Requires=` or `After=` dependency. Coordinated release restart and
schema validation do not change that zero-shared-fate runtime design.

## Verification

Run from the monorepo root:

```bash
python3.13 -m pytest -q cloud/tests
bash -n cloud/scripts/*.sh
RADON_COMPOSE_ENV_FILE=/path/to/test.env \
  docker compose --env-file /path/to/test.env \
  -f cloud/docker-compose.yml config --quiet
gitleaks detect --source . --config cloud/.gitleaks.toml --redact --no-banner
git diff --check
```

Deployment, rollback, locking, bootstrap, and unit-path changes require
adversarial regression coverage. Tests must use isolated roots and must never
write host `/etc`, `/usr/local`, `/var/lib`, production data, or real secrets.

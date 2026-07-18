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

First-time cutover must preseed staged shell helpers at their final paths
before `systemd-analyze verify` (units reference absolute ExecStart paths that
do not exist yet). Remove only those seeds if verification fails. After any
live hot-patch of an installed control-plane file, re-run bootstrap so the
readiness manifest hashes match the next release.

### Safe control-plane refresh (2026-07-18)

Bootstrap validates the current `/home/radon/radon/cloud` contents, not the
Git relationship between that checkout and the CI-tested release. Before any
root operation, prove the VPS checkout is the intended tested commit and, if
it is behind, fast-forward it as `radon` without resetting or checking out
unreviewed code:

```bash
TARGET_SHA=<exact-tested-sha>
sudo -u radon -H git -C /home/radon/radon fetch --prune origin
TARGET_COMMIT="$(sudo -u radon -H git -C /home/radon/radon rev-parse "${TARGET_SHA}^{commit}")"
CURRENT_COMMIT="$(sudo -u radon -H git -C /home/radon/radon rev-parse HEAD)"
if [ "$CURRENT_COMMIT" != "$TARGET_COMMIT" ]; then
  sudo -u radon -H git -C /home/radon/radon merge --ff-only "$TARGET_COMMIT"
fi
test "$(sudo -u radon -H git -C /home/radon/radon rev-parse HEAD)" = "$TARGET_COMMIT"

cd /home/radon/radon
bash cloud/scripts/bootstrap-control-plane.sh
```

Do not restart Gateway during this sequence, and do not bypass the installed
control-plane manifest preflight. Re-run the CI deploy for the same tested SHA
after bootstrap, wait for it to finish, then verify `/status` reports schema
v2 with `ok=true` and `overall_state=up` and that API, Next.js, relay, monitor,
newsfeed, and health services are active.

Operational evidence: target `20c4b14b` initially appeared current while the
VPS checkout was stale; the fast-forward precondition corrected it. Bootstrap
then installed and verified 20 artifacts. CI rerun `29630430683` deployed
successfully, and the stated schema and core-service checks were green.

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

Root demotion of gateway control must run with a radon-readable cwd
(`HOME=/home/radon` and `cd /home/radon`). Demoting while cwd is `/root` makes
Compose fail with `stat .: permission denied` and can leave
`/var/lib/radon/ib-gateway-transition.json` stuck.

Deploy preflight runs as `radon`. Installed `0440` sudoers are not
radon-readable; source-to-manifest matching is non-privileged, and installed
target existence/hash/mode is verified only by
`sudo -n /usr/local/sbin/radon-deploy-root verify-control-plane`.

Immutable runners under `~/.radon-deploy-runners/` are extracted `a-w`.
`prune-deploy-runners.py` must restore write bits before deletion.

## Environment Handling

- `config/required-env.txt` is the shared required-key contract.
- `scripts/check-env.py` validates names, permissions, literal values, and mode
  consistency without sourcing secrets.
- Production invariants on Hetzner monorepo hosts:
  `IB_GATEWAY_MODE=cloud`, `RADON_MODE=hetzner`, `IB_GATEWAY_HOST=127.0.0.1`,
  `NODE_ENV=production`.
- Compose interpolation and service `env_file` both receive the explicit
  external env path through `RADON_COMPOSE_ENV_FILE` (stable file:
  `/home/radon/radon-cloud/.env`).
- `web/.env` contains only `NEXT_PUBLIC_*` build values and is mode `0600`.
  Never copy the complete production env into the web tree.
- Setup validates the stable env before dependency installation or builds.
- Host needs the exact Bun pin on radon PATH for staged Next builds.

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

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

## Runtime Planes

Production is three planes. Do not collapse them.

- **Host plane** (never container): systemd, journald, polkit, sudoers,
  Caddy, Tailscale, Docker engine, `radon-health` on `127.0.0.1:8330`,
  `/usr/local/sbin/radon-deploy-root`, `/usr/local/bin/radon-ib-gateway-control`.
- **Broker plane** (already Docker): digest-pinned IB Gateway in
  `cloud/docker-compose.yml`. This is the only production container.
- **App plane** (host today, images later): Next.js, FastAPI, relay,
  monitor, newsfeed, and timer-owned oneshots. Default `RADON_RUNTIME=host`.
  `docker/app` Dockerfiles are scaffolding only; they are not production
  runtime and CI deploy does not build them. App-plane images must not own
  Gateway, Caddy, health, or `docker.sock`.

After `radon-deploy-root refresh-control-plane` is installed (helper +
sudoers), a unit-only push does not need root SSH. The SHA that *adds*
that sudoers verb still needs one root `bootstrap-control-plane.sh`.
Refresh copies changed control-plane units and `daemon-reload`s; it does
not start, stop, or restart Gateway.

## Canonical Host Paths

- Monorepo checkout: `/home/radon/radon`
- Cloud source: `/home/radon/radon/cloud`
- Immutable deploy support: `/home/radon/.radon-deploy-runners/<sha>.<run>/cloud`
- Canonical future secret file: `/etc/radon/env` (regular file, mode `0600`)
- Compatibility secret file (until one green host cutover):
  `/home/radon/radon-cloud/.env`, mode `0600`, owner `radon:radon`
- Durable privileged deploy state: `/var/lib/radon/deploy`
- Control-plane manifest/readiness:
  `/var/lib/radon/control-plane-manifest.sha256` and
  `/var/lib/radon/control-plane-ready`

The legacy directory is not a code source. Do not symlink the whole
`/home/radon/radon-cloud` directory while the secret file lives inside it.
Code paths, working directories, Compose, drift audit, helpers, and units use
the monorepo cloud path. `EnvironmentFile=/home/radon/radon-cloud/.env` remains
the unit path until one green host cutover onto `/etc/radon/env`.

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
- Canonical future secrets path: `/etc/radon/env`. Compatibility path
  `/home/radon/radon-cloud/.env` remains until one green host cutover.
  `deploy.sh` prefers `/etc/radon/env` when that path is a regular file.
  Unit `EnvironmentFile=` is unchanged.
- Compose interpolation and service `env_file` both receive the explicit
  external env path through `RADON_COMPOSE_ENV_FILE` (compatibility file:
  `/home/radon/radon-cloud/.env`).
- `docker/app` images are not production runtime yet. Default remains
  host binaries (`RADON_RUNTIME=host`). Do not add an image-build job to
  CI deploy `needs`.
- `web/.env` contains only `NEXT_PUBLIC_*` build values and is mode `0600`.
  Never copy the complete production env into the web tree.
- Setup validates the stable env before dependency installation or builds.
- Host needs the exact Bun pin on radon PATH for staged Next builds.

## Systemd And Drift

Canonical unit files are copied root-owned to `/etc/systemd/system`; they are
not symlinked from the checkout.

Bootstrap installs only the control-plane units. Every other unit
(the timer-owned scans and their timers) is installed by the CI deploy:
after the promote and before `restart-managed`, `deploy.sh` runs
`radon-deploy-root install-units`, which copies each unit whose
`cloud/services/` content hashes to its entry in
`config/installed-units.sha256`, daemon-reloads once, and `enable --now`s
NEW timers only (never a timer-owned `.service`). The manifest digest is the
review gate: editing a unit in `services/` and bumping its hash in the same
commit is the whole procedure -- no root SSH. An edit whose hash is NOT
bumped is left uninstalled (the drift audit then flags it), so the
drift-allowlist acknowledgment still covers a deliberate pending window;
`tests/test_unit_install_acknowledgment.py` fails CI otherwise. A unit
absent from the manifest is never installed.

Allowlisted units (`config/auto-sync-units.txt`) get a second, tighter
publish after a green deploy: `radon-deploy-root sync-scheduled-units`
reads git objects at the GitHub `joemccann/radon` main tip (not the
radon-writable checkout), requires the blob SHA-256 to match the
manifest, installs `0644 root:root`, and `daemon-reload`s. It never
starts, stops, or enables units.

After promote, `deploy.sh` runs `radon-deploy-root refresh-control-plane`
(unit-class diffs only: `services/*` at `0644 root:root`, one
`daemon-reload`). It does not start, stop, or restart Gateway. Privileged
diffs (`scripts/*`, `config/*`) fail closed unless root runs
`refresh-control-plane-privileged`, which is not in sudoers. The SHA
that adds the `refresh-control-plane` sudoers verb still needs one
`bootstrap-control-plane.sh`. After that verb is installed, a unit-only
push does not need root SSH.

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

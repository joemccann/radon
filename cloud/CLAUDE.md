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
- **App plane** (host default, images optional): Next.js, FastAPI, relay,
  monitor, newsfeed, and timer-owned oneshots. Default `RADON_RUNTIME=host`.
  Images live in `docker/app`. Production ExecStart stays host binaries
  until per-unit `runtime-container.conf` drop-ins are installed. The
  root wrapper is `/usr/local/sbin/radon-app-runtime` (`pull` via sudoers;
  `run` is systemd-only and does not take the deploy lock). Do not put
  `User=radon` on `docker run`. App-plane images must not own Gateway,
  Caddy, health, or the Docker engine socket. Image builds are a separate
  workflow, not a `ci.yml` deploy `needs`.

After `radon-deploy-root refresh-control-plane` is installed (helper +
sudoers), a unit-only push does not need root SSH. The SHA that *adds*
that sudoers verb still needs one root `bootstrap-control-plane.sh`.
Refresh copies changed control-plane units and `daemon-reload`s; it does
not start, stop, or restart Gateway. Since R-430 the deploy job also runs
`radon-deploy-root sync-control-plane` first, so privileged diffs (helper,
sudoers, polkit) converge on GitHub main without root SSH too.

**Refresh installs git blobs, never the working tree.** The sources it copies
into `/etc/sudoers.d`, `/usr/local/sbin`, `/etc/polkit-1` and the unit
directory come from `git cat-file blob` at the deployed commit, staged
root-owned under `/var/lib/radon/deploy/control-plane-src.*`. That commit is
the local HEAD, and it must be reachable from the GitHub main tip -- an
ancestor, so a rollback still installs its own release's control plane, but
never a commit main has not contained. `/home/radon/radon` is writable by
`radon`, which holds a NOPASSWD verb for `refresh-control-plane-privileged`;
reading the checkout there made those bytes a root install with only syntax
validation in front of them. Same rule `install-units` has always followed
(R-084).

The five app-plane drop-ins run the container with the systemd notify
socket proxied: `radon-app-runtime run` spawns `notify-proxy` inside the
unit cgroup and mounts ITS socket as the container's `NOTIFY_SOCKET`. A
datagram sent from the container's own PIDs (in `system.slice/docker-*.scope`)
is dropped by systemd regardless of `NotifyAccess=`, so `Type=notify` +
`WatchdogSec=` work only through the proxy (R-429).

## Canonical Host Paths

- Monorepo checkout: `/home/radon/radon`
- Cloud source: `/home/radon/radon/cloud`
- Immutable deploy support: `/home/radon/.radon-deploy-runners/<sha>.<run>/cloud`
- Canonical secrets: `/etc/radon/env` (regular file, mode `0640`, owner `root:radon`)
- Compatibility secret symlink: `/home/radon/radon-cloud/.env` -> `/etc/radon/env`
- Canonical media: `/var/lib/radon/media`
- Compatibility media symlink: `/home/radon/radon-cloud/media` -> `/var/lib/radon/media`
- Durable privileged deploy state: `/var/lib/radon/deploy`
- Control-plane manifest/readiness:
  `/var/lib/radon/control-plane-manifest.sha256` and
  `/var/lib/radon/control-plane-ready`

The legacy directory is not a code source. Do not symlink the whole
`/home/radon/radon-cloud` directory. Code paths, working directories, Compose,
drift audit, helpers, and units use the monorepo cloud path. Units load
`EnvironmentFile=/etc/radon/env`; the exception is `radon-mcp.service`, which
loads the stripped `/etc/radon/mcp.env` that `deploy.sh:write_mcp_env` derives
from it.

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
   teardown, then pre-pulls the release's app image pair
   (`radon-app-runtime pull <sha>`, sudoers-granted) while the current
   release still serves; the same verb drops SHA-tagged pairs that are
   neither the target nor in use by a running container (R-431).
8. Fsyncs a durable transition journal, snapshots active services and timers,
   promotes artifacts, restores the prior topology, and runs code-controlled
   gates.
9. Commits the topology transition and green marker only after verification.

The journal helper is loaded from the immutable runner so rollback to a commit
that predates `cloud/` cannot delete its own recovery implementation. Root
topology state is durable across reboot under `/var/lib/radon/deploy`.

**Auto-deploy mechanics (moved from root `CLAUDE.md`).** `.github/workflows/ci.yml` runs the Vitest + pytest gate (including `cloud/tests`) then deploys on green: it SSHes to Hetzner, materializes an immutable `cloud/` runner from the release SHA under `~/.radon-deploy-runners/`, and runs that runner's `deploy.sh '$SHA'`. The deploy job remains bound to the GitHub Environment `Production` for deployment history, URL metadata, environment-scoped configuration, and a main-only deployment branch policy; it has no required-reviewer rule, so no manual approval is needed after the automated gates pass. Host secrets stay at `~/radon-cloud/.env` (`RADON_DEPLOY_ENV_FILE`). After root bootstrap publishes `/var/lib/radon/control-plane-ready`, legacy dual-checkout deploy is retired for new releases; pre-ready SHAs still use the compatibility path. Before `deploy.sh`, the deploy job runs `cloud/scripts/sync-control-plane.sh` → `radon-deploy-root sync-control-plane`, which installs the GitHub-main-tip control-plane bundle (helper, sudoers, polkit, control-plane units, drop-ins) via that tip's own bootstrap, so control-plane edits and root hot-patches need no manual bootstrap (R-430, 2026-08-29). Confirm: `gh run list --workflow=ci.yml --limit 1`. Migration/rollback: `docs/monorepo-cloud-migration.md`. Cutover lessons: `tasks/lessons.md` (2026-07-11). The deploy health-gates the relay restart: before tearing services down (while the current radon-api still serves `/health`), `wait_for_gateway_ready` confirms the IB gateway is authenticated + port_listening (bounded 60s, warn-and-proceed). The relay self-heals on reconnect and raises a `service_health` row (`ib-realtime-relay`) instead of looping silently on no-ticks.

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

### Automated control-plane sync (2026-08-29, R-430)

The deploy job runs `cloud/scripts/sync-control-plane.sh` (as `radon`, from
the immutable runner) before `deploy.sh`. It calls the sudoers verb
`radon-deploy-root sync-control-plane`: root resolves the GitHub main tip,
extracts `cloud/` at that commit from git objects into a root-only staging
tree under `/var/lib/radon/deploy/`, and runs that tip's own
`bootstrap-control-plane.sh` (deploy lock, transition refusal, validation,
atomic install, manifest rewrite). A bundle that is already current is a
no-op. Helper, sudoers, polkit, control-plane unit and drop-in edits, and
root hot-patches of installed drop-ins, therefore reconcile on the next green
push without root SSH. The verb takes no argument: radon cannot hand root a
tree, only ask it to converge on GitHub main. Prestage skips (does not fail)
when the bundle is not ready. The manual sequence below remains the recovery
path when the verb itself is not yet granted or GitHub is unreachable.

Exit semantics (R-437, R-440): 75 (deploy lock held, pending transition) is
retried and then handed to `deploy.sh`. Any other failure, a bootstrap
rejection or the helper's 300s deadline (124), fails the deploy job and is
recorded in `/home/radon/.radon-control-plane-rejected`; while that file
exists `deploy.sh`'s preflight refuses to apply a differing bundle "after
promote" (the every-deploy `refresh_install_file` path now carries
bootstrap's full validator table anyway). A bootstrap TERMed after its
`daemon-reload` restores the previous readiness marker with the previous
bundle; only a reload systemd refused leaves readiness withdrawn. A missing
marker on a host whose units carry `runtime-container.conf` drop-ins makes
the deploy job refuse (exit 78) instead of using the legacy runner.

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
  `IB_GATEWAY_MODE=cloud`, `RADON_MODE=hetzner`, `NODE_ENV=production`.
  `IB_GATEWAY_HOST=127.0.0.1` on `RADON_HOST_ROLE=combined` (default) and
  `broker`. App-role hosts must use an RFC1918 address, never Tailscale
  CGNAT and never a public NIC. See `docs/spof-host-split.md`.
- Canonical future secrets path: `/etc/radon/env`. Compatibility path
  `/home/radon/radon-cloud/.env` remains until one green host cutover.
  `deploy.sh` prefers `/etc/radon/env` when that path is a regular file.
  Unit `EnvironmentFile=` is unchanged.
- Compose interpolation and service `env_file` both receive the explicit
  external env path through `RADON_COMPOSE_ENV_FILE` (compatibility file:
  `/home/radon/radon-cloud/.env`).
- `docker/app` images are not production runtime until the per-unit
  drop-ins are installed. Default remains host binaries
  (`RADON_RUNTIME=host`). `.github/workflows/app-images.yml` builds
  images and is not a `ci.yml` deploy `needs`.
- `web/.env` contains only `NEXT_PUBLIC_*` build values and is mode `0600`.
  Never copy the complete production env into the web tree.
- Setup validates the stable env before dependency installation or builds.
- Host needs the exact Bun pin on radon PATH for staged Next builds.

**IB Flex / Gateway env (Hetzner `/etc/radon/env` mode `0640`, owner `root:radon`; moved from root `CLAUDE.md`):** `IB_FLEX_TOKEN`, `IB_FLEX_QUERY_ID=1422766` (blotter), `IB_FLEX_NAV_QUERY_ID=1442520` (Activity query "Equity Summary in Base"; carries THREE sections — NAV in Base, Cash Transactions and Transfers — don't repurpose for trade pulls), `IB_GATEWAY_MODE=cloud` (production; FastAPI must not own Compose), `IB_GATEWAY_COMPOSE_DIR=/home/radon/radon/cloud` (monorepo path; not `~/radon-cloud`), `RADON_MODE=hetzner`.

**Verify Flex query ids against the real env before trusting one written down anywhere** — a stale id documented here once pointed the runbook at a query that does not exist for this account. `IB_FLEX_FLOWS_QUERY_ID` is deliberately unset: `_flows_query_id()` falls back to the NAV id and `resolve_flows` reuses the one fetched document, so a run makes ONE Flex request. Setting it to a second id doubles the request rate against a token that has already taken a 24h-to-168h throttle embargo.

**Backblaze B2 (portfolio cold-archive, production required):** `RADON_ARCHIVE_S3_ENDPOINT`, `RADON_ARCHIVE_S3_BUCKET`, `RADON_ARCHIVE_S3_ACCESS_KEY_ID`, `RADON_ARCHIVE_S3_SECRET_ACCESS_KEY`, `RADON_ARCHIVE_S3_REGION` (+ optional `RADON_ARCHIVE_S3_PREFIX`). S3-compatible API to bucket `radon-archive`. Used by `radon-portfolio-archive.service` / `scripts/archive_portfolio_snapshots.py`. Not Cloudflare R2. Full contract: root `.env.example`, `docs/cloud-services.md` "Portfolio archive".

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
absent from the manifest is never installed. `install-units` journals what
it promoted under `/var/lib/radon/deploy`; a rollback runs
`radon-deploy-root revert-units` after the checkout restore and before
`recover`, which disables and removes the units the failed release added and
restores the bodies it replaced. `commit-transition` closes the journal.

Allowlisted units (`config/auto-sync-units.txt`) get a second, tighter
publish after a green deploy: `radon-deploy-root sync-scheduled-units`
reads git objects at the GitHub `joemccann/radon` main tip (not the
radon-writable checkout), requires the blob SHA-256 to match the
manifest, installs `0644 root:root`, and `daemon-reload`s. It never
starts, stops, or enables units.

After promote, `deploy.sh` runs `radon-deploy-root refresh-control-plane`
(unit-class diffs only: `services/*` at `0644 root:root`, one
`daemon-reload`). It does not start, stop, or restart Gateway. Privileged
diffs (`scripts/*`, `config/*`) fail closed unless the installed sudoers
grants `refresh-control-plane-privileged`; then preflight warns and the
deploy applies the hashed privileged refresh. The SHA that *adds* that
sudoers verb still needs one `bootstrap-control-plane.sh`. After that
verb is installed, helper/sudoers/polkit edits deploy without root SSH.

The drift audit runs from `/home/radon/radon/cloud` and compares live Caddy,
Compose, systemd, polkit, sudoers, and installed helpers with this source; on
`RADON_HOST_ROLE=app` the Compose and `ib-gateway-control` surfaces are
role-skipped because Gateway runtime surfaces are absent by design there. It
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

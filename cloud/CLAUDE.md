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
    host-firewall.sh         # operator-run ufw rulesets (app/broker), never on deploy
    ib-gateway-control.sh
    setup-vps.sh
  services/                  # canonical systemd units and timers
  tests/                     # cloud configuration and recovery tests
  docker-compose.yml         # production IB Gateway container
```

## VIX model spread schedule

`radon-vix-model-spread.service` and `.timer` are included in setup's
`SERVICE_FILES` and `config/installed-units.sha256`; the normal green deploy
installs them and enables the timer without an operator root install. The
personal-use collector samples official Cboe closes daily at 02:50 UTC, with
no randomized delay. Migration 0098, payload/storage and operator-only access
are defined in [the indicator spec](../docs/indicators/vix-model-spread.md).

## Taiwan and Korea export sampling

`radon-asia-export-lead.service` and `.timer` are included in setup's
`SERVICE_FILES` and `config/installed-units.sha256`; a normal green deploy
installs both and enables the daily 09:40 UTC timer. The collector uses free
official monthly total USD export histories, with attribution, and needs no
additional credentials or operator installation. Migration 0099 creates
`asia_export_lead_history`; the `asia-export-lead` snapshot and heartbeat
share a 26-hour freshness window. Source failures retain the last good
snapshot. See [the indicator specification](../docs/indicators/exports.md).
The MSCI forward-EPS comparison remains unavailable pending licensed access.

## NYSE common-stock High-Low sampling

`radon-high-low-line.service` and `.timer` are setup-listed and manifest-pinned;
normal deploy installs both and enables daily 00:30 UTC sampling, weekends included,
with a 300-second service deadline. Approved StockCharts `!NEWHINYC`/`!NEWLONYC`
history is fetched with honest `radon/2.0` HTTP, no new credentials. Migration 0101
stores daily and weekly rows; snapshot/health key `high-low-line` has a 26-hour
window. Published fractional readings are preserved, cumulative baseline is zero
before 2003-01-02, and errors preserve the last good snapshot. Source lag is a
payload condition separate from writer health. API use is operator-only; no demo
mirror. Contract: [High-Low Line](../docs/indicators/high-low.md).

## Runtime Planes

Production is three planes. Do not collapse them.

- **Host plane** (never container): systemd, journald (drop-in `journald.conf.d/zz-radon.conf`: 1G cap, no syslog copy), polkit, sudoers,
  Caddy, Tailscale, Docker engine, `radon-health` on `127.0.0.1:8330`,
  `/usr/local/sbin/radon-deploy-root`, `/usr/local/bin/radon-ib-gateway-control`.
- **Broker plane** (already Docker): digest-pinned IB Gateway in
  `cloud/docker-compose.yml`. App images have a separate release boundary.
- **App plane:** Next.js, FastAPI, relay, monitor, newsfeed and timer-owned
  oneshots. The installed per-unit `runtime-container.conf` determines container
  execution; the host environment default alone does not identify the running
  plane. `setup-vps.sh` installs those per-service drop-ins itself
  (`install_container_dropins`, staged root-owned through
  `stage_from_checkout`) BEFORE `enable_services` and `start_services`, so a
  fresh host never starts a unit carrying `LoadCredentialEncrypted` while it
  still has the base `User=radon` `ExecStart`; waiting for the first
  control-plane refresh to supply the drop-in handed `radon` the decrypted
  secret-store master key for that window. `install_fleet_dropin` still
  installs only `common.conf`. [The runtime owner](../docs/cloud-services.md#runtime-planes)
  and checked-in `docker/app` plus unit drop-ins own the configuration.
  `/usr/local/sbin/radon-app-runtime` is the root wrapper (`pull` and no-argument `prune` via sudoers;
  `run` is systemd-only and does not take the deploy lock). Do not put
  `User=radon` on `docker run`. App containers must not own Gateway, Caddy,
  health or the Docker engine socket.
  The root [CI workflow](../.github/workflows/ci.yml) makes `app-images` a required
  deploy dependency; [image admission tests](tests/test_ci_deploy_image_reuse.py)
  pin the gate and exact-SHA pair proof. The build-only workflow does not replace it.

After `radon-deploy-root refresh-control-plane` is installed (helper +
sudoers), a unit-only push does not need root SSH. The SHA that *adds*
that sudoers verb still needs one root `bootstrap-control-plane.sh`.
Refresh copies changed control-plane units and `daemon-reload`s; it does
not start, stop, or restart Gateway. Since R-430 the deploy job also runs
`radon-deploy-root sync-control-plane` first, so privileged diffs (helper,
sudoers, polkit) converge on GitHub main without root SSH too.

**`radon` is not in group `docker`.** That group is root-equivalent: a member
mounts the host into a container and walks out as root. The account held it
only to drive the `ib-gateway` container, and that now goes through
`/usr/local/sbin/radon-docker-gw` — root-owned, a fixed verb set
(`compose-up`, `compose-down`, `inspect-running`, `pgrep-jvm`, `pgrep-java`,
`thread-dump <pid>`, `logs`, `stats`, `ps`) against a pinned container, each
verb listed in full in `sudoers.d/radon-ops` with no wildcard. Callers:
`ib-gateway-control.sh` (still `User=radon`, so the 2FA lease and guard files
under `/var/lib/radon` keep their ownership) and `scripts/jvm_forensics.py`.

`compose-up` refuses with exit 73 while the IBKR operator hold
(`/var/lib/radon/ib-operator-hold.json`) is set, and a root-only `kill` verb
(not in sudoers) backs `radon ib release`. `setup-vps.sh install_ib_hold`
installs that command root-owned as `/usr/local/sbin/radon-ib-hold`, and the
stdlib hold CLI it runs (`python3.13 -I`) as `/usr/local/lib/radon/ib_operator_hold.py`,
never the radon-owned checkout copy; runbook
in `docs/ib-gateway-recovery.md`. The compose body pins
`EXISTING_SESSION_DETECTED_ACTION=primaryoverride`, outside the env file, so a
logged-in Gateway yields to the operator's own IBKR login. A changed compose
body reaches the broker only through a manual reinstall of
`/etc/radon/ib-gateway-compose.yml` and a container recreate, which is one login.

The compose body it runs lives at `/etc/radon/ib-gateway-compose.yml`, a
control-plane artifact, NOT `cloud/docker-compose.yml` in the checkout: root
acting on a file its caller can rewrite is the same escalation with extra
steps. The shim refuses a symlinked or non-root-owned body, and likewise refuses
`/etc/radon/env` when it is a symlink, not root-owned, or group/other-writable
(`require_trusted_env_file`, REL-234 — both refusals exit 78; an exit 78 from
`ib-gateway-control.sh` during recovery means fix the file's ownership/perms,
not the Gateway), and
`--project-name cloud` is pinned so the `cloud_ib-config` volume — the
Gateway's Jts settings and 2FA state — survives the move.

The newsfeed Chromium seccomp profile is the same kind of artifact:
`cloud/config/seccomp/chromium.json` installs root-owned at
`/etc/radon/seccomp/chromium.json` (bootstrap kind `seccomp`, refresh arm
`*/radon/seccomp/*.json`, `setup-vps.sh:install_app_runtime`, drift pair
`chromium-seccomp`). Every install path refuses a body that does not parse or
does not deny by default (`defaultAction` `SCMP_ACT_ERRNO`), and
`radon-app-runtime` refuses to start `radon-newsfeed.service` without it.
`/etc/radon` is radon-writable, so every install path also refuses a target
directory that is a symlink or not root-owned; recovery is removing that
directory as root and rerunning the install.

`config-check` is `deploy.sh`'s preflight compose render. It takes no
env-file argument on purpose — a caller-supplied path is the one thing the
shim refuses — and pins the same `/etc/radon/env` the deploy's
`ENV_FILE_DEFAULT` resolves to in production. When the shim refuses (or for a
dev/test invocation against some other env file), `preflight_env` falls back to
a direct `docker compose config` — including in production, where a shim
refusal that the direct render covers emits the countable journal line
`[preflight] compose-render: shim refused (exit N), direct render covered`
(R-647/REL-242: the fallback deliberately does not abort the deploy, because
the shim's sudoers verb arrives with the release being promoted). A compose
body that fails both paths still fails preflight: preferring one path
would deadlock the deploy across the sudoers-verb / docker-group transition,
since whichever mechanism the current host lacks would abort the promote that
installs it (`deploy.sh:204-231`, bd7d7e4c). Note the
narrowing: preflight now renders the INSTALLED compose body, not the incoming
release's. The incoming body is gated at install time instead, by provenance
(git blob at the deployed commit, read from the root provision store and
reachable from the main commit root reads from the pinned GitHub URL - a
local-only commit is refused, as is an unreachable remote) plus
`compose_body_is_valid`.

**`publish-caddy` stages from the trusted tip too.** The edge config decides
which proxy and fetch-metadata headers survive on the way to the API's
local-trust check, and `radon` both owns the checkout copy and holds a NOPASSWD
grant for the verb. `caddy validate` proves the candidate parses and nothing
about what it does, so `stage_caddy_candidate` reads the blob at the commit the
remote reports for the release branch (`resolve_fetched_main_tip`) rather than
the working tree - the same anchor the control-plane refresh uses. A
checkout-only edit never reaches the live configuration.

That validator's deny-list refuses EVERY host-namespace join, not only
`pid:` — `ipc:`, `userns_mode:`, `uts:` and `cgroup:` widen the container's
runtime the same way (R-668/REL-249), and the deny greps match quoted values
too (`privileged: "true"` / `'true'` — the bare-`true` grep alone waved the
quoted form through until T-441 caught it). `deploy-root-helper.sh` additionally refuses
`RADON_DEPLOY_HELPER_TEST_MODE=1` outright when its effective uid is 0, ahead
of the first `RADON_TEST_*` expansion: the mode substitutes systemctl, rm,
sync, the session python and setpriv from the environment, and the helper is
a NOPASSWD root chokepoint. The three copies
(deploy-root-helper, bootstrap-control-plane, setup-vps) stay byte-identical;
a parity test in `cloud/tests/test_rel234_compose_gate.py` pins them. Their
body predicates consume here-strings, never `printf | grep -q` or another
early-exit producer pipeline: under `pipefail`, SIGPIPE can reject a safe body
or bypass a forbidden match. Large-body regressions exercise both outcomes
in all three copies.

**The broker uses a separate bundle.** `radon-deploy-root sync-control-plane`
reads its `broker` role from the host environment file and invokes the trusted
bootstrap with that role. The broker installs only the deploy helper, Gateway
control helpers, the existing root-owned operator hold CLI, compose and broker
units. It installs no app runtime, sudoers, polkit, app units or container
drop-ins, and requires neither Node nor visudo. It does not restart Gateway.
Initial provisioning uses root SSH and a root-owned archive of the tested
commit; subsequent syncs use the pinned root provision store. Root release
controllers may pass `sync-control-plane <sha>`: the exact SHA must still be
the pinned remote main tip at admission and immediately before bootstrap.
Success prints `installed_sha=<sha> host_role=<role>` after the transaction
and floor record succeed. The deploy job passes the tested SHA (DS-2026-10-18-01):
without it the three expected-SHA guards were dead code and a green run
installed whatever main was at that instant, so a commit whose own checks were
still pending could install its root sudoers and deploy helper. The sudoers
grant therefore also admits `sync-control-plane [0-9a-f]*`, following the
existing `radon-app-runtime pull [0-9a-f]*` shape. That argument only NARROWS
what root will install: it cannot name a tree, and a SHA that is not the
pinned remote main tip is refused. The no-argument verb and its grant are
unchanged, and the wrapper falls back to it while a host still carries the
older policy. Root hold CLI
source is `scripts/utils/ib_operator_hold.py`; the manifest marks repository
sources with `repo/` and the archive includes that one dependency alongside
`cloud/`.

`inspect-running` passes docker's stdout, stderr and exit code through
untouched: `gateway_state()` tells `missing` from `unknown` by matching
"No such object" on stderr, and swallowing it wedges the watchdog's restart
ladder at `unknown`.

**Refresh installs git blobs, never the working tree.** The sources it copies
into `/etc/sudoers.d`, `/usr/local/sbin`, `/etc/polkit-1` and the unit
directory come from `git cat-file blob` at the deployed commit, read from the
root provision store (below) and staged root-owned under
`/opt/radon-provision/control-plane-src.*`. That commit is the local HEAD,
and it must be reachable from the GitHub main tip -- an ancestor, so a
rollback still installs its own release's control plane, but never a commit
main has not contained. `/home/radon/radon` is writable by `radon`, which
holds a NOPASSWD verb for `refresh-control-plane-privileged`; reading the
checkout there made those bytes a root install with only syntax validation in
front of them. Same rule `install-units` has always followed (R-084).
Privileged files (helpers, sudoers, polkit, runtime, compose, seccomp) never
move backwards: `/opt/radon-provision/control-plane-floor` records the last
main commit whose privileged set root installed (`sync-control-plane`,
`refresh-control-plane-privileged`, `setup-vps.sh`), and a privileged diff
from a HEAD that does not descend from it fails with 76. An older HEAD still
rolls back its unit files. A missing record accepts any main commit; an
unreadable one refuses every privileged diff.

**Root provision store.** Every root install of a privileged artifact
(`sync-control-plane`, `refresh-control-plane[-privileged]`, `install-units`,
`sync-scheduled-units`, `publish-caddy`, and `setup-vps.sh`) reads git objects
from `/opt/radon-provision/radon.git`: a bare clone, `root:root` `0755`, that
only root writes. Root fetches it from the pinned
`https://github.com/joemccann/radon.git` with `fetch.fsckObjects` and
`transfer.fsckObjects`, `GIT_CONFIG_NOSYSTEM=1` and
`GIT_CONFIG_GLOBAL=/dev/null`, bounded at 120s, and only when the commit
`git ls-remote` just reported is not already there, so a deploy pays at most
one incremental fetch. It never reads objects from `/home/radon/radon/.git`,
whose object files and alternates radon can rewrite and whose reads git does
not re-hash; that store supplies the HEAD commit id alone, which root then
proves is main history. An unreachable remote, a fetch without the verified
commit, or a store that is not root-only fails closed (69 / 74). First use
creates the store; nothing grants radon write to it. Recovery for an exact
older SHA or with GitHub down, as root, when the store already has it:

```bash
SHA=<exact-sha>
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
STORE=/opt/radon-provision/radon.git
git --git-dir="$STORE" cat-file -e "${SHA}^{commit}"
STAGE="$(mktemp -d /opt/radon-provision/manual.XXXXXX)"
git --git-dir="$STORE" -c tar.umask=022 archive "$SHA" cloud scripts/utils/ib_operator_hold.py | tar -x -C "$STAGE"
RADON_BOOTSTRAP_CLOUD_ROOT="$STAGE/cloud" bash "$STAGE/cloud/scripts/bootstrap-control-plane.sh"
rm -rf "$STAGE"
# only when deliberately pinning an older privileged set:
printf '%s\n' "$SHA" > /opt/radon-provision/control-plane-floor
```

Deploy, bootstrap, operator, Gateway control and app runtime inline Python run with `-I`. Their
stdlib validators and process supervisors ignore caller working directories,
`PYTHONPATH`, and user site packages. Keep isolation when adding Python calls
to these entrypoints. Automatic sync checks all five candidate scripts before
executing the candidate bootstrap; a missing helper or isolation flag refuses
admission. The fixed-source admission check covers variable expansions, literal
Python paths and continued commands; source review remains required. See
[the admission runbook](../docs/incident-runbook.md#control-plane-isolation-admission).
Privileged refresh applies the same check before installing shell helpers.
These checks also reject older bundles that omit interpreter isolation.

The five app-plane drop-ins run the container with the systemd notify
socket proxied: `radon-app-runtime run` spawns `notify-proxy` inside the
unit cgroup and mounts ITS socket as the container's `NOTIFY_SOCKET`. A
datagram sent from the container's own PIDs (in `system.slice/docker-*.scope`)
is dropped by systemd regardless of `NotifyAccess=`, so `Type=notify` +
`WatchdogSec=` work only through the proxy (R-429).

## Canonical Host Paths

`radon-nextjs.service` starts `/usr/local/bin/next-clerk-guard` inside its
container. The guard requires an exact publishable-key token in the baked
client assets before executing `bun run start`; an environment key that is
only a prefix of a baked key is rejected.

- Monorepo checkout: `/home/radon/radon`
- Cloud source: `/home/radon/radon/cloud`
- Immutable deploy support: `/home/radon/.radon-deploy-runners/<sha>.<run>/cloud`
- Canonical secrets: `/etc/radon/env` (regular file, mode `0640`, owner `root:radon`). Production loads env only from this file. `/home/radon/radon-cloud/.env` is retired, including any `.env.bak-*` files in that directory.
- Seeding `/etc/radon/env` never reads a source the service account can
  replace. `post-setup.sh` sends the bytes on the ssh connection's stdin and
  installs them from a root-only `mktemp -d /root/...` staging directory, and
  the advice `setup-vps.sh` prints when the file is missing stages the
  template the same way. Never `install` or `scp` straight out of
  `/home/radon/radon` or `/tmp`: both follow the source.
- Staging media: `/var/lib/radon/media` (owner `radon:radon-media`; caddy is not in group `radon`). Root changes its mode only through an `O_NOFOLLOW` fd (`chmod_dir_nofollow`); ACL changes inside it, including recursive `-R -P` walks, run as `radon` via `setpriv`
- Control-plane rollback state (manifest, ready marker) in radon-owned `/var/lib/radon`: root restores it only through a pinned `O_NOFOLLOW` directory fd, writing a fresh `O_EXCL` temp then renaming it over the target (`restore_target`); never a by-name `rm` then `cp`
- Private research: `/var/lib/radon-private` is a root-owned `0700` anchor;
  its `research` child is radon-owned `0700`. The worker mounts that child
  read-write and API read-only at `/var/lib/radon/research`. Seed through the
  container mount; host user radon cannot traverse the anchor.
- Protected served media: `/var/lib/radon-public/media` (`root:caddy`, directory `0750`, files `0640`). No app bind mounts. The credential-free root-owned `media_publish.py` plus `radon-media-publish.service` and `.timer` publish bounded raster snapshots: the first deadline is 15 seconds after boot (immediate when enabled after that deadline), then 15 seconds after service inactivity. Runtime, timer accuracy and deferred batches also affect visibility. The legacy `.path` remains installed for rollback but stopped and disabled; setup and upgrades retire it before backfill and enable only the timer. On a host where systemd has already garbage-collected that unit, `reset-failed` on the absent `.path` is tolerated and only the `.service` gates `publish-caddy`; before 2026-10-23 that failure returned 75 on every deploy and left the live Caddyfile stale. Initial migration drains before the Caddy root switches; retries use a protected metadata cache. Every supported staging leaf is preserved regardless of age; source cleanup owns retention. More than 5,000 supported leaves fails before mutation. Actual source deletions mirror only after a complete scan; prior valid bytes survive rejected updates. See [public media publication](../docs/operations.md#public-media-publication).
- Compatibility media symlink: `/home/radon/radon-cloud/media` -> `/var/lib/radon/media`
- Durable privileged deploy state: `/var/lib/radon-deploy` (root `0700` under root-owned `/var/lib`; never a child of radon-owned `/var/lib/radon`). The helper moves a transition journaled at the pre-move `/var/lib/radon/deploy` once, through a pinned `O_NOFOLLOW` fd, and bootstrap refuses while either holds one
- Root provision store (root's clone of the pinned remote):
  `/opt/radon-provision/radon.git`
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

Pushes to `main` run the root CI workflow. Every ssh-action step that reaches
the app host pins `fingerprint: ${{ secrets.VPS_SSH_FINGERPRINT }}` (Production
environment). drone-ssh negotiates the host's ECDSA key, not ed25519, so the
secret is `ssh-keyscan -t ecdsa <host> | ssh-keygen -lf - -E sha256`'s
`SHA256:...` value; a rebuilt host needs the secret updated before the next
push or every deploy step fails the handshake. The deploy job:

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
7. Prepares target-SHA artifacts before teardown. The
   [deploy source](scripts/deploy.sh) owns frozen build, prestage and verified
   image-reuse admission. `prepare_python_wheels` and `install_target_python_env`
   run pip from the release dir: `requirements.txt` carries
   `fastembed @ file:./vendor/fastembed-0.8.0-py3-none-any.whl` (materialized by
   `scripts/deps/materialize_fastembed_pillow13.py` into the release's gitignored
   `vendor/`), and pip resolves a relative `file:` URL against its working
   directory, not the requirements file (the `9d91faf30` deploy refused with
   `FileNotFoundError: ./vendor/...whl` from the runner's directory). Pinned by
   `cloud/tests/test_deploy_corrections.py::TestFrozenArtifacts`. The parallel prepull job is an optimization; deploy
   still requires the exact-SHA app image pair locally before teardown and
   refuses if its pull fails. A bounded database preflight also refuses teardown
   when Turso does not answer, leaving the current release serving.
8. Fsyncs a durable transition journal, snapshots active services and timers,
   promotes artifacts, restores the prior topology, and runs code-controlled
   gates.
9. Commits the topology transition and green marker only after verification.

Prestage runs before the normal job's pinned control-plane synchronization.
If the selected canonical deploy lock or its immediate parent is genuinely
unprovisioned, a read-only check of the fixed root-owned directory authority
allows prestage to defer before a deployment child, publisher, privileged
helper or journal work.
Existing writable, foreign-owned, symlinked, unreadable or uncertain paths
refuse deferral; the existing canonical parent must retain its `0750` mode and
`radon` group. Custom lock overrides and recovery-only behavior retain their
existing policy. Ordinary deployment still requires its provisioned locks and
subscription environment publication after synchronization, including an
already-green release. Rollback journals and service recovery are unchanged.

The journal helper is loaded from the immutable runner so rollback to a commit
that predates `cloud/` cannot delete its own recovery implementation. Root
topology state is durable across reboot under `/var/lib/radon-deploy`.
The operator writes every byte of the saved unit list before fsync and atomic
rename; a partial write or persistence error refuses service teardown and retains
the prior topology file.

**Interrupted backups.** The release snapshot includes an active or activating
`radon-db-backup.service`. Stop it before replacing code, then asynchronously
resume that exact replay-safe oneshot during `restart-managed` / `recover`.
Do not replay other oneshots or start a backup that was dormant. Another
snapshotted oneshot that stop-clean SIGTERMed (`failed`, `Result=signal`) is
`reset-failed`, not started, so the deploy's own stop does not page; an
exit-code failure stays visible. Once restore
is recorded, repeated recovery does not restart the dump, including after an
off-box failure. A second `recover` with nothing left to start is a finished
restore: the helper's child is `/bin/bash`, and on bash 3.2 an empty
`services` or `timers` list is an unbound variable under `set -u`, so those
walks use `${arr[@]+"${arr[@]}"}`. A rejected start submission fails recovery before the restore
marker is written and can be retried. Once accepted, the eventual dump/upload
result belongs to backup health, not application rollback. Deploy does not
manufacture a healthy heartbeat.

**Auto-deploy mechanics (moved from root `CLAUDE.md`).** `.github/workflows/ci.yml` runs the Vitest + pytest gate (including `cloud/tests`) then deploys on green: its three host jobs join Tailscale as ephemeral `tag:ci` nodes before key-authenticated SSH to the app tailnet address (public app SSH is closed; [network trust](../docs/operations.md#ci-deploy-over-the-tailnet) owns enrollment and recovery). The final deploy job materializes an immutable `cloud/` runner from the release SHA under `~/.radon-deploy-runners/`, and runs that runner's `deploy.sh '$SHA'`. The deploy job remains bound to the GitHub Environment `Production` for deployment history, URL metadata, environment-scoped configuration, and a main-only deployment branch policy; it has no required-reviewer rule, so no manual approval is needed after the automated gates pass. Host secrets remain outside the checkout; [Environment Handling](#environment-handling) owns canonical and compatibility paths. After root bootstrap publishes `/var/lib/radon/control-plane-ready`, legacy dual-checkout deploy is retired for new releases; pre-ready SHAs still use the compatibility path. The SSH script first waits a bounded time (`RADON_DEPLOY_LOCK_WAIT_SECS`, default 480s) for a still-finishing previous release to free `/var/lib/radon-lock/deploy.lock` and the retained compatibility inode `~/.radon-deploy.lock`, so back-to-back green merges queue instead of failing on the lock (2026-09-19, run 35463319654); `deploy.sh` itself stays non-blocking and still refuses with exit 75 if the lock is held after the wait. Before `deploy.sh`, the deploy job runs `cloud/scripts/sync-control-plane.sh` → `radon-deploy-root sync-control-plane`, which installs the GitHub-main-tip control-plane bundle (helper, sudoers, polkit, control-plane units, drop-ins) via that tip's own bootstrap, so control-plane edits and root hot-patches need no manual bootstrap (R-430, 2026-08-29). Confirm: `gh run list --workflow=ci.yml --limit 1`. Migration/rollback: `docs/monorepo-cloud-migration.md`. `deploy.sh` then `publish-caddy`: stage, `caddy validate`, atomic install, `systemctl reload caddy` (30s), then `systemctl restart caddy` (60s) if reload is TERMed. The helper supervisor budget for publish-caddy is 300s so reload+restart both fit; 180s was consumed by a wedged reload and restart never ran (`11a0575d`, `868ee0f2`). Checked-in Caddy configuration provides HTTPS with an HTTP redirect for the dedicated MCP host; follow the [hosted-MCP owner](../docs/cloud-services.md#hosted-mcp-radon-mcpservice-issue-232-chunk-1) for operator TLS verification before changing the published consumer URL. Do not add a new `/var/log/caddy/*.log` path: root `caddy validate` creates that file as root:root and the caddy user cannot start (`mcp.log` took the edge down on 8628705d). Cutover lessons: `tasks/lessons.md` (2026-07-11). The deploy health-gates the relay restart: before tearing services down (while the current radon-api still serves `/health`), `wait_for_gateway_ready` confirms the IB gateway is authenticated + port_listening (bounded 60s, warn-and-proceed). The relay self-heals on reconnect and raises a `service_health` row (`ib-realtime-relay`) instead of looping silently on no-ticks.

The root helper normally polls service state for 60 seconds. Production stops
of `radon-research.service` instead allow 150 seconds: its canonical unit has
`TimeoutStopSec=120`, followed by container cleanup in `ExecStopPost`. Research
startup and all other state waits keep their original bounds. The root mutation
supervisor still caps the complete action at 180 seconds and fails closed into
recovery if shutdown does not complete. `cloud/tests/test_research_deploy.py`
pins these nested budgets and exercises delayed shutdown with a simulated clock.

A failed batched stop retries loaded units, tolerating a retired inventory entry
only when successful probes report `LoadState=not-found`, `ActiveState=inactive`
and an empty `FragmentPath`. Unknown states, probe errors and loaded-unit stop
failures remain fatal. Recovery preserves the inventory and active snapshot.
Transient `radon-*` units (`systemd-run`, `Transient=yes`) never enter the
inventory: systemd discards one once stopped, so a transition that stopped it
could not restore it (2026-09-28, `radon-forktest.timer` failed a deploy and its
rollback). One batched `systemctl show` decides; a failed query keeps every unit.
The Python image keeps application source root-owned and provisions only
`/home/radon/radon/logs` for the runtime user; its non-root build smoke verifies
log creation, writing and rotation while source directories remain unwritable.

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
extracts `cloud/` at that commit from the root provision store into a
root-only staging tree under `/opt/radon-provision/`, and runs that tip's own
`bootstrap-control-plane.sh` (deploy lock, transition refusal, validation,
atomic install, manifest rewrite). A bundle that is already current is a
no-op. Helper, sudoers, polkit, control-plane unit and drop-in edits, and
root hot-patches of installed drop-ins, therefore reconcile on the next green
push without root SSH. The verb's only argument is an expected 40-hex SHA:
radon cannot hand root a tree, only ask it to converge on GitHub main, and
pinning the tested commit makes root refuse when main has moved past it.
Prestage skips (does not fail)
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

Bootstrap refuses a cloud root any account other than root can write, so it
never runs from the radon-owned `/home/radon/radon/cloud`. When the tested
release is the GitHub main tip (the normal case), converge on it as root:

```bash
# as root; converges on the GitHub main tip via root's own clone
/usr/local/sbin/radon-deploy-root sync-control-plane
```

For an exact older SHA, or with GitHub unreachable, use the root provision
store recovery above.

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
Host Node.js is the NodeSource package `22.23.4` (`nodejs=22.23.4-1nodesource1`). `install_node22` keeps the binary only when `node --version` is exactly `v22.23.4`. App and chart images pin `node:22.23.4-bookworm-slim` by digest in `docker/app/Dockerfile.node` and `docker/app/Dockerfile.python`. Re-run `setup-vps.sh` on a host still on an older binary.

Its `open_firewall` RESETS ufw to the declared app ruleset (byte-for-byte
mirror of `scripts/host-firewall.sh`, pinned by `tests/test_host_firewall.py`):
no blanket `tailscale0` allow, 8321 only from `10.0.0.4` and
`RADON_FW_OPERATOR_SOURCES`. Live hosts change through `host-firewall.sh`
(dry run by default), never deploy; runbook `docs/operations.md` "Host firewalls". R-728 / REL-309 snapshots the prior ufw configuration and enabled state before reset; command failure or TERM/INT restores it. Success removes the root-only snapshot; failed rollback or SIGKILL/power loss retains the printed backup for the explicit root recovery sequence in that runbook. Both entrypoints share the identical recovery function, with isolated executable parity and fault tests. The destructive wipe removes retired ingress snapshots while retaining the active ufw policy.


Setup clones the public app repo over anonymous HTTPS and mints no GitHub SSH
key for `radon` (the host holds no GitHub credential; DS-2026-09-30-02).
Setup pins GitHub's published ed25519 SSH host key (no first-contact keyscan),
provisions the secret-store credential as 32 raw bytes, and prepares the
radon-replaceable media directory with create-then-verify + `chown
--no-dereference` rather than a check-then-install pair.
Setup stages root-installed artifacts from committed git blobs read from
the root provision store, never from working-tree files or the checkout's
object store. The commit is the checkout's HEAD when the store proves it is
an ancestor of the trust anchor, else the anchor itself: the
`refs/heads/main` SHA root reads with `git ls-remote` from the pinned
`PROVENANCE_REMOTE_URL`, never the radon-writable `origin/main`, remote URL
or config. The working-tree copy must match that blob byte for byte. It
fails closed when the remote is unreachable or the fetch does not carry the
anchor (`RADON_PROVISION_ROOT` overrides the store path for tests). It
also publishes `mcp.env` only through a regular-file destination via temp-write +
atomic `mv -T` rename, so a destination swapped after the check is replaced,
not entered (contract: `cloud/tests/test_setup_vps_privileged_paths.py`).
`install_caddy` skips reinstall only when the installed dpkg version already
matches the pinned `CADDY_VERSION`, not merely on `command -v caddy`
(2026-09-22), so a rerun on a host with a stale Caddy converges it.

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
- Canonical secrets path: `/etc/radon/env`; service units load it through
  `EnvironmentFile=`. `/home/radon/radon-cloud/.env` is retired, including
  any `.env.bak-*` files in that directory. `deploy.sh` uses `/etc/radon/env`
  and fails when that regular file is missing. `setup-vps.sh`
  `create_etc_radon_dir` creates `/etc/radon` and the media directory. It
  does not create `/home/radon/radon-cloud/.env`.
- Compose interpolation and service `env_file` both receive `/etc/radon/env`
  through `RADON_COMPOSE_ENV_FILE`.
- [Runtime planes](#runtime-planes) owns execution mode and required release
  image admission; per-unit drop-ins own the runtime overlay.
- `web/.env` contains only `NEXT_PUBLIC_*` build values and is mode `0600`.
  Never copy the complete production env into the web tree.
- Setup validates the stable env before dependency installation or builds.
- Host needs Bun 1.4.4 on radon PATH for staged Next builds (`deploy.sh` and `setup-vps.sh` `BUN_VERSION`).

**IB Flex / Gateway env (Hetzner `/etc/radon/env` mode `0640`, owner `root:radon`; moved from root `CLAUDE.md`):** `IB_FLEX_TOKEN`, `IB_FLEX_QUERY_ID=1422766` (blotter), `IB_FLEX_NAV_QUERY_ID=1442520` (Activity query "Equity Summary in Base"; carries THREE sections — NAV in Base, Cash Transactions and Transfers — don't repurpose for trade pulls), `IB_GATEWAY_MODE=cloud` (production; FastAPI must not own Compose), `IB_GATEWAY_COMPOSE_DIR=/home/radon/radon/cloud` (monorepo path; not `~/radon-cloud`), `RADON_MODE=hetzner`.

**Verify Flex query ids against the real env before trusting one written down anywhere** — a stale id documented here once pointed the runbook at a query that does not exist for this account. `IB_FLEX_FLOWS_QUERY_ID` is deliberately unset: `_flows_query_id()` falls back to the NAV id and `resolve_flows` reuses the one fetched document, so a run makes ONE Flex request. Setting it to a second id doubles the request rate against a token that has already taken a 24h-to-168h throttle embargo.

**Backblaze B2 (portfolio cold-archive, production required):** `RADON_ARCHIVE_S3_ENDPOINT`, `RADON_ARCHIVE_S3_BUCKET`, `RADON_ARCHIVE_S3_ACCESS_KEY_ID`, `RADON_ARCHIVE_S3_SECRET_ACCESS_KEY`, `RADON_ARCHIVE_S3_REGION` (+ optional `RADON_ARCHIVE_S3_PREFIX`). S3-compatible API to bucket `radon-archive`. Used by `radon-portfolio-archive.service` / `scripts/archive_portfolio_snapshots.py`. Not Cloudflare R2. Full contract: root `.env.example`, `docs/cloud-services.md` "Portfolio archive".

**Cloudflare Workers AI (Clairic report dates, optional, off by default):** `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` live in `/etc/radon/env` (root:radon `0640`, unquoted `KEY=value`), the same file `radon-research.service` loads for its other API keys. They take effect at the next service start. They are not in `cloud/config/required-env.txt` (a missing required key fails deploy preflight). The token is a Workers AI token with Read and Edit only. `CLAIRIC_DATES_ENABLED` and `CLAIRIC_DATE_SANITY_ENABLED` also default off; set `CLAIRIC_DATES_ENABLED=1` (and optionally `CLAIRIC_DATE_SANITY_ENABLED=1`) in that file, then restart `radon-research.service` or deploy. Behavior: [Dropbox research](../docs/dropbox-research.md#clairic-report-dates-off-by-default).

They are intentionally left out of `mcp.env` (`deploy.sh` `MCP_ENV_KEYS` is Clerk and `RADON_MCP_*` only) and out of the newsfeed allowlist in `radon-app-runtime.sh` `render_env_file`.

Provisioning (app host, as root). Append via stdin. Never pass the token as a command argument:

```bash
cp -p /etc/radon/env /etc/radon/env.pre-clairic
cat >> /etc/radon/env
stat -c '%U:%G %a' /etc/radon/env    # root:radon 640
grep -c '^CLOUDFLARE_' /etc/radon/env  # 2
```

`cat` reads the two unquoted lines (`CLOUDFLARE_ACCOUNT_ID=...` and `CLOUDFLARE_API_TOKEN=...`) from stdin until EOF. Rotation: create a new token, replace the `CLOUDFLARE_API_TOKEN` line, restart `radon-research.service`, then revoke the old token.


## Systemd And Drift

`setup-vps.sh` includes the `radon-credit-vix`, `radon-aa-frontier-refresh`, `radon-ai-cycle-backfill`,
`radon-ai-cycle`, `radon-liquidcompute`, `radon-subscription-tokens` and
`radon-panic-index` service/timer pairs in the full-host installation inventory.
Setup installs those pairs and enables only their timers; existing hosts receive
them through the hash-pinned `install-units` path. Historical collection resumes
at 05:30 UTC, the frontier timer updates the fixed Artificial Analysis cohort at
07:00 UTC, current collection runs at 07:15 UTC, and the Liquid Compute public
GPU index ticker runs at 07:30 UTC, each with up to five minutes of jitter. Provider credentials are optional source
entitlements; missing keys leave those measurements unavailable. Collection,
reviewed disclosures and source limits are documented in
[`docs/ai-infrastructure-operations.md`](../docs/ai-infrastructure-operations.md).
The subscription keeper loads the root-private derived environment
`/etc/radon/subscription-tokens/env`. Setup derives it before installing units;
each ordinary deploy refreshes it through the fixed `publish-subscription-env`
root verb after environment preflight and the job's pinned control-plane sync.
Prestage precedes that sync and skips this publisher. The keeper's UID, native
credential paths and vault socket are unchanged. Lifecycle and rollback ownership:
[`docs/subscription-tokens.md`](../docs/subscription-tokens.md#service-environment-lifecycle).

`radon-subscription-tokens.timer` runs every 30 minutes UTC (`Persistent=true`)
and heals the agent-CLI subscription credentials (claude, codex, grok,
Antigravity `agy`) from the encrypted secret store, with a daily keepalive
probe; a dead codex or grok grant pages a one-tap login link. Runbook:
[`docs/subscription-tokens.md`](../docs/subscription-tokens.md).

**Secret-store key units (DS-2026-10-05-05).** Every unit that loads
`radon-secret-store-key` runs through `radon-app-runtime`: `radon-api`, the
control-plane `radon-subscription-vault.service` (base unit and drop-in), and
the `Type=oneshot` drop-ins for `radon-ai-cycle`, `radon-ai-cycle-backfill`
and `radon-aa-frontier-refresh`. Both drop-in gates therefore accept
`Type=simple|notify|oneshot`. `radon-subscription-tokens` stays `User=radon`
with no key and reaches the vault through the broker socket
(`/run/radon-subscription-vault/vault.sock`); its `Wants=` starts the broker.
`install-units` never enables a `.service`, so an existing host needs one
`systemctl enable radon-subscription-vault.service` for boot start. Host
cutover (removing the plaintext seed key): `docs/operations.md` "Secret-store
key cutover". Contract: `cloud/tests/test_ds_secret_store_key_units.py`.
The Python image carries integrity-pinned `claude`, `codex`, `grok` and `agy`
(`docker/app/vendor-clis/`), so `radon-api` and `radon-research` never mount host
`~/.local/bin`. Provider credential directories and `HOME=/home/radon` remain
unchanged; Antigravity retains its existing writable refresh/state directories. Root `prepare_private_dir` pins
every ancestor with `O_NOFOLLOW`, and an unverifiable `id -nG radon` refuses
every key-bearing start. Contract: `cloud/tests/test_secret_jobs_runtime.py`.

`radon-tv-alerts.timer` runs every 5 minutes, 24/7 (`Persistent=false`), and
drains TradingView webhook rows into one digest Pushover per cycle. Caddy bounds
`/api/webhooks/tradingview/*` to TradingView's four sender IPs and a 16KB body.
Spec: [`docs/tradingview-integration.md`](../docs/tradingview-integration.md).

`setup-vps.sh` also inventories `radon-slm-tagger.service` plus
`radon-slm-tagger-monitor.{service,timer}` so a fresh host has the unit files.
`enable_services` skips all three until Joe enables them after bakeoff `C WINS`.
`RADON_SLM_TAGGER_MODE` stays `off`. Spec: [`docs/ml/newsfeed-slm-tagger.md`](../docs/ml/newsfeed-slm-tagger.md).

`setup-vps.sh` also inventories `radon-knowledge-eval.{service,timer}` so a
fresh host has the unit files. `enable_services` skips both on every setup.
It does not inspect the baseline file. Leave that skip until a human reviews
the draft golden set. The repo ships an initial live baseline in
`scripts/knowledge/golden_eval_baseline.json`; enabling the timer before
that review would page on a draft gate. The unit
writes no `service_health` row (`EXEMPT_UNITS` `gap:`); a failed run pages
via the unit watchdog. Drift ack: `not-installed:radon-knowledge-eval.*`.
Auto-sync stays off. `cloud/tests/test_knowledge_eval_setup.py` pins
the inventory-and-skip. Contract:
[`docs/knowledge-embeddings.md`](../docs/knowledge-embeddings.md).

`setup-vps.sh` inventories `radon-grok-upgrade.{service,timer}` and
`enable_services` enables both. Daily 07:40 UTC. The oneshot side-installs
the latest stable Grok CLI, smokes it, and auto-promotes on pass (writes
`/var/lib/radon/grok_lkg.json`). A failed smoke stays on last-known-good
and pages via Pushover plus the unit watchdog. The job heartbeats
`service_health` as `grok-upgrade` (26h daily window). Contract:
The optional isolated responder is prepared through
`cloud/scripts/setup-grok-responder-account.sh`, from a root-only provision
stage. Nested `services/responder-domain/` units are outside automatic fleet
installation. The responder's executed code is not: `setup-vps.sh` and every
`install-units` publish a root-owned tree at
`/usr/local/lib/radon/grok-responder`, and the optional unit runs that tree
with `python3.13 -E -S` (DS-2026-10-17-16). The account clone stays the
writable worktree. An already-finalized host swaps its installed unit only
when `setup-grok-responder-account.sh` is rerun from a trusted stage; the
`ready` marker selects that refresh and leaves the clone and timers alone.
The explicit native-login and pickup checks precede a
transactional timer handover; root-private installed-unit inventory adds the
optional units to ongoing drift verification. The canonical keeper upgrader
remains unchanged. Migration and rollback commands are owned by
[`docs/grok-page-responder.md`](../docs/grok-page-responder.md).
`cloud/tests/test_grok_upgrade_setup.py` pins the inventory-and-enable.
The unit names every link the upgrader may move (`--live-bin`,
`--alias-bin ~/.grok/bin/grok`); `grok_upgrade.py` never derives a path
from HOME, so a test run in the responder clone cannot relink the live CLI.
The unit runs the root-owned copy `/usr/local/lib/radon/grok-upgrade` on
`/usr/bin/python3.13 -E -S`, never the responder clone (hidden from it);
`setup-grok-page-responder.sh` installs that copy, so an upgrader fix lands
on the host only at a setup rerun (`cloud/tests/test_grok_upgrade_controller.py`).

`setup-vps.sh` inventories `radon-research-archive.{service,timer}` and
`enable_services` enables the timer: Sunday 04:20 UTC, moves
settled private research `evidence/` to B2 (`research/` prefix), verifies,
then prunes. Its Type=oneshot `runtime-container.conf` drop-in (control
plane) runs it in the Python image with only the private research bind,
since host radon cannot traverse `/var/lib/radon-private`. Heartbeat
`research-archive` (9d). Runbook: `docs/operations.md` "Research evidence
archive".

`setup-vps.sh` inventories `radon-rsi-oversold.{service,timer}` and
`enable_services` enables the timer. Daily 16:40 ET, twenty minutes
after `radon-ma-ratio`. Oneshot `scripts/rsi_oversold_scan.py`,
`TimeoutStartSec=2100`. Heartbeat `rsi-oversold` (26h). Spec:
[`docs/indicators/rsi-oversold.md`](../docs/indicators/rsi-oversold.md).


Canonical unit files are copied root-owned to `/etc/systemd/system`; they are
not symlinked from the checkout.

Bootstrap installs only the control-plane units. Every other unit
(the timer-owned scans and their timers) is installed by the CI deploy:
after the promote and before `restart-managed`, `deploy.sh` runs
`radon-deploy-root install-units`, which copies each unit whose
`cloud/services/` content hashes to its entry in
`config/installed-units.sha256`, daemon-reloads once, and `enable --now`s
NEW timers only (never a timer-owned `.service`). New indicator pairs such as
`radon-vix-pc.{service,timer}` ship the same way. The manifest digest is the
review gate: editing a unit in `services/` and bumping its hash in the same
commit is the whole procedure -- no root SSH. An edit whose hash is NOT
bumped is left uninstalled (the drift audit then flags it), so the
drift-allowlist acknowledgment (read from the main-tip blob, never the host
checkout) still covers a deliberate pending window;
`tests/test_unit_install_acknowledgment.py` fails CI otherwise. A unit
absent from the manifest is never installed. `install-units` journals what
it promoted under `/var/lib/radon-deploy`; a rollback runs
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
must never read or report `.env*` contents. There is no beta carve-out: beta
host artifacts were removed 2026-10-11, so a live `radon-beta*` unit or
sudoers fragment is `config-drift` (`tests/test_beta_sunset.py`).

Host swap is `/swapfile` (2G, `/etc/fstab`) from `setup-vps.sh:ensure_swapfile`;
idempotent, refuses a symlinked path. Runbook: `docs/operations.md` "Host swap".

`setup-vps.sh` inventories `radon-control.service` and `enable_services`
enables it: the host control socket the containerised radon-api uses for the
`/admin` Service controls (`/run/radon-control`, bind-mounted by
`radon-app-runtime` into radon-api only; runs `sudo -n /usr/local/bin/radon`
under the existing `radon-ops` grant). Existing hosts get the body through the
`installed-units.sha256` pin; being a `.service` it is never enabled by
`install-units`, so the app host needs one `systemctl enable --now
radon-control.service`. `operator-radon.sh` excludes it from `radon
stop|start|restart`. Contract: `docs/operations.md` "Host control socket".

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

Image pre-pull compares each cached release tag with its registry manifest digest before skipping layer downloads. `pull` from `deploy.sh` runs under the deploy lock. The CI prepull job runs `pull` without that lock. `deploy.sh` calls `radon-app-runtime prune` after a green deploy and never after prestage, which runs beside prepull, once the supervisor has closed the lock fd. Every `pull` holds a shared pull lock until it exits; both prunes skip while another pull holds it. Which tags either verb may delete is the [app image prune](../docs/operations.md#app-image-prune) procedure. Runtime starts still allow the exact local image during registry outages.

### SPX breadth scheduled comparison

`setup-vps.sh` inventories `radon-record-high-breadth.{service,timer}` and enables the timer on full-host setup. The bounded auto-sync manifest installs both files on existing app-plane hosts. The daily 18:15 America/New_York run reconciles TradingView MCP source history, writes `record-high-breadth` service health, and retains prior data on authentication failure. Its 26h freshness budget also covers weekend heartbeat runs. Provision the production OAuth store through explicit consent and configure `TRADINGVIEW_MCP_TOKEN_FILE`; setup does not install or copy account credentials. This provider-coherent comparison does not change generic commodity-price failover. Source-universe and history limits are documented in `docs/indicators/record-high-breadth-source-parity.md`.


### Unusual Whales Advanced collectors

The root-owned bootstrap manifest includes `radon-uw-stream.service`,
`radon-uw-research.service`, `radon-uw-research.timer`, and their runtime
container drop-ins. `setup-vps.sh` installs these through the existing
control-plane path. Collection requires explicit `RADON_UW_STREAM_ENABLED=1`
admission and a configured UW token; installation alone must not enable an
unverified feed. Runtime credential grants and filtered environment staging
must remain consistent with the collector commands.

Deployment discovers an already active UW stream only after lock, control-plane,
and environment admission. The optional process then participates in restart
and rollback with the core services. Sourcing deploy.sh or refusing a prestage
lock must not invoke systemctl. Account settings and request admission are
documented in `docs/operations.md` and `docs/uw-rest-governance.md`.

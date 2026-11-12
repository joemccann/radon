# Shared model service

All Radon inference goes through `scripts/model_gateway`. App processes keep their existing Python and TypeScript interfaces; their adapters send bounded requests to this service. Provider grants and model runtimes live in the service. A failed service connection never opens a direct-provider fallback.

## Interfaces

Python app callers use `clients.model_ladder` for text/vision JSON and its local acceptance callback. A rejected result resumes a single-use service continuation under the original deadline. Node pipeline callers use `scripts/model_gateway/client.js`; Next.js uses `web/lib/llm/provider.ts` through the authenticated FastAPI `/model-gateway` bridge. That bridge runs on its own two-thread pool and returns 503 `saturated` when both workers are busy, so a chat or forecast cannot occupy the default executor that order preflight and the working-order read share (R-747). New consumers use these same adapters.

Fixed operations cover normalized chat with tools/images, JSON, vision extraction, explicit 384/2048-dimensional embedding spaces, OCR, X search, Chronos forecasting, and Clairic dates. Inputs cannot supply upstream URLs, headers, credentials, paths, or arbitrary commands. Existing publication, Humanizer, portfolio, source, trading and provider pacing rules remain in their owning pipelines.

The default text/JSON order starts with Antigravity. Requests needing images or native tools use compatible providers. Native Messages, Responses and Chat Completions preserve their original schemas, including custom tool grammars, and use only providers supporting that protocol. A response must reach a verified terminal state before delivery. Unsupported capability is an error; schemas and images are never silently discarded. The loopback bridge forwards only the request body, so native Claude requests get the pinned client's beta list from the gateway. Auto-mode body fields add their own betas: `thinking.display: "updates"` and `safeguards` (`native._claude_field_betas`). Without those betas, Claude Security sessions with `--permission-mode auto --verbose` get a 400.

## Authentication and renewal

The inference socket is `/run/radon-model-gateway/gateway.sock`. The separate management socket is `/run/radon-model-control/control.sock`. Only privileged credential-validation, catalog and keeper clients receive the management mount. Neither socket is a TCP listener. Coding agents use an ephemeral authenticated loopback adapter to reach the private service; tool execution stays inside the agent's existing local permissions and approval policy. Bridged Grok gets its own per-run `GROK_HOME`. That directory also holds its config, auth file and leader socket. Grok never reads the operator's `~/.grok`, which the page-responder unit makes inaccessible. Grok will not start if it can read the directory's existence but not `requirements.toml` (`model_gateway.cli.configured_cli`).

The gateway receives canonical subscription grants, a scoped credential-broker socket and fixed model-key slots. It does not receive the secret-store master key. Stored NVIDIA, Cerebras and Cloudflare values take effect through the broker; application processes do not export those model secrets. The subscription timer requests gateway-owned renewal jobs. Inference-triggered renewal cannot launch an interactive login. A provider requiring renewed human authorization remains unavailable and retains the existing alert path.

The service owns model selection, deadlines, concurrency, cancellation and refresh coordination. Newsfeed tagging (`purpose: newsfeed-tags`) uses the plain ladder. The `slm-tagger` rung and its shadow telemetry are unwired, and the gateway refuses `slm-tagger` as a provider. Model inventory reads cached, sanitized metadata and marks configured defaults explicitly when discovery is unavailable. Missing context limits are unknown, never estimated.

Each ladder rung gets a slice of the request deadline. A rung may spend the whole remaining deadline except 20 seconds for each later live rung, and never less than an even split. An accepted draft still finishes inside that budget (120 seconds unless the caller set another read_timeout). When a draft is rejected, each later live rung is guaranteed 60 seconds (`contracts.MIN_FALLBACK_SLICE_S`). If the leftover deadline is shorter, it is extended by only that shortfall, so a rejected draft can run past 120 seconds. The caller waits that long on a continuation. An accepted first draft does not extend the deadline. A later rung counts as live only when it has a credential, is not marked dead and is not cooling down. A rung whose every model failed with 401, 402, 403, 404, `quota_or_billing` or `credit_balance` is marked dead for 10 minutes, in-process. A dead rung is still attempted, and a win clears the mark. Timeouts, 429 and 5xx never mark a rung dead (`engine._rung_slice`). A 429 whose body names a reset records `clients.provider_cooldown` (provider name and expiry) and later calls, including a new oneshot, skip that rung as `rate_limited_cooldown` until it lapses. A bare 429 records nothing.

A spent quota, at any status, also records that cooldown (`engine._note_provider_cooldown`). The body must name a quota, usage limit, credit balance, billing or payment required; overload and capacity errors do not count. The cooldown lasts until the reset the provider names: Codex `resets_at` epoch seconds, Antigravity `Resets in 24h34m35s`, a `Retry-After` header or an ISO time. With no reset it backs off 5 minutes and doubles on each further quota failure, up to 6 hours (`provider_cooldown.QUOTA_BACKOFF_BASE_S` and `QUOTA_BACKOFF_CAP_S`). The rung's other models are not tried, because they share the account. A cooling rung sends no request and holds no provider lock. The skip is recorded in `tried=`, so a ladder where every rung is cooling reads as an outage, not a content rejection. The first win clears the cooldown and the backoff step. The expiry is persisted and survives a restart; the doubling step is in-process.

Each provider admits `RADON_MODEL_PROVIDER_CONCURRENCY` in-flight calls at a time (`provider_gate.ProviderGate`; default 1, the VPS setting; 1 to 16, anything else refuses to start). Credential renewal takes the provider exclusively: it waits for in-flight calls to drain and holds new ones back while it waits. The first pass probes for a slot for 1 second and fails over when every slot is held. A busy provider then gets a second pass after the other rungs, still ahead of the Cerebras last resort, and waits up to 60 seconds of its slice for the lock. Busy is contention, not an outage, so a busy rung stays available to a continuation.

NVIDIA and Cerebras are paused in every environment by `engine.PAUSED_BY_DEFAULT` (operator, 2026-11-01). A paused rung reads as unkeyed, so no request is sent, and its stored key is kept. `RADON_LADDER_PAUSED_PROVIDERS` in the gateway env adds more rungs and cannot unpause these two. Cerebras returns 402 for every model. NVIDIA won 4 of about 1,550 requests over 3 days, and its research figure match is unsafe in either model order (pilot results, PR #1382). To unpause, remove the name from the set.

## Upgrades and rollback

CLI packages and inference dependencies are built at reviewed pins in immutable release images. The gateway target starts from `runtime-deps` and copies `scripts/` after the torch lock so a scripts-only push reuses those layers. `scripts/model_gateway/model-artifacts.json` pins the existing Chronos and local embedding snapshots and their asset hashes. Loading and cache migration verify those assets. Embedding model spaces do not change during fallback or upgrades.

A release needs all required checks on its exact head and matching immutable application, archive and gateway images. The managed gateway status reports the SHA of its already-selected immutable gateway image. The runtime supplies a gateway-only `RADON_DEPLOY_SHA` after validating the fixed image repository and full lowercase SHA; caller environment, dotenv values and a later checkout lookup are not identity authorities. Host coding clients are promoted from verified release artifacts; automated consumers do not install or upgrade provider CLIs. The first gateway cutover records a root-owned, hash-verified previous control-plane bundle for bounded rollback to that exact serving release. The privileged control-plane floor remains monotonic.

First-cutover recovery reads application HEAD as UID1000, binds the failed
target to root's pinned main, and refuses any committed-release marker. The
independent control-plane floor stays unchanged across rewritten history.
Capture also preserves canonical old files absent from a legacy manifest,
after verifying their previous-Git bytes, ownership and mode. Recovery reloads
the restored units before invoking the previous helper.

The exact sticky `root:1000` `/etc/radon` directory is sealed temporarily
through a pinned directory FD. Before chmod, recovery fsyncs a private
`config-parent-mode.json` intent inside its root-owned receipt. SIGKILL or
power-loss retry restores `01770` only when device, inode, ownership and modes
match that intent; an unrelated `01750` directory is never widened. The marker
is removed only after restored permissions reach stable storage. Pending
permission recovery blocks receipt reuse and archival. A later serving and
baseline-verified capture archives a completed attempt under a unique
target-named root-private directory before creating the next receipt.

Production verification must cover service readiness, isolated consumer environments, actual native client requests, provider fallback, token renewal, the forecasting and embedding spaces, and a real pipeline publication. A passing canary is not a deployed repair.

The native CLI transport canary (`scripts/ci/check_model_cli_transport.py`) stops each client's process group once the bridge captures its request. On macOS, `killpg` of a group whose only member has exited but is not yet reaped fails with EPERM rather than ESRCH, so the canary treats EPERM from an exited child as already stopped. An unexpected exception prints only its type and the raising `file:line`, never its message or child output.

Before allocating prestage worktrees, after exact image pulls and immediately
before stopping consumers, deployment runs the root-owned, read-only
`sudo /usr/local/sbin/radon-app-runtime check-model-space` action. Its fixed
per-filesystem budget uses non-root `f_bavail` and available inodes, includes
bounded simultaneous CLI archives/trees, pinned model-cache bytes not already
present at their pinned digest, member metadata, and retains a declared
1GiB/1024-inode operational/recovery margin. The CLI bounds are pinned per
release in `cloud/scripts/model_clients.py` (Linux payload 2030000000 bytes /
1024 members, Darwin export 880000000 bytes, about 25% over the observed
release); export and extraction refuse anything larger, and app-images runs
`scripts/ci/check_model_client_bounds.py` against the exact built image so a
bigger bundle fails CI. A later rebuild whose client payload hash already
passed that check (GHCR tag `pbounds-<64 hex>`, not a deploy SHA and not
`:latest`) skips the export and still smokes the new image; a missing tag
or payload hash still measures. The shared 4KiB filesystem budget is 5144893312
writable bytes (plus 545128448 when the model cache must be fetched) and 3209 inodes.
Unknown inode capacity refuses; root-reserved blocks never satisfy the check.
There is no threshold override or automatic data deletion. A rejected prestage
or capacity probe cannot fall back to a host compile. Promotion rechecks before
writing; concurrent disk growth can still cause a later refusal.

`deploy.sh` first runs `radon-app-runtime stage-model-clients <sha>` right after the exact-SHA pull, while the current release still serves. It exports (or reuse-clones) the target's Linux tree and Darwin bundle without moving `current`. `install-model-clients` then only selects a staged tree whose receipt `image_id` equals the image id inspected at that moment and whose Linux and Darwin receipts verify; any miss exports during restart as before. Staging is best-effort (no grant or a refusal only warns) and runs under the deploy lock, never from the prepull job, so it cannot race the fixed export container name. Before it, a changed gateway payload exported inside the `stop-clean` to `restart-managed` window and held app.radon.run on 502 for about 100 seconds (2026-11-11).

Promotion of a new exact SHA reuses a verified Linux receipt and Darwin
bundle when the local engine reports the same image id. Donors are exact
`ghcr.io/joemccann/radon-model-gateway:<40 hex>` tags, the same image id
listed under `localhost/ghcr.io/...` after a prune untagged the donor SHA,
or a Linux receipt that already recorded that image id. Failing those, a
donor tag whose image carries the same `radon.model-clients.payload-sha256`
label is reused (the hash of `docker/app/Dockerfile.python`, the vendor-clis
and chart-runtime locks, and the exported `model_gateway` helpers). It
hardlinks those bytes, rewrites only the release SHA, re-checks every hash,
and activates the Linux selector. It does not move the Darwin selector; that
still happens at commit. A tree that already exists for the deploying SHA is
not proof, because the registry tag can move. A missing tag, a different
image id without a matching payload label, a missing or malformed label, a
corrupt donor, or an inspect failure exports from the image as before.
A successful export records the image id on the new receipt so the next
identical image does not need the donor tag.

Gateway-era recovery verifies and reuses the already-installed exact Linux
executables/helpers and Darwin bundle using immutable receipts and hashes. It
can reselect the existing Linux release without a new export, payload copy or
fresh-install capacity gate. It does not publish a Mac export before commit.
Missing/corrupt payloads refuse. Cache-migration failures retain canonical refs,
remove temporary reference files and expose only the exception class and safe
errno name, such as `errno=ENOSPC`, with exit78. Pacing deadlines and provider
grants are unchanged.

Optional prestage defers without worktree allocation or a prestage marker when
the old control plane lacks the exact read-only disk-check sudo grant. Normal
deployment remains mandatory after trusted control-plane sync; an available
checker refusing capacity returns78 and cannot become a fallback build.



## Mac-local gateway for the nightly runners

The nightly runners do not use the VPS gateway. `scripts/runner/install.sh`
runs this same service on the Mac as `com.radon.model-gateway-local`, under the
disabled-login account `_radonmodelgw`, from a root-owned copy of
`GATEWAY_FILES` and a hash-pinned `httpx` venv (`scripts/runner/gateway-requirements.txt`).
It signs requests with the Mac's own subscription grants, kept in
`/Library/Radon/model-gateway-local/home` (mode 700, that account only):
Claude from a `claude setup-token` file, Codex from `CODEX_HOME/auth.json`,
Grok from `~/.grok/auth.json`, Antigravity from a dedicated keychain (macOS
agy has no token file; `RADON_ANTIGRAVITY_KEYCHAIN_HOME` holds only that
empty-password keychain, unlocked per call; agy's item is service `gemini`,
account `antigravity`) through a pinned, Google-signed macOS `agy`
(`scripts/runner/antigravity-darwin.json`, `install_antigravity.py`: SHA-512,
Developer ID team and arm64 checked before install). The `json` and `chat`
ladder runs here in the VPS order. Embeddings, OCR and forecasting stay on the VPS.
`_radonbot` reaches it only through the group socket
`/Library/Radon/model-gateway-local/run/gateway.sock` and never reads a grant.
It runs 6 concurrent calls per provider, so the two security loops do not
queue behind each other or behind production traffic. An expired Codex grant
is renewed on demand, non-interactively, for that provider only; renewal state
lives in `RADON_SUBSCRIPTION_STATE_DIR`. There is no renewal timer: the
interactive all-provider pass would page for providers this Mac never set up. Provisioning steps: [runner.md](runner.md#5-model-gateway-operator).

## One-time Mac transport installation

The Mac manager requires root-owned installed source. Running it directly from
an operator-owned checkout is deliberately refused. After the green exact-head
release is deployed on `radon-app-root`, fetch the installed control-plane
manager through that existing authenticated SSH connection:

```sh
manager=$(mktemp -t radon-model-manager)
ssh radon-app-root 'cat /usr/local/lib/radon/mac_model_gateway.py' > "$manager"
sudo /usr/bin/install -d -o root -g wheel -m 0755 /Library/Radon /Library/Radon/model-gateway
sudo /usr/bin/install -o root -g wheel -m 0555 "$manager" /Library/Radon/model-gateway/manager.py
rm "$manager"
```

Obtain the server's **public** Ed25519 host key through that authenticated
connection (`ssh radon-app-root 'cat /etc/ssh/ssh_host_ed25519_key.pub'`), rather
than trusting an unauthenticated key scan. Use the actual model host DNS name
and public host-key pair in this root command; the final argument is the local
operator account, `joemccann-mac-mini` on the current Mac:

```sh
sudo /usr/bin/python3 -I /Library/Radon/model-gateway/manager.py prepare MODEL_HOST 'ssh-ed25519 AUTHENTICATED_PUBLIC_HOST_KEY' joemccann-mac-mini
```

`prepare` is idempotent and prints only its newly dedicated public client key.
The Linux operator authorizes that key with the installed root helper:

```sh
/usr/bin/python3 -I /usr/local/lib/radon/model_transport.py prepare 'ssh-ed25519 DEDICATED_MAC_PUBLIC_KEY'
systemctl restart radon-model-gateway.service
```

Run the Linux commands as root on the app host. They create a dedicated
`radon-model-tunnel` SSH account and the `radon-model-connect` inference-only
group, validate SSH configuration, and reload SSH. The account has no shell,
TCP forwarding, agent forwarding, PTY, writable export directory, provider
grants, management socket, vault socket, or broader `radon` group membership.
Its authorized key lives in root-only `/etc/ssh/radon-model-transport`, not
under the radon-writable sticky `/etc/radon` (sshd `StrictModes` refuses keys
there); the helper trusts `/etc/radon/env` only under that exact `root:radon`
`01770`/`01750` parent.
Its forced read-only SFTP session is chrooted to
`/var/lib/radon-model-export`; its Unix forwarding admits only `/run/radon-model-gateway/gateway.sock`.
The gateway start hook binds only that inference directory read-only inside
the chroot, verifies source inode and mount protections on restart, and
recreates the ephemeral bind after reboot. OpenSSH 9.6 shares local TCP and
Unix admission, so `AllowTcpForwarding local` is paired with the exact
bracketed Unix-path `PermitOpen`; no network destination matches that path.
The real SSH fixture verifies TCP and management access remain denied. Numeric UID/GID routing fields are the only new
values written to the existing root-owned environment file.

After authorization, activate the supervised Mac transport and updater:

```sh
sudo /usr/bin/python3 -I /Library/Radon/model-gateway/manager.py activate
```

Activation verifies the published bundle before starting launchd. It checks
the release manifest, every file's SHA-256 and size, Apple signatures and
arm64 architecture, and root ownership/nonwritable ancestry before selecting
`/Library/Radon/model-clients/current`. Only four native clients and four
consumer transport helpers are installed. Provider credentials, native
provider engine code, operator SSH keys and GitHub tokens are not included.
The private Mac socket is `/Library/Radon/model-gateway/gateway.sock`;
`_radonmodel` grants traversal only to the named operator and `_radonbot`.
Root launchd supervises SSH and retries it after disconnects; a separate
30-minute job verifies and promotes only the latest successfully committed
root export. Keep four prior client receipts and every release demoted within two hours
for bounded active sessions. On the VPS, Linux client releases and root
exports keep only the selected release plus the newest demoted one (the
rollback target); older ones are pruned once demoted for two hours. A payload
byte-identical to the selected release is hardlinked (never symlinked) into the
new SHA, so an unchanged deploy costs a receipt, not another 1.6G/0.7G copy.

This one-time root installation is separate from successful source/CI
verification. Do not report the Mac service deployed until its launchd job,
private socket and a real native client request have been verified. Migrate
or quarantine unattended bot grants only after canonical gateway grants and
the transport work; leave personal operator credentials untouched.

## Deployment finalization failures

Before deleting reversible unit snapshots, root writes and fsyncs the exact
committed release SHA under `/var/lib/radon-deploy/committed-release`.
`verify-committed` is a fixed read-only helper operation, with no path or SHA
arguments. A present receipt returns its stored SHA independently of checkout
identity; only an absent receipt returns the uncommitted status. The app journal's `finalization_pending` phase retries immutable
Darwin export selection and retirement of mutable updater timers. A crash or
export error after the root commit never triggers rollback using discarded
snapshots, even if service health changes later. New transitions clear the
old receipt before taking a new reversible snapshot. An unexplained receipt
mismatch or unreadable commit status fails closed for operator review.


The gateway pins NVIDIA pacing state to
`/var/lib/radon/model-cache/nvidia-rate.json` in its persistent private cache.
Operator RPM, trip threshold, cooldown and wait limits survive environment
filtering. Before stopping managed containers, root captures only validated
numeric account clocks as UID1000, including `.tripped` markers; it also
rechecks each fixed unit immediately before stopping it. Every
`radon-app-runtime` app unit must be in `model_pacing.UNITS`: `halt` runs the
recheck as ExecStop, and an unlisted unit exits 64 and marks a clean run
failed. Python snapshots
join the existing account lock for at most 250ms. Captured clocks are staged
privately, mounted read-only into the unprivileged image migrator, and merged
without weakening existing deadlines. Per-source digest receipts prevent an
unchanged old failure streak from reappearing after later successful requests.
An unreadable or malformed pacing snapshot blocks teardown rather than
resetting account limits. In-flight provider writes after the final snapshot
are not claimed as verified; the new gateway state itself persists directly.

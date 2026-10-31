# Shared model service

All Radon inference goes through `scripts/model_gateway`. App processes keep their existing Python and TypeScript interfaces; their adapters send bounded requests to this service. Provider grants and model runtimes live in the service. A failed service connection never opens a direct-provider fallback.

## Interfaces

Python app callers use `clients.model_ladder` for text/vision JSON and its local acceptance callback. A rejected result resumes a single-use service continuation under the original deadline. Node pipeline callers use `scripts/model_gateway/client.js`; Next.js uses `web/lib/llm/provider.ts` through the authenticated FastAPI `/model-gateway` bridge. New consumers use these same adapters.

Fixed operations cover normalized chat with tools/images, JSON, vision extraction, explicit 384/2048-dimensional embedding spaces, OCR, X search, Chronos forecasting, and Clairic dates. Inputs cannot supply upstream URLs, headers, credentials, paths, or arbitrary commands. Existing publication, Humanizer, portfolio, source, trading and provider pacing rules remain in their owning pipelines.

The default text/JSON order starts with Antigravity. Requests needing images or native tools use compatible providers. Native Messages, Responses and Chat Completions preserve their original schemas, including custom tool grammars, and use only providers supporting that protocol. A response must reach a verified terminal state before delivery. Unsupported capability is an error; schemas and images are never silently discarded.

## Authentication and renewal

The inference socket is `/run/radon-model-gateway/gateway.sock`. The separate management socket is `/run/radon-model-control/control.sock`. Only privileged credential-validation, catalog and keeper clients receive the management mount. Neither socket is a TCP listener. Coding agents use an ephemeral authenticated loopback adapter to reach the private service; tool execution stays inside the agent's existing local permissions and approval policy.

The gateway receives canonical subscription grants, a scoped credential-broker socket and fixed model-key slots. It does not receive the secret-store master key. Stored NVIDIA, Cerebras and Cloudflare values take effect through the broker; application processes do not export those model secrets. The subscription timer requests gateway-owned renewal jobs. Inference-triggered renewal cannot launch an interactive login. A provider requiring renewed human authorization remains unavailable and retains the existing alert path.

The service owns model selection, deadlines, concurrency, cancellation and refresh coordination. SLM modes and sampling also run there using the request's current taxonomy; the newsfeed caller persists comparison telemetry with its existing database writer. Model inventory reads cached, sanitized metadata and marks configured defaults explicitly when discovery is unavailable. Missing context limits are unknown, never estimated.

## Upgrades and rollback

CLI packages and inference dependencies are built at reviewed pins in immutable release images. `scripts/model_gateway/model-artifacts.json` pins the existing Chronos and local embedding snapshots and their asset hashes. Loading and cache migration verify those assets. Embedding model spaces do not change during fallback or upgrades.

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

Before allocating prestage worktrees, after exact image pulls and immediately
before stopping consumers, deployment runs the root-owned, read-only
`sudo /usr/local/sbin/radon-app-runtime check-model-space` action. Its fixed
per-filesystem budget uses non-root `f_bavail` and available inodes, includes
bounded simultaneous CLI archives/trees, pinned model-cache bytes and member
metadata, and retains a declared 1GiB/1024-inode operational/recovery margin.
The shared 4KiB filesystem budget is 8881176576 writable bytes and 201161 inodes.
Unknown inode capacity refuses; root-reserved blocks never satisfy the check.
There is no threshold override or automatic data deletion. A rejected prestage
or capacity probe cannot fall back to a host compile. Promotion rechecks before
writing; concurrent disk growth can still cause a later refusal.

Promotion of a new exact SHA reuses a verified Linux receipt and Darwin
bundle when the local engine reports the same image id for another exact
`ghcr.io/joemccann/radon-model-gateway:<40 hex>` tag. It copies those bytes,
rewrites only the release SHA, re-checks every hash, and activates the Linux
selector. It does not move the Darwin selector; that still happens at commit.
A tree that already exists for the deploying SHA is not proof, because the
registry tag can move. A missing tag, a different image id, a corrupt donor,
or an inspect failure exports from the image as before.

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
for bounded active sessions. Linux client releases and root exports use the
same retention floor; no unbounded per-deploy CLI archive is kept.

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
rechecks each fixed unit immediately before stopping it. Python snapshots
join the existing account lock for at most 250ms. Captured clocks are staged
privately, mounted read-only into the unprivileged image migrator, and merged
without weakening existing deadlines. Per-source digest receipts prevent an
unchanged old failure streak from reappearing after later successful requests.
An unreadable or malformed pacing snapshot blocks teardown rather than
resetting account limits. In-flight provider writes after the final snapshot
are not claimed as verified; the new gateway state itself persists directly.

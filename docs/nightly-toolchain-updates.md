# Nightly toolchain maintenance

The operator's old `com.joemccann.ai-cli-updater` attempted an npm-global
Codex update even when `/opt/homebrew/bin/codex` belonged to Homebrew. Those
attempts failed with `EEXIST`. Desktop/CLI ownership and the DeepSec vendored
reviewer are separate update surfaces. Updating the shared `codex` does not
replace the CLI selected by DeepSec's SDK.

## Reviewer release policy

`scripts/runner/toolchain-policy.json` records the minimum tested DeepSec and
Codex versions and the explicit subscription model. The checked-in
`toolchain/package-lock.json` is a reproducible recovery baseline. Its npm
overrides advance both `@openai/codex` and `@openai/codex-sdk`; a pre-1.0
caret dependency on `^0.145.0` cannot advance to `0.162.1` by itself.

`nightly_toolchain_update.py stage` installs this baseline into a new private
candidate directory with `npm ci --ignore-scripts`. `refresh` queries the
official npm registry for exact stable versions, writes a candidate lockfile,
and stages that exact tree. Automatic candidates permit same-major DeepSec
releases while retaining the reviewed Codex CLI/SDK pins. Model client pins
advance only through a reviewed CI change. A major DeepSec change, failed
install or failed smoke leaves the working reviewer unchanged. No mutable
package selector runs in the scanner.

Every candidate reports the exact installed versions and passes DeepSec's CLI
startup. Inference smoke runs through the private model gateway, which owns
subscription authentication and model fallback; it never loads a bot grant or
launches direct SDK inference. Process/revalidate uses the gateway's supported
DeepSec bridge adapter with an ephemeral loopback token. The SDK's custom
Responses provider keeps tool execution and existing sandbox policy local.
Failure diagnostics remain mode `0600` in the private candidate directory.

## Bot maintenance and recovery

The fresh nightly prompt calls `ensure-deepsec --workspace .deepsec` before
source processing. This is a narrow rootless recovery path: the dedicated
`_radonbot` identity, canonical workspace/state, ancestry under the live
security-deepsec LaunchDaemon and absence of an active reviewer are required.
The existing installed runner already owns this loop's exclusive lifecycle
lock. Its KEEP_PATHS implementation moves `.deepsec` between work and keep;
no other loop or global CLI uses this tree. A separate advisory gate
serializes preflights. Candidate installation, real inference, promotion and
post-promotion inference finish before any process/revalidate command starts.
Promotion journals and rollback trees live outside the disposable clone in
this loop's private state. Interrupted promotion is restored before retry.
The audit stays INCOMPLETE if recovery or the actual installed smoke fails.
This path needs no new root helper, credentials or LaunchDaemon install.


Run maintenance as `_radonbot`, which owns the workspace and subscription,
while all nightly loops and reviewers are idle:

```sh
python3.13 scripts/nightly_toolchain_update.py maintain \
  --workspace /Users/_radonbot/radon-runner/state/security-deepsec/keep/.deepsec
```

`maintain` resolves, stages, smokes, promotes, then smokes the installed tree.
The JSON result names the rollback directory. Config, matchers, private run
records, exports and incremental `data/radon` state are preserved. The update
never changes the audited SHA or declares the backlog reviewed. Resume the
existing incomplete audit with its recorded immutable range after maintenance.
The per-audit `ensure-deepsec --workspace .deepsec` must pass before processing that range.

Promotion journals each package-tree move. An interrupted promotion refuses
another promotion. Restore the journal's recorded backup with:

```sh
python3.13 scripts/nightly_toolchain_update.py rollback \
  --workspace /path/to/.deepsec --stage /path/from/promotion-journal
```

Reviewer promotion requires `/usr/local/radon-runner/maintenance.lock` and
`maintenance-ready.json`, root-owned and protected. The readiness marker names
all seven cooperative loop wrappers (`version: 1`, `loops: [...]`). Each wrapper
holds a shared file lock for its entire runner lifetime; promotion holds an
exclusive lock. The helper also checks every installed launchd loop and agent
process before mutation and defers on active or unverifiable state; it never reclaims a runner
lock, kills a process or restarts an active application. Without the installed gate and complete readiness marker, generic maintenance
promotion refuses with no workspace mutation. The narrower `ensure-deepsec`
path relies on the existing per-loop lifecycle and validates that scope
before touching the private reviewer tree; it never updates any shared CLI.

The Mini's bot home and LaunchDaemons require operator privilege to provision.
The current operator account cannot install or run bot maintenance without
that privilege. Existing keychain-unlock sudo permission is not a shell or
maintenance authorization. Provider grants remain inside the model service;
runner clients receive only its private socket and ephemeral bridge tokens.

## Desktop applications

`operator` retains its installed twice-daily 09:00/21:00 LaunchAgent for
allowlisted desktop applications. `native` reports `managed-by-gateway-ci`;
it performs no Claude, Grok, Agy, Muse or Codex self-update. Model clients are
immutable CI artifacts with exact package versions and recorded hashes.

`desktop` refreshes Homebrew metadata and updates installed allowlisted VS Code
and Obsidian casks only after their processes exit. Codex returns
`managed-by-gateway-ci` without a cask upgrade. Activity gates remain per tool.
No forced quit, credential extraction or OS restart is used. The installed
updater snapshot and previous plist remain under
`~/.local/share/radon-toolchain-updater/releases/`.

Observed installed applications also include ChatGPT, Claude, Chrome, Brave,
Docker, Tailscale, Signal, Dropbox, Warp and Apple App Store apps. These are not
managed by the three installed Homebrew casks. Preserve their vendor updaters;
this helper reports an unowned cask rather than claiming an update. ChatGPT's
Codex app-server daemon is another managed runtime, separate from shared CLI
and DeepSec. Vendor authentication, signed app updates, App Store approval,
OS updates and required restarts cannot be bypassed by this script.

Official OpenAI installation guidance uses [Homebrew cask upgrades for a cask
installation](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex).
The [GPT-6.1 Sol model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
documents the explicit model; actual subscription access is established by
the per-account SDK smoke, not inferred from that page.

## Installed runner self-refresh

After its one-time administrator bootstrap, the root timer invokes
`scripts/runner/update_installed.py` with isolated Python and no arguments.
It fetches only public `joemccann/radon` main into a protected root-owned
bare store, requires the latest exact-SHA `ci.yml` push run and its
`Deploy to VPS` job to finish successfully, and archives that immutable
commit's scripts into a validated root-owned snapshot. It accepts no bot
path, ref, environment, token, SSH agent or command. Under the exclusive
maintenance gate it defers active loops, rechecks main/CI, installs all seven
loops and records success only after the installer exits successfully.
Failed or pending CI and rate-limited/unavailable public metadata defer the
refresh; the next timer retries. The installed helper is itself copied by
the reviewed main installer. The maintenance lock inode must remain stable,
and refreshing must not unload the currently executing root updater job.

The first root timer and protected files still require macOS administrator
installation. Source publication cannot grant the current operator session
that OS privilege. Once installed, reviewed merged changes no longer require
a separate manual runner reinstall each night.

The separate bot maintenance timer uses `native`, which requires the actual
`_radonbot` account and its canonical home, then holds the cooperative
exclusive gate while updating only that account's native CLIs. It never
invokes Homebrew or changes the operator's installations. Each busy bot CLI
is deferred independently; a managed Codex app-server does not block this
maintenance action. The bot's release state is private and retained.

### Immutable Claude runtime floor

Host Bun updates do not alter Claude's bundled runtime. The app image pins
Claude Code 2.1.311, whose integrity-verified Linux payload contains Bun 1.4.4.
Its existing unprivileged image smoke now checks the embedded Bun version as
well as the CLI version and root ownership, and rejects a missing or older
runtime marker. Promote a new exact native package through the normal image
CI/deploy; never mutate the running container or enable its self-updater.

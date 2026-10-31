# Nightly agent runner

One loop run is what the operator would do by hand: open an agent CLI in a fresh clone of `main`, paste the loop's prompt, walk away. `scripts/runner/run_loop.sh <loop>` does exactly that:

1. Take the loop's lock (a live run makes the new fire page `skipped` and exit 0; a proven dead or reused pid is reclaimed by one contender). Missing identity metadata or an unreadable process fingerprint refuses with exit 75 and preserves the clone (REL-291 / R-710).
2. Delete the previous clone and `git clone` `main` again, so nothing an agent left behind (files, `.git` hooks, config) survives to the next night.
3. Prepend a header (date, branch `<BRANCH_PREFIX>/<date>`, time budget) to `.claude/runner-prompts/<loop>.md` from that clone.
4. Run the first agent in `AGENTS` under `timeout`. The next agent runs only if one exits non-zero; a timeout ends the night. Anything the agent left running is killed with its process group.
5. Look up the PR for the branch and send one Pushover message with the agent's `RESULT:` line and the PR link.

The agent does the whole job in one session: audit, fix, push the branch, open a draft PR, watch CI. Every PR body follows `.github/pull_request_template.md`; the gh guard (`nightly_pr_guard.py`), `nightly_publish.py publish` and the `PR body template` CI check refuse any other shape (`scripts/ci/check_pr_body.py`, installed beside the guard by `install.sh`). The prompt owns what the loop does; the runner owns nothing else.

The two security loops (`security`, `security-deepsec`) also use the optional knobs in [Loop config](#loop-config): one session per phase (audit, remediate, deliver), a claude-only ladder from a resolver, a root-owned `gh` guard, and root-owned pre- and post-run hooks that port the old wrapper's rails (credential and billing-reroute refusals, the green-base checkout, the sanitized dead-man (which keeps the quota ladder's usage URL only when sentence punctuation alone follows it), the private report). [docs/operations.md](operations.md#background-services) has the operator contract. The `security` loop's native audit workflow cannot read the environment, so its prompt passes `$RADON_REPO_ROOT` as `args.repoRoot`. When a session lists only plugin workflows, the prompt runs the clone's `.claude/workflows/security-audit.mjs` by absolute `scriptPath` instead of by name.

## Model service and immutable clients

Automated coding and incident inference uses the private model gateway. The
reviewed gateway image supplies model clients at `/opt/radon-model-clients` on
Linux and `/Library/Radon/model-clients` on the Mac runner;
the installer verifies the release and records executable/helper SHA-256 hashes
and exact CLI versions in `current/manifest.json`. Incident runtime selection
checks root ownership, protected ancestry, immutable release files and matching
hashes before using a client. Its model inventory comes from the gateway, not a
CLI provider login probe. Existing last-known-good fallback and execution gates
remain in force.

`agent_cli_bootstrap.sh` now reports gateway status only. It creates no key file
and rewrites no operator settings. Native CLI self-updates and desktop Codex
updates are retired from `nightly_toolchain_update.py`; model pins move through
reviewed CI artifacts. DeepSec package/version checks remain, while its smoke
uses gateway inference. DeepSec process/revalidate commands use the bridge
adapter, whose temporary loopback credential selects the pinned SDK's custom
Responses provider without reading local subscription grants.

## Safety boundary

Safety is where the runner runs, not what the script checks:

| Control | Enforced by |
|---|---|
| Agent cannot read operator files, ssh keys, `~/.radon`, Keychain | Dedicated macOS user `_radonbot`, standard (non-admin), hidden |
| Agent cannot edit the runner | `run_loop.sh`, `loops/*.env`, the hooks, their `lib/` helpers, the `gh` guard and `gitconfig` installed root-owned in `/usr/local/radon-runner` |
| Agent cannot plant a binary or git config the runner runs | The LaunchDaemon's `PATH` is `/opt/homebrew/bin:/usr/bin:/bin`; root-owned gateway client shims are prepended only for the agent. Every git the runner, the hooks and the agent run reads `/usr/local/radon-runner/gitconfig` (`GIT_CONFIG_GLOBAL`, `GIT_CONFIG_NOSYSTEM=1`), never `~/.gitconfig`, and the pre-run hook rebuilds the clone's `.git/config` before every phase |
| No production credential in the clone | The clone gets no `.env`. The runner loads only `PUSHOVER_USER` and `PUSHOVER_TOKEN` from `RADON_RUNNER_DOTENV` or `/usr/local/radon-runner/dotenv-path` (never from the plist), then falls back to `~/.radon-runner.env`. `GH_TOKEN` loads from `~/.radon-runner.env`; model grants remain in the gateway and are stripped from child environments. `_radonbot` and every agent share a uid, so any file the runner can read, the agent can read. The runner unsets the Pushover keys in the agent process and sends every notification itself; each runner prompt tells the agent not to send `radon PR green` or list the absent keys under Next. `GH_TOKEN` stays so the agent can push. Point the dotenv at a Pushover-only file (step 8), not the operator's full Radon `.env`. Never put a production credential or an admin token in `~/.radon-runner.env` or anywhere in the bot's home |
| Agent cannot merge or push `main` | A separate GitHub machine account with the Write role on this repo only, used through its classic token (`repo` plus `workflow`, so nightly CI work that edits `.github/workflows` can be pushed to a branch), plus the `main-review` repository ruleset on the default branch: 1 approving review, approval of the most recent push required, stale approvals dismissed on push. Its only bypass actor is the Repository admin role in pull-request mode, so an admin merges through a PR and never pushes `main` directly; the bot account is not exempt. The 27 required status checks stay in the classic branch protection on `main`, not in the ruleset. A token of the operator's own account would not do: it acts as the admin and bypasses the ruleset |

The seven loops in [Migration](#migration-from-the-per-loop-wrappers) use the bot runner. `scripts/codemap_nightly.sh` is separate and opens a refresh PR for operator review and merge; the job never merges. Verify the live ruleset in step 8b.3 before enabling a new installation.

## Set up a new Mac

The whole install, start to finish, on a Mac that has never run Radon. Allow about 45 minutes. Everything runs in Terminal as the operator (an admin account); a step says when to switch to the bot shell. You never need to log in to the bot's desktop.

Where things live when you are done:

| Path | Owner | Holds |
|---|---|---|
| `/usr/local/radon-runner/run_loop.sh`, `loops/<loop>.env` | root | The runner and each loop's config. The agent cannot edit them |
| `/usr/local/radon-runner/hooks/`, `lib/`, `guard/<loop>/gh` | root | The security loops' pre/post-run hooks, the helpers they and the `gh` guard run (copied from `scripts/`), and the guard shim |
| `/Library/LaunchDaemons/com.radon.runner.<loop>.plist` | root | The nightly schedule, run as `_radonbot` |
| `/usr/local/radon-runner/dotenv-path` | root, 644 | Absolute path to the Pushover dotenv. Written only when install is run with `RADON_RUNNER_DOTENV`. A later install without that var leaves the file in place. Never a secret value |
| `/Users/_radonbot/.radon-runner.env` | bot, 600 | `GH_TOKEN` and, as legacy fallback, `PUSHOVER_USER` / `PUSHOVER_TOKEN`. The agent can read it (it runs as the bot); the runner unsets the Pushover keys in the agent process. Never a production or admin credential |
| `/Library/Radon/model-gateway/identity` | root, 600 | Dedicated model-only SSH transport key; never a provider or operator key |
| `/Users/_radonbot/radon-runner/work/<loop>` | bot | Tonight's clone, deleted and re-cloned every run |
| `/Users/_radonbot/radon-runner/logs/<loop>/<date>.log` | bot, 700 | The run log, kept 14 days |
| `/Users/_radonbot/radon-runner/state/<loop>` | bot, 700 | Per-loop state the runner never deletes: the security loops' private `scratch/`, deliver record, `held.git` (unreleased P0/P1 branches) and `keep/` |
| `/Users/_radonbot/radon-runner/tmp` | bot, 700 | The daemons' `TMPDIR`, so the bot's temporary files never land in the shared, world-writable `/tmp` |
| `/Users/_radonbot/.radon-runner-reports-key` | bot, 600 | Write deploy key for the private `joemccann/radon-security-reports`, used by the post-run hook. The agent runs as the same user and can read it, as it could read the old wrapper's key; a deploy key reaches only that one repository |
| Operator login keychain: `github-radon-runner-bot`, `github-radon-runner-bot-token` | operator | The bot's GitHub password and token, for rotation |

### 1. Prerequisites (operator)

```bash
# Homebrew first if the Mac has none: https://brew.sh
brew install git gh coreutils python@3.13 uv bun node
# gitleaks 8.30.1 for the security loops (docs/security-approved-tools.md), checksum-pinned, root-owned
GL="$(mktemp -d)" && curl -fsSLo "$GL/gitleaks.tgz" https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_darwin_arm64.tar.gz \
  && echo "b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5  $GL/gitleaks.tgz" | shasum -a 256 -c \
  && tar -xzf "$GL/gitleaks.tgz" -C "$GL" gitleaks && sudo install -o root -g wheel -m 755 "$GL/gitleaks" /usr/local/bin/gitleaks \
  && sudo ln -sf /usr/local/bin/gitleaks /opt/homebrew/bin/gitleaks && rm -rf "$GL"
/opt/homebrew/bin/gitleaks version   # 8.30.1
sudo pmset -a sleep 0 displaysleep 10     # the Mac must stay awake at night
```

`coreutils` provides `timeout`; the runner exits 69 without it. `node` runs Vitest for the testing loop (bun installs the packages, `npx vitest run` runs them); without it that loop leaves Vitest to PR CI. For the security loop, `security_pre.sh` installs both bun projects (`bun install --frozen-lockfile --ignore-scripts`, root and `web/`) and `KEEP_PATHS` carries `node_modules` and `web/node_modules` across the nightly re-clone, so they reinstall only when a lockfile or `package.json` changes. A failed install skips the Vitest stage; it never refuses the phase. Model CLIs come from the immutable client updater in step 4; Homebrew does not update them.

### 2. Install the runner (operator)

```bash
RI="$(mktemp -d)" && /usr/bin/git clone https://github.com/joemccann/radon.git "$RI"
/bin/bash "$RI/scripts/runner/install.sh" --print-plist documentation   # review
sudo /bin/bash "$RI/scripts/runner/install.sh" documentation ci-performance performance testing reliability security security-deepsec
/bin/rm -rf "$RI"
```

Always clone into a fresh `mktemp -d` directory, never a fixed `/tmp` path another account could create first. `install.sh` refuses (exit 77) when the clone, or any directory above it, is owned by someone other than root or you, or is group- or world-writable. The same bar applies to `scripts/` and to every directory a `LIB_FILES` entry lives in, derived from that list rather than written out, so a new library subdirectory is checked without anyone remembering to extend it. Paths are resolved physically (`pwd -P`) on both sides of the check, so a symlinked ancestor cannot make validation and the later reads walk different chains.

It asks for your sudo password, then `User password:` for the new `_radonbot` account. Choose a password and save it (your password manager, or `security add-generic-password -s radon-runnerbot-login -a _radonbot -w`); step 5 reuses it. The `No clear text password ... FDE` warning and `Home directory is assigned (not created!)` are expected: the script creates the home right after. It ends with one `installed com.radon.runner.<loop>` line per loop.

The account is hidden, so it does not appear on the login screen. Check it:

```bash
id _radonbot && ls -ld /Users/_radonbot /usr/local/radon-runner/run_loop.sh
```

### 3. Open the bot shell

Open a second Terminal tab for this. Steps 4, 5, 6, the bot-shell parts of 8b, and 9 run in it:

```bash
sudo -u _radonbot -H /bin/zsh -l
```

The prompt shows `_radonbot@...`. Before running a check, confirm its output paths start with `/Users/_radonbot`. The same command in your own tab checks your account instead.

### 4. Agent CLIs (bot shell)

The root-owned model-client updater supplies Claude Code, Codex and Grok from
the exact CI-tested gateway release. Do not install provider CLIs, run provider
login, or copy model API keys into the bot account. The runner resolves clients
from `/Library/Radon/model-clients/current` and routes native model requests
through its authenticated local adapter. Existing tool permissions, GitHub
identity and process-group cleanup remain local.

### 5. Model transport (operator)

Provision the root-owned `cloud/scripts/mac_model_gateway.py` helper with the
model host's authenticated Ed25519 host key and operator account. Its `prepare`
operation creates a dedicated transport key; the corresponding Linux
`cloud/scripts/model_transport.py prepare` grants that public key only
read-only artifact SFTP and private inference-socket forwarding. The Mac
`activate` operation verifies the release and installs supervised tunnel and
half-hour artifact-update jobs. Neither operation receives an operator SSH key
or a provider grant. See [the service contract](model-gateway.md).

Provider authentication and renewal belong to the Linux gateway. A Mac reboot
does not require unlocking the bot's model keychain. A provider requiring new
human consent uses the gateway's existing reauthorization alert path.

### 6. Model readiness (bot shell)

```bash
RADON_MODEL_GATEWAY_SOCKET=/Library/Radon/model-gateway/gateway.sock \
  python3.13 -I /Library/Radon/model-clients/current/lib/model_gateway/cli.py status
```

The result identifies wired providers and service capabilities. A failed
connection stops model work; it never creates local provider settings or
credentials. `agent_cli_bootstrap.sh` performs the same status check. The old
bot-side NVIDIA/Cerebras env, native login files and fx proxy are retired.
Only known bot model grants are quarantined by the privileged cutover after
canonical gateway grants are verified; personal operator CLI logins are not
part of that migration.

### 7. GitHub machine account (operator, browser)

The bot pushes and opens PRs as its own account, so the `main` ruleset binds it. Your own token would act as the repo admin and bypass the ruleset. GitHub allows one free machine account per person.

1. Sign out of GitHub (or use a private window), then open https://github.com/signup. Use an email no other GitHub account has; `joseph.isaac@gmail.com` is taken by `joemccann`, so the existing bot uses `joe@cryptdex.trade`. Username `radon-runner-bot` (or `radon-runner-bot2` if you rebuild while the old one exists). Generate the password and store it before submitting:

   ```bash
   security add-generic-password -s github-radon-runner-bot -a radon-runner-bot -w "$(openssl rand -base64 24 | tr -d '/+=')"
   security find-generic-password -s github-radon-runner-bot -w | pbcopy
   ```

   Watch for browser autofill: Chrome fills your own `joemccann` username and password into the signup form. Clear both fields and paste the bot's values before you press Create account.
2. Enter the 8-digit code GitHub emails to that address, then sign in as the bot.
3. From your own account, in Terminal, invite it with the Write role:

   ```bash
   gh api -X PUT repos/joemccann/radon/collaborators/radon-runner-bot -f permission=push
   ```

4. As the bot, accept at https://github.com/joemccann/radon/invitations. Confirm from your account: `gh api repos/joemccann/radon/collaborators/radon-runner-bot/permission --jq .permission` prints `write`.
5. As the bot, open https://github.com/settings/tokens/new?scopes=repo,workflow&description=radon-runner. Keep only `repo` and `workflow` ticked, set Expiration to Custom with a date one year out, then Generate. Copy the `ghp_...` value into your keychain without echoing it:

   ```bash
   read -rs "T?token: "; security add-generic-password -U -s github-radon-runner-bot-token -a radon-runner-bot -w "$T"; unset T
   ```

6. Check the token acts as the bot with only `repo` and `workflow`:

   ```bash
   T=$(security find-generic-password -s github-radon-runner-bot-token -w)
   curl -sI -H "Authorization: token $T" https://api.github.com/user | grep -iE 'x-oauth-scopes|token-expiration'
   curl -s -H "Authorization: token $T" https://api.github.com/user | grep '"login"'; unset T
   ```

   Expect `x-oauth-scopes: repo, workflow`, your chosen expiry, and `"login": "radon-runner-bot"`.

### 8. Runner secrets (operator)

Write the token into the bot's secrets file straight from your keychain:

```bash
security find-generic-password -s github-radon-runner-bot-token -w | sudo -u _radonbot /bin/sh -c 'umask 077; read -r t; f=/Users/_radonbot/.radon-runner.env; { grep -v "^GH_TOKEN=" "$f"; printf "GH_TOKEN=%s\n" "$t"; } > "$f.new" && mv "$f.new" "$f"'
```

Pushover is a pair. The runner takes both keys from the first source that has both, and never mixes a half pair:

1. both `PUSHOVER_USER` and `PUSHOVER_TOKEN` already set in the inherited environment (launchd never sets them; this is for manual runs and tests)
2. the Radon dotenv: `RADON_RUNNER_DOTENV` if set, else the first line of `/usr/local/radon-runner/dotenv-path`
3. `~/.radon-runner.env` (legacy fallback)

Only those two keys are read from the dotenv. `GH_TOKEN` stays in `~/.radon-runner.env`. The path is not in the plist, so regenerating the plists cannot drop it.

`_radonbot` and every agent run as the same uid, so whatever file the runner can read, the agent can read. Pointing `RADON_RUNNER_DOTENV` at the full Radon root `.env` and granting the bot read access exposes every production key in it to the agents, which contradicts the safety table. Use a Pushover-only file, or a copy containing only the two notification keys:

```bash
grep -E '^(export )?(PUSHOVER_USER|PUSHOVER_TOKEN)=' /path/to/radon/.env | sudo -u _radonbot /bin/sh -c 'umask 077; d=/Users/_radonbot/.radon; mkdir -p $d && chmod 700 $d && cat > $d/pushover.env'
```

Set the path at install time (a refresh without the var leaves `dotenv-path` in place). Refuse a relative path. `install.sh` never chmod, chown or copies the target `.env`. If the bot cannot read it, install prints one WARNING and continues:

```bash
sudo RADON_RUNNER_DOTENV=/Users/_radonbot/.radon/pushover.env /bin/bash /path/to/clone/scripts/runner/install.sh reliability testing documentation ci-performance performance security security-deepsec
sudo -u _radonbot test -r /Users/_radonbot/.radon/pushover.env && echo readable
```

Check presence without printing values. `2` means both keys are set:

```bash
grep -cE '^(export )?PUSHOVER_(USER|TOKEN)=.' /Users/_radonbot/.radon/pushover.env
grep -oE '^(GH_TOKEN=ghp_|PUSHOVER_USER=.|PUSHOVER_TOKEN=.)' ~/.radon-runner.env    # 3 lines if the legacy file still holds the pair
```

Each run logs exactly one presence line, paths only, never values. A half pair logs `incomplete` for that source and moves on. A curl failure logs `pushover: send failed status=<code>` and the run continues:

```
pushover: PUSHOVER_USER present, PUSHOVER_TOKEN present (source: dotenv /Users/_radonbot/.radon/pushover.env)
pushover: credentials missing (checked: env, /Users/_radonbot/.radon/pushover.env, /Users/_radonbot/.radon-runner.env); notifications skipped
```

To rotate a token, replace `PUSHOVER_TOKEN` in the chosen dotenv and blank or fix the stale one in `/Users/_radonbot/.radon-runner.env`. Then kick a loop and confirm one `pushover:` present line in its log.

### 8b. Security loops (operator and bot shell)

The `security` and `security-deepsec` loops need CI-pinned Claude Code through the private gateway, a deploy key for the private reports repository, their night-to-night state and DeepSec's workspace. The first three steps retire the old per-loop LaunchAgents; skip them on a Mac that never ran the wrappers.

1. Operator tab, before cutover, confirm no security run is live (expect no output):

   ```bash
   ls -d ~/radon-weekend/radon-security/.weekend-runner.lock ~/radon-weekend/radon-security-deepsec/.weekend-runner.lock 2>/dev/null
   ```

2. Operator tab, unload and remove the old LaunchAgents:

   ```bash
   for l in com.radon.security-daily com.radon.security-deepsec com.radon.claude-cli-env-drift com.radon.claude-stable-sync; do launchctl bootout gui/$(id -u)/$l 2>/dev/null; rm -f ~/Library/LaunchAgents/$l.plist; done
   ```

3. Operator tab, confirm the ruleset that binds the bot:

   ```bash
   gh api repos/joemccann/radon/rulesets --jq '.[] | [.name,.enforcement] | @tsv'   # expect main-review active
   ```

4. Verify gateway readiness using step 6. Install the approved Claude Security
   plugin for the bot through the managed client, retaining the existing
   plugin workflow and tool permissions. Provider login and subscription-token
   creation do not run in the bot account. DeepSec's pinned SDK receives only
   the temporary loopback adapter credential during process/revalidate.

5. Bot shell, the reports deploy key:

   ```bash
   ssh-keygen -t ed25519 -N '' -C radon-runner-reports -f ~/.radon-runner-reports-key && chmod 600 ~/.radon-runner-reports-key
   ```

   Operator tab, register it as a write key on the private repository:

   ```bash
   sudo -u _radonbot cat /Users/_radonbot/.radon-runner-reports-key.pub | gh repo deploy-key add - -R joemccann/radon-security-reports --allow-write -t radon-runner-bot
   ```

Steps 6 to 8 write into the bot's home only as the bot: your account reads your files and pipes them to a `tar` that runs as `_radonbot`, so nothing runs as root on a path the bot can write. `B` is the bot's state directory.

6. Operator tab, the state directories, then migrate the private scratch and deliver records:

   ```bash
   B=/Users/_radonbot/radon-runner/state; bot() { sudo -u _radonbot "$@"; }
   bot /bin/mkdir -p -m 700 $B/security/scratch $B/security/.security-deliver $B/security-deepsec/scratch $B/security-deepsec/.security-deepsec-deliver
   tar -C ~/radon-weekend/.security-nightly-scratch -cf - --exclude .github-known-hosts . | bot tar -C $B/security/scratch -xf -
   tar -C ~/radon-weekend/.security-deliver -cf - . | bot tar -C $B/security/.security-deliver -xf -
   tar -C ~/radon-weekend/.security-deepsec-scratch -cf - --exclude .github-known-hosts . | bot tar -C $B/security-deepsec/scratch -xf -
   tar -C ~/radon-weekend/.security-deepsec-deliver -cf - . | bot tar -C $B/security-deepsec/.security-deepsec-deliver -xf -
   ```

7. Operator tab, the held P0/P1 branches (never via `/tmp`):

   ```bash
   for l in security security-deepsec; do git --git-dir=$HOME/radon-weekend/.gitdirs-agent/$l.git bundle create - --branches | bot /bin/sh -c 'umask 077; cat > "$1"' sh $B/$l/held.bundle; done
   ```

8. Operator tab, DeepSec's workspace sources and incremental state, with its recorded `rootPath` re-pointed at the runner clone:

   ```bash
   bot /bin/mkdir -p -m 700 $B/security-deepsec/keep/.deepsec $B/security-deepsec/keep/data/radon
   tar -C ~/radon-weekend/radon-security-deepsec/.deepsec -cf - package.json package-lock.json pnpm-workspace.yaml deepsec.config.ts generated-matchers.ts AGENTS.md README.md | bot tar -C $B/security-deepsec/keep/.deepsec -xf -
   tar -C ~/radon-weekend/radon-security-deepsec/data/radon -cf - . | bot tar -C $B/security-deepsec/keep/data/radon -xf -
   bot find $B/security-deepsec/keep/data/radon -name '*.json' -exec sed -i '' 's#/Users/joemccann-mac-mini/radon-weekend/radon-security-deepsec#/Users/_radonbot/radon-runner/work/security-deepsec#g' {} +
   ```

9. Bot shell, unbundle the held branches into each loop's private bare repository, then bootstrap DeepSec (rail 8: the operator does this, never a nightly run):

   ```bash
   for l in security security-deepsec; do git init -q --bare ~/radon-runner/state/$l/held.git && git --git-dir=$HOME/radon-runner/state/$l/held.git fetch -q ~/radon-runner/state/$l/held.bundle 'refs/heads/*:refs/heads/*' && rm ~/radon-runner/state/$l/held.bundle; done
   cd ~/radon-runner/state/security-deepsec/keep/.deepsec && npm ci && ./node_modules/.bin/deepsec --version   # expect 2.3.8
   ```

10. Operator tab, after the PR that moved the loops merges, install all seven loops (step 2), then smoke one:

    ```bash
    sudo launchctl kickstart system/com.radon.runner.security && sudo -u _radonbot tail -f /Users/_radonbot/radon-runner/logs/security/$(date +%F).log
    ```

11. After three green nights, operator tab, retire the old deploy key and the wrapper state:

    ```bash
    gh repo deploy-key list -R joemccann/radon-security-reports   # note the old key's id
    gh repo deploy-key delete <old-key-id> -R joemccann/radon-security-reports && rm ~/radon-weekend/.security-reports-deploy-key ~/radon-weekend/.security-reports-deploy-key.pub && rm -rf ~/radon-weekend/radon-security ~/radon-weekend/radon-security-deepsec ~/radon-weekend/radon-security-deepsec.pre-provision-20260915125818 ~/radon-weekend/.gitdirs/security*.git ~/radon-weekend/.gitdirs-agent/security*.git ~/radon-weekend/.security-nightly-scratch ~/radon-weekend/.security-deepsec-scratch ~/radon-weekend/.security-deliver ~/radon-weekend/.security-deepsec-deliver ~/radon-weekend/.security-reports ~/radon-weekend/venv-security ~/radon-weekend/venv-security-deepsec ~/radon-weekend/.runner-state/radon-security*
    ```

### 8c. Performance loop production probe (operator, bot shell, VPS)

The `performance` loop consumes one read-only snapshot from a separate disabled-login account, `_radonprobe`. Only that account owns the forced-command SSH key and pinned host keys. Its root-installed LaunchDaemon runs at **21:55 in the Mac's local timezone**, five minutes before the performance loop. Collection has a 180-second deadline and a 1,000,000-byte ceiling. No collection occurs at install, load or boot. For manual runs, an administrator starts the fixed helper first.

The root-owned installer copies `PERF_SSH_TARGET` from the installed `performance.env` into `/private/var/db/radon-perf-probe/config.json`. The checked-in target is `radon@100.112.32.16`, the app host on the operator tailnet. The private directory is helper-owned mode 700; the key and known_hosts are mode 600. The helper-owned public directory is mode 750 and contains only the atomic mode-640 `prod-probe.json`; `_radonprobe_read` grants read access to the helper and `_radonbot` only. The config is root-owned mode 640 with that group. Unrelated local accounts cannot read it. `_radonbot` can read that snapshot and cannot write it or traverse the private directory. No sudo rule, root command, arbitrary path or command argument is provided to the agent.

**Tailnet prerequisite after the firewall cutover:** public app SSH is closed. Older installed loop configurations may still use the public address. Before restoring those installations, an administrator must change `PERF_SSH_TARGET` to `radon@100.112.32.16` in the reviewed loop configuration, reinstall it so the helper config is refreshed, and pin the host key under that exact tailnet address. Verify that the Mac has an operator-authorized tailnet identity; the ephemeral `tag:ci` deployment identity is separate. Preserve the helper account and forced-command restrictions. Do not reopen public SSH or restore a bot-readable credential. [Network trust](operations.md#network-trust-ops-plane-step-2) owns access and recovery.

Provisioning also prepares `/private/var/log/radon-perf-probe.log` as a single regular mode-600 file owned by the helper and its read group. launchd opens this file as the helper before starting Python. Unsafe existing log objects are refused; a valid existing log retains its bytes and inode. Schema-1 snapshots accept the canonical producer's nonempty host metrics object and its hostname fallback. Before a snapshot is published or returned, the helper keeps only these top-level keys: `schema`, `taken_at`, `host`, `errors`, `section_ms`, `io_net`, `disk_usage`, `units`, `journal`, `http`, and `tls`. Any other key is removed. The helper log records the dropped count and identifier key names, never their values. Names that are not short identifiers are counted as `<redacted>`.

The pre-run hook invokes the root-installed helper's `read` mode. It validates ownership, modes, regular files, schema 1, the key allowlist, timestamp and size before copying the snapshot into `state/performance/prod-probe.json`. Snapshots older than 15 minutes or more than 60 seconds in the future are unavailable. A missing helper, missing credentials, host-key mismatch or stale snapshot records an error and leaves the local performance phase usable. There is no SSH fallback in the bot process. Existing agents can still alter their own copied evidence; this handoff protects credentials and the authoritative snapshot, not the integrity of a same-uid agent's workspace.

Administrator rollout, from a reviewed operator-owned checkout with no active runner/native-update/helper jobs:

1. Install the reviewed source using the ordinary trusted-source path. It creates `_radonprobe` with a disabled shell and a distinct non-root uid, installs the program and schedule, and prepares protected directories. Existing helper credentials survive subsequent installer/self-refresh runs. The update service and helper share the root-owned maintenance gate.

   ```bash
   sudo /bin/bash /path/to/reviewed/clone/scripts/runner/install.sh performance
   sudo dscl . -read /Users/_radonprobe UniqueID UserShell NFSHomeDirectory
   sudo dscl . -read /Groups/_radonprobe_read GroupMembership PrimaryGroupID
   sudo launchctl print system/com.radon.runner-perf-probe
   ```

2. Generate a **new** key as the helper; do not copy the bot's existing private key. Run once. `ssh-keygen` must refuse an existing key rather than overwrite it. Capture host keys in a temporary helper-owned file, verify their fingerprint out of band against the server's host key, then rename the candidate. The SSH commands use that same `$target` address and enforce `StrictHostKeyChecking=yes`.

   ```bash
   sudo -u _radonprobe /bin/sh -c 'key=/private/var/db/radon-perf-probe/private/id_ed25519; test ! -e "$key" && test ! -L "$key" && exec /usr/bin/ssh-keygen -t ed25519 -N "" -C radon-perf-snapshot -f "$key"'
   target=$(sed -n 's/^PERF_SSH_TARGET=//p' /usr/local/radon-runner/loops/performance.env | tail -n 1 | tr -d "\"'")
   test "$target" = radon@100.112.32.16 || { echo "Migrate the loop target before provisioning" >&2; exit 1; }
   host=${target#*@}
   sudo -u _radonprobe /bin/sh -c 'umask 077; candidate=$(mktemp /private/var/db/radon-perf-probe/private/.known-hosts.XXXXXX); ssh-keyscan -t ed25519 "$1" > "$candidate" && ssh-keygen -lf "$candidate"; printf "candidate=%s\n" "$candidate"' sh "$host"
   ```

   Compare the candidate fingerprint with `/etc/ssh/ssh_host_ed25519_key.pub` through the existing administrator channel. Only after a match, rename that exact candidate to `private/known_hosts` as `_radonprobe`, and verify both input files are mode 600. Do not use trust on first use or replace a pin automatically.

3. On the server, add the helper's **public** key under the existing restricted account using the same forced command. Keep the old bot public key until successful handover. The server runs only `cloud/scripts/perf_probe.py` as `radon`, never root, with no shell, pty or forwarding:

   ```
   restrict,command="/usr/bin/python3 -I /home/radon/radon/cloud/scripts/perf_probe.py" ssh-ed25519 AAAA... radon-perf-snapshot
   ```

4. Start the fixed helper, wait for exit, then verify the authoritative snapshot and access boundaries. Never start a second collection while the first is active.

   ```bash
   sudo launchctl kickstart system/com.radon.runner-perf-probe
   sudo launchctl print system/com.radon.runner-perf-probe
   sudo -u _radonbot /opt/homebrew/bin/python3.13 -I /usr/local/radon-runner/perf_probe_helper.py read
   sudo -u _radonbot test ! -r /private/var/db/radon-perf-probe/private/id_ed25519
   sudo -u _radonbot test ! -w /private/var/db/radon-perf-probe/public
   sudo -u nobody test ! -r /private/var/db/radon-perf-probe/public/prod-probe.json
   ```

5. After successful JSON/freshness/permission verification, revoke only the old bot probe public-key line on the server, then remove only its corresponding bot private/public key and old probe known_hosts files as `_radonbot`. Verify the old key is rejected through the owner channel before deleting its local copy. Do not remove unrelated authorized keys. The isolation change is not complete until the old credential is revoked and retired.

- **Symptom:** the copied probe reports unavailable or stale.
- **Prerequisites:** an operator-authorized tailnet device and installed tailnet target, an administrator for account creation, the reviewed runner install, a verified pinned host key and the new restricted public key registration. The current operator session requires an interactive sudo password; merging/deploying does not finish Mac credential handover.
- **Blast radius:** one read-only snapshot on the existing schedule; the helper cannot execute an arbitrary command or write to the agent workspace.
- **Safe diagnosis:** inspect the helper's last exit status and `/var/log/radon-perf-probe.log`; compare fingerprints through the administrator channel. The log contains an availability class and, when extra keys were removed, the dropped-key count plus identifier names. It never contains key values.
- **Stop conditions:** a public app target or unavailable tailnet path, wrong ownership, writable/symlinked paths, matching uids, administrator-group membership, active helper/runner during install, mismatched fingerprint or unknown key ownership. Do not install a fallback credential in the bot home.
- **Verification:** the bot can read fresh JSON but cannot read the private inputs or write the public directory; the original credential is revoked after handover.
- **Rollback:** unload `com.radon.runner-perf-probe` and revoke the new restricted public key. Keep the bot on the snapshot consumer so it records unavailable; do not restore a bot-readable key. A reviewed prior helper version can be installed while idle without changing its private inputs.
- **Escalation:** leave the local loop usable and record unavailable evidence until the administrator completes or repairs provisioning.

### 9. Smoke test (bot shell)

```bash
/bin/bash /usr/local/radon-runner/run_loop.sh documentation
```

It runs in the foreground for up to the loop configuration's `TIMEOUT_SECS`. Do not stop it to read the log. Follow it from your own tab instead (the log directory is mode 700, so your account needs `sudo -u`):

```bash
sudo -u _radonbot tail -f /Users/_radonbot/radon-runner/logs/documentation/$(date +%F).log
```

Verify a completed `RESULT:` line in the log and a `radon documentation: done via <agent>` Pushover. `RESULT: no PR - <reason>` is healthy when the audit found no substantive correction. When a change is published, verify its draft PR on `documentation/<date>` has author `radon-runner-bot` (`gh pr list --author radon-runner-bot --state all`) and that every applicable check on its exact head is green. A runner `done` page alone reports the agent's exit status; it does not prove CI passed.

If completion evidence is missing or the run fails, preserve the log and
inspect the troubleshooting checks below before another fire. Do not
reinstall the runner or upgrade its CLIs while a run is active. For a bad
installation, keep the affected job stopped and have the operator reinstall
a reviewed known-good runner/config from a retained checkout after the run
exits; prompts still come from `main` on each fire. Escalate unresolved
failures with the loop name and sanitized log, never credentials.

### 10. Clean up

`exit` the bot shell.

## Operate

| Task | Command |
|---|---|
| Read last night's log | `sudo -u _radonbot tail -60 /Users/_radonbot/radon-runner/logs/documentation/$(date +%F).log` |
| launchd's own output (start failures) | `sudo -u _radonbot tail /Users/_radonbot/radon-runner/launchd-documentation.log` |
| Is the job loaded, when did it last run | `sudo launchctl print system/com.radon.runner.documentation \| grep -E 'state\|last exit'` |
| Run a loop now | `sudo launchctl kickstart system/com.radon.runner.documentation` |
| Change `run_loop.sh` or a loop `.env` | after one installation of the cooperative runner, `com.radon.runner-update` refreshes deployed, CI-green main every six hours while all loops are idle. Prompts still refresh every night. See [unattended releases](unattended-releases.md) |
| Rotate the GitHub token (before its expiry) | step 7.5 to 7.6, then step 8 |
| Release a completed P1 fix | the loop exports immutable source to private CI; the operator coordinator owns independent review, public PR, merge and host sync. No terminal-written marker. P0 requires incident coordination |
| Read a security loop's private report | the Pushover page's "Open private report" link (`joemccann/radon-security-reports`) |
| After a reboot | Verify `com.radon.model-gateway-tunnel` and `com.radon.model-gateway-updater` in system launchd, then step 6 |
| Provider needs renewed consent | Follow the gateway reauthorization alert; do not create bot-side model grants |

## Troubleshooting

An uncertain lock requires operator inspection of its recorded PID and process start time before removing it. Do not remove a live owner's lock or clone. A crashed stale-lock reclaimer can leave a `reaping` directory; verify that no run owns the workspace before clearing the orphan. The exit trap removes only the current runner's own lock. Install this behavior from the reviewed, merged checkout with `sudo scripts/runner/install.sh reliability` after the active run exits; changing source alone does not update the root-owned installed runner.

| Symptom | Cause | Fix |
|---|---|---|
| `sudo: a password is required` during initial root-runner upgrade | the old install has no unattended root updater | one legitimate macOS administrator authorization installs the reviewed updater; later CI-green runner refreshes need no terminal command. Never grant a bot-writable script root authority |
| `--check` passes but paths show your home | You ran it in your own tab | Rerun it in the bot shell |
| GitHub signup: "email ... already associated with an account" | One email per GitHub account | Use a different address |
| Signup form shows `joemccann` | Browser autofill | Clear the username and password fields, paste the bot's |
| PR author is `joemccann`, not the bot | `GH_TOKEN` holds your token | Replace it with the bot's (step 8) |
| `no timeout or gtimeout in /opt/homebrew/bin:/usr/bin:/bin` | coreutils missing | `brew install coreutils` |
| First `uv pip install` fails on `file:./vendor/fastembed-0.8.0-py3-none-any.whl` | `vendor/` is gitignored, and the security pre-run `git clean` deletes it | `run_loop.sh` writes the wheel after `PRE_RUN` and before the agent. The installed copy refreshes from CI-green main |

## NVIDIA rate limits

NVIDIA inference is paced inside the model gateway. The existing limiter
retains its configured RPM, bounded state locking, Retry-After cooldowns and
persistent authorization/429 trip deadlines. Gateway cutover captures known
legacy state before old containers stop, merges stronger deadlines into the
persistent account record and records each imported snapshot so later
restarts cannot revive an already-cleared failure streak.

Coding agents do not launch the legacy fx loopback proxy or carry its bearer
key. A refused provider attempt moves through compatible gateway rungs;
account pacing and cooldowns are never reset to force another attempt.

## Loop config

`scripts/runner/loops/<loop>.env` sets `BRANCH_PREFIX`, `PROMPT`, `AGENTS` (agent names in order; `fx:<provider>` for fx, `claude:<model>` for claude, which always runs with `--effort medium`), `TIMEOUT_SECS`, `SCHEDULE_HOUR` and `SCHEDULE_MINUTE`. Adding a loop is one `.env`, one prompt and one `install.sh <loop>`. `install.sh` installs every `loops/*.env` on any invocation, named or not, because `run_loop.sh` reads the installed env: the 2026-10-19 `install.sh performance` refreshed the harness but left six envs at a shape without `DEADMAN_LABEL`, so four loops ran a marker-posting harness with nothing to post to. Legacy `agy` and `fx:<provider>` config names now use the managed Codex native adapter; their old local binaries and provider keys are unused. Claude and Grok retain their native protocols. Provider capability and explicit model-identity checks remain service-owned.

Optional knobs (empty means off; the security loops use them):

| Knob | Effect |
|---|---|
| `AGENT_ENV` | `NAME=value` words exported for the resolver, the hooks and the agent |
| `AGENT_UNSET` | Names unset before anything runs. Each one found set is logged as `IGNORING: <NAME>`, never with its value |
| `ALLOWED_AGENTS` | A rung whose agent is not listed is `REFUSED` (exit 2, one page) before cloning |
| `AGENTS_RESOLVER` | A root-owned script under `/usr/local/radon-runner`, run as `/opt/homebrew/bin/python3.13 -I <script> --rungs`; its stdout replaces `AGENTS`. Empty or failed output keeps `AGENTS` and is logged |
| `KEEP_PATHS` | Relative paths moved into `state/<loop>/keep/` before the re-clone and back after it. Whatever is in `keep/` is always restored, so a crash leaves nothing behind and an operator can seed it |
| `PHASES` | `name:secs ...`: one agent session per phase with its own timeout and a header carrying `Phase:` and `State:`. Every phase runs whatever the earlier rc; each pages `radon <loop> <phase>`. The runner exits 75 if any phase is not OK and 2 if one was refused |
| `PRE_RUN` / `POST_RUN` | Root-owned hooks under `/usr/local/radon-runner`, run around each phase with `LOOP`, `PHASE`, `WORK`, `LOOP_STATE`, `BRANCH` (and, after it, `PHASE_RC`, `PHASE_LOG`, `PHASE_START_MARK`, `PHASE_AUDITED_SHA`), the runner's `PATH`, and a 900s cap. A non-zero `PRE_RUN` skips the agent (`REFUSED`); its `refused=` reason names a class and never a path (the file goes to the runner log). The public issue comment does not include that reason. `POST_RUN` prints `status=` (`OK...` means success; the last one printed wins), `pr_url=`, `report_url=` and, after it has posted the phase line, `marker_posted=1`. A `POST_RUN` that prints no `status=` (timed out, crashed, missing) fails the phase. With `PRE_RUN` set, the header tells the agent to branch from `HEAD` (the base the hook pinned), not `origin/main` |
| `GH_GUARD=1` | Puts `/usr/local/radon-runner/guard/<loop>/gh` first on the agent's `PATH`: every `pr` / `api` / `issue` / `alias` call goes through `nightly_pr_guard.py` |
| `DEADMAN_LABEL` / `DEADMAN_TITLE` | When set, the harness posts the [phase status line](#phase-status-line) on the open issue with that label, creating it with the title if absent. Empty means no harness issue comment |

Every agent gets `RADON_RUNNER_LOOP_STATE` (the loop's state directory), `RADON_RUNNER_PIDFILE` (list a detached job's pid there and the runner reaps it), `RADON_RUNNER_AGENT` and `RADON_RUNNER_MODEL`. After each agent the runner kills its process group, then every bot process whose cwd is in the loop's clone or state and every declared pid that started during the phase, except one that is, or descends from, another loop's live runner. On SIGTERM, including one that arrives while a hook runs, it does the same, pages `KILLED`, gives `POST_RUN` ten seconds, posts the phase line if the hook did not, and exits 143. The LaunchDaemon sets `DISABLE_AUTOUPDATER=1`, `TMPDIR` (the bot's `radon-runner/tmp/`) and `ExitTimeOut` 60. PR lookup ignores fork PRs.

## Phase status line

The harness (`scripts/runner/phase_status.sh`, called from `run_loop.sh` and the security post-run hook) posts one comment per phase from the real process exit. The agent does not write it. `finish_phase` posts it, and the SIGTERM trap posts it again when the hook did not print `marker_posted=1`, so a crashed or killed phase still leaves a code. A loop whose installed env has no `DEADMAN_LABEL` logs `phase marker not posted: no DEADMAN_LABEL` instead of returning silently.

```
<!-- radon-phase loop=testing phase=audit exit=0 audited-through=<sha> ts=2026-10-18T15:47:00Z -->
radon-phase loop=testing phase=audit exit=0 audited-through=<sha> ts=2026-10-18T15:47:00Z
audited-through: <sha>
```

Fields, in order: `loop`, `phase`, `exit`, optional `audited-through`, `ts` (`YYYY-MM-DDTHH:MM:SSZ`). Tokens are `[a-z0-9][a-z0-9-]*`.

| Exit | Meaning |
|---|---|
| 0 | OK |
| 1 | FAILED (agent or post-run hook failed, the hook gave no status, or the phase was signalled; the runner process still exits 143) |
| 2 | REFUSED (a guard stopped the phase before the agent ran) |
| 70 | GROUND TRUTH FAILED |
| 75 | INCOMPLETE (resumable; a zero process exit whose hook status is `INCOMPLETE` or `TRUNCATED`) |
| 124 | TIMEOUT (a process exit 124 stays 124 even when the deliver hook rewrites the status word to `INCOMPLETE`) |

`audited-through` is present only for `phase=audit` with exit 0. The sha is `git rev-parse HEAD` taken after `PRE_RUN` and before the agent (the pinned green base). The colon form is what `scripts/nightly_audit_context.py` and `scripts/nightly_issue_prune.py` already match. A non-zero exit never carries it, and the audit-context reader ignores a machine line that is not an audit exit 0, so a failed phase cannot advance the cursor. Comments with no machine line stay eligible.

Security and `security-deepsec` now post this public audit comment too. The body is the three lines above and nothing else: no finding text, no PR numbers for held fixes, no paths, no secrets, no refusal class. The class stays in the runner log and the Pushover `status=`. The private report is unchanged.

Before the post-run hook commits a phase report to the private reports repository it runs the report through the canonical scrubber, `scripts/credential_redaction.py`, from the root-owned `$PREFIX/lib` copy with `python3 -I` (it is in `LIB_FILES`), and then through the hook's own `sed` pass. The canonical pass goes first so YAML block-scalar bodies are consumed before any line-oriented rule sees them and so compound credential names share one key alphabet with the rest of the repo; the `sed` pass only adds the key classes the module does not carry (`credential`, a bare `-key` / `_key` suffix). Together they keep findings, routes and `file:line` but remove secret literals: credential assignments, block-scalar values, cookie and bearer headers, known provider key prefixes, JWTs, and PEM / OpenSSH / PGP private-key blocks (complete or cut off). A failed scrub publishes nothing. A new secret class belongs in `scripts/credential_redaction.py`, which `scripts/nightly_issue_format.py` and this hook both consume.

The deliver phase's "ready to merge" line is never the agent's assertion. The post-run hook re-verifies every claimed PR live before the operator sees it: the URL must be a `pull/<number>` URL on the configured host and `owner/repo` (the number, not the URL, is what goes to `gh`, so `-R` stays authoritative), the PR must be OPEN, same-repo and on the loop's branch prefix, its `headRefOid` must still equal the head SHA the deliver record was marked green at, and its `statusCheckRollup` must be non-empty and wholly `SUCCESS` / `SKIPPED` / `NEUTRAL` ("no checks reported" is pending, not green). A claim of zero PRs is accepted only when the open-PR listing ran, returned fewer than 100 rows, and contained no same-repo PR on the loop's branch prefix. A failed query, a timeout, or a full page is not absence. Anything else is `INCOMPLETE: unverified-pr-url` or `INCOMPLETE: unverified-zero-pr-claim`, the phase exits 75, and the next fire resumes the same branch and PR.

Loops without `PHASES` (reliability, ci-performance, documentation, testing) run audit, remediate and deliver in one process. They post one comment, `phase=audit`, with that process's exit. They do not emit three codes. `performance` posts its real phase name (`perf`).

`scripts/nightly_phase_status.py` parses the line and falls back to the banner (`Healthy` / `Fixed` / `Needs you` / `Incomplete`) and the old `**PHASE** STAMP **status**` wording. There is no separate nightly-runners report script in this repo. `joemccann/radon-factory` was not found; the runner lives here.

`install.sh` copies `phase_status.sh` next to `run_loop.sh` and `nightly_phase_status.py` into `$PREFIX/lib`. Prompts update when the night re-clones `main`. The installed harness does not, until `install.sh` runs again (the scheduled root updater, or the operator command in the PR). The updater's store is shallow, so it fetches `main` with a forced refspec (`+refs/heads/main:refs/heads/main`); the unforced form was rejected as a non-fast-forward on every run after the first, and `/var/log/radon-runner-update.log` read `git failed (exit 1)` until 2026-10-25.

## Migration from the per-loop wrappers

| Loop | State |
|---|---|
| documentation | Cut over: runs at 03:00 on branch `documentation/<date>` as `_radonbot`, dead-man label `documentation-nightly` (#202); the prompt posts the rolling-issue comment every night (no `POST_RUN` hook); the old wrapper, setup script, LaunchAgent and skill are deleted |
| performance | New loop: runs at 22:00 (01:00 ET) on branch `performance/<date>`, one 4h phase, dead-man label `performance-nightly`; pre-run hook pins the green base, pulls the production probe (8c), and prunes superseded comments once earlier PRs merge |
| ci-performance | Cut over: runs at 00:20 on branch `ci-performance/<date>`; `scripts/ci_performance_nightly.sh` and its LaunchAgent are deleted |
| testing | Cut over: runs at 00:10 on branch `testing/<date>` (4h budget, for the closing three rounds of the full gates); `scripts/testing_weekend.sh`, its setup script, LaunchAgent, skill and portable prompts are deleted. It gets no scoped Turso or Unusual Whales key: the suites need none |
| reliability | Cut over: runs at 00:00 on branch `reliability/<date>`; `scripts/reliability_weekend.sh`, its setup script, LaunchAgent, skill and portable prompts are deleted. Full pytest, `cloud/tests` and Vitest run in PR CI; the night runs focused suites and the drills |
| security | Cut over: runs at 00:40 on branch `security/<date>`, claude only, three phases (audit 2h, remediate 6h, deliver 3h), same dead-man label `security-nightly`; `scripts/security_nightly.sh`, its setup script, LaunchAgent, skill and the shared ladder shim are deleted, and the rails live in `scripts/runner/hooks/security_{pre,post}.sh` |
| security-deepsec | Cut over: runs at 00:50 on branch `security-deepsec/<date>`, audit 8h, dead-man label `security-deepsec`, `.deepsec/` and `data/radon/` kept across re-clones; `scripts/security_deepsec_nightly.sh`, its LaunchAgent and skill are deleted |

DeepSec operator-only carry-over is re-verified every phase. `scripts/nightly_deepsec_queue.py reverify` reads `$RADON_RUNNER_LOOP_STATE/scratch/last-audited.json` plus the committed ledger `docs/security-deepsec-closed.json` (sanitized IDs and public evidence only) and assigns `open`, `closed`, or `unverifiable`. Closed IDs stay in the private `closed_queue` and are not listed in the PR Next section again; they appear once in that night's deliver report as closed tonight. `DS-2026-09-20-03` is seeded closed (PR #689, `e5c4e627`, `cloud/scripts/setup-vps.sh` L39-47). An item reopens only with new evidence. No VPS or host check is required.

Cutover per loop: compare three nights of shadow PRs, set `BRANCH_PREFIX` to the old prefix, unload the old LaunchAgent, then delete the old wrapper, setup script, helpers and their tests. The security loops cut over directly with no shadow nights: two runners would both post the dead-man and deliver on the same branch. The CLI env-drift check and the stable Claude copy went with them (the pre-run hook runs the drift check; the bot has its own claude).

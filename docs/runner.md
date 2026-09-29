# Nightly agent runner

One loop run is what the operator would do by hand: open an agent CLI in a fresh clone of `main`, paste the loop's prompt, walk away. `scripts/runner/run_loop.sh <loop>` does exactly that:

1. Take the loop's lock (a live run makes the new fire page `skipped` and exit 0; a lock whose pid is gone or now belongs to a different process is reclaimed).
2. Delete the previous clone and `git clone` `main` again, so nothing an agent left behind (files, `.git` hooks, config) survives to the next night.
3. Prepend a header (date, branch `<BRANCH_PREFIX>/<date>`, time budget) to `.claude/runner-prompts/<loop>.md` from that clone.
4. Run the first agent in `AGENTS` under `timeout`. The next agent runs only if one exits non-zero; a timeout ends the night. Anything the agent left running is killed with its process group.
5. Look up the PR for the branch and send one Pushover message with the agent's `RESULT:` line and the PR link.

The agent does the whole job in one session: audit, fix, push the branch, open a draft PR, watch CI. The prompt owns what the loop does; the runner owns nothing else.

The two security loops (`security`, `security-deepsec`) also use the optional knobs in [Loop config](#loop-config): one session per phase (audit, remediate, deliver), a claude-only ladder from a resolver, a root-owned `gh` guard, and root-owned pre- and post-run hooks that port the old wrapper's rails (credential and billing-reroute refusals, the green-base checkout, the sanitized dead-man, the private report). [docs/operations.md](operations.md#background-services) has the operator contract.

## Safety boundary

Safety is where the runner runs, not what the script checks:

| Control | Enforced by |
|---|---|
| Agent cannot read operator files, ssh keys, `~/.radon`, Keychain | Dedicated macOS user `_radonbot`, standard (non-admin), hidden |
| Agent cannot edit the runner | `run_loop.sh`, `loops/*.env`, the hooks, their `lib/` helpers, the `gh` guard and `gitconfig` installed root-owned in `/usr/local/radon-runner` |
| Agent cannot plant a binary or git config the runner runs | The LaunchDaemon's `PATH` is `/opt/homebrew/bin:/usr/bin:/bin`; the bot's CLI directories (`~/.local/bin`, `~/.grok/bin`, `~/.bun/bin`) are prepended only for the agent. Every git the runner, the hooks and the agent run reads `/usr/local/radon-runner/gitconfig` (`GIT_CONFIG_GLOBAL`, `GIT_CONFIG_NOSYSTEM=1`), never `~/.gitconfig`, and the pre-run hook rebuilds the clone's `.git/config` before every phase |
| No production credential in the clone | The clone gets no `.env`. `~/.radon-runner.env` may hold only `GH_TOKEN`, `PUSHOVER_USER` and `PUSHOVER_TOKEN`. The agent runs as `_radonbot`, so it can read that file. The runner unsets the Pushover keys in the agent process; `GH_TOKEN` stays so the agent can push. Do not put a production credential or an admin token in that file or anywhere in the bot's home |
| Agent cannot merge or push `main` | A separate GitHub machine account with the Write role on this repo only, used through its classic `repo` token, plus the `main-review` repository ruleset on the default branch: 1 approving review, approval of the most recent push required, stale approvals dismissed on push. Its only bypass actor is the Repository admin role in pull-request mode, so an admin merges through a PR and never pushes `main` directly; the bot account is not exempt. The 27 required status checks stay in the classic branch protection on `main`, not in the ruleset. A token of the operator's own account would not do: it acts as the admin and bypasses the ruleset |

The ruleset is applied at cutover. Before then every nightly loop still runs as the operator, whose admin token bypasses it anyway, and `scripts/codemap_nightly.sh` merges its own PR with that token.

## Set up a new Mac

The whole install, start to finish, on a Mac that has never run Radon. Allow about 45 minutes. Everything runs in Terminal as the operator (an admin account); a step says when to switch to the bot shell. You never need to log in to the bot's desktop.

Where things live when you are done:

| Path | Owner | Holds |
|---|---|---|
| `/usr/local/radon-runner/run_loop.sh`, `loops/<loop>.env` | root | The runner and each loop's config. The agent cannot edit them |
| `/usr/local/radon-runner/hooks/`, `lib/`, `guard/<loop>/gh` | root | The security loops' pre/post-run hooks, the helpers they and the `gh` guard run (copied from `scripts/`), and the guard shim |
| `/Library/LaunchDaemons/com.radon.runner.<loop>.plist` | root | The nightly schedule, run as `_radonbot` |
| `/Users/_radonbot/.radon-runner.env` | bot, 600 | `GH_TOKEN`, `PUSHOVER_USER`, `PUSHOVER_TOKEN`. The agent can read it (it runs as the bot); the runner unsets the Pushover keys in the agent process. Never a production or admin credential |
| `/Users/_radonbot/.radon/agent-cli/env` | bot, 600 | `NVIDIA_API_KEY`, `CEREBRAS_API_KEY` for the fx agents |
| `/Users/_radonbot/radon-runner/work/<loop>` | bot | Tonight's clone, deleted and re-cloned every run |
| `/Users/_radonbot/radon-runner/logs/<loop>/<date>.log` | bot, 700 | The run log, kept 14 days |
| `/Users/_radonbot/radon-runner/state/<loop>` | bot, 700 | Per-loop state the runner never deletes: the security loops' private `scratch/`, deliver record, `held.git` (unreleased P0/P1 branches) and `keep/` |
| `/Users/_radonbot/.radon-runner-reports-key` | bot, 600 | Write deploy key for the private `joemccann/radon-security-reports`, used by the post-run hook. The agent runs as the same user and can read it, as it could read the old wrapper's key; a deploy key reaches only that one repository |
| Operator login keychain: `github-radon-runner-bot`, `github-radon-runner-bot-token` | operator | The bot's GitHub password and token, for rotation |

### 1. Prerequisites (operator)

```bash
# Homebrew first if the Mac has none: https://brew.sh
brew install git gh coreutils python@3.13 uv bun node
brew install --cask codex
# gitleaks 8.30.1 for the security loops (docs/security-approved-tools.md), checksum-pinned, root-owned
curl -fsSLo /tmp/gitleaks.tgz https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_darwin_arm64.tar.gz \
  && echo "b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5  /tmp/gitleaks.tgz" | shasum -a 256 -c \
  && tar -xzf /tmp/gitleaks.tgz -C /tmp gitleaks && sudo install -o root -g wheel -m 755 /tmp/gitleaks /usr/local/bin/gitleaks \
  && sudo ln -sf /usr/local/bin/gitleaks /opt/homebrew/bin/gitleaks && rm /tmp/gitleaks.tgz /tmp/gitleaks
/opt/homebrew/bin/gitleaks version   # 8.30.1
sudo pmset -a sleep 0 displaysleep 10     # the Mac must stay awake at night
```

`coreutils` provides `timeout`; the runner exits 69 without it. `node` runs Vitest for the testing loop (bun installs the packages, `npx vitest run` runs them); without it that loop leaves Vitest to PR CI. `codex` is shared from Homebrew; the other agent CLIs are installed per user in step 4.

### 2. Install the runner (operator)

```bash
RI="$(mktemp -d)" && /usr/bin/git clone https://github.com/joemccann/radon.git "$RI"
/bin/bash "$RI/scripts/runner/install.sh" --print-plist documentation   # review
sudo /bin/bash "$RI/scripts/runner/install.sh" documentation ci-performance testing reliability security security-deepsec
/bin/rm -rf "$RI"
```

Always clone into a fresh `mktemp -d` directory, never a fixed `/tmp` path another account could create first. `install.sh` refuses (exit 77) when the clone, or any directory above it, is owned by someone other than root or you, or is group- or world-writable.

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

Install and sign in to every agent the loop's `AGENTS` names (`grok codex agy fx:nvidia fx:cerebras` for documentation, ci-performance, testing and reliability; `claude` for the two security loops, step 8b). The sign-ins print a URL or a device code: open it in your own browser, signed in to the account that owns the subscription.

| Agent | Install (bot shell) | Sign in (bot shell) | Binary the runner uses |
|---|---|---|---|
| grok | `curl -fsSL https://x.ai/cli/install.sh \| bash` | `~/.grok/bin/grok`, approve the device code | `~/.grok/bin/grok` |
| codex | shared, step 1 | `/opt/homebrew/bin/codex login` (or `codex login --device-auth`); DeepSec's reviewer uses this login too | `/opt/homebrew/bin/codex` |
| claude | step 8b | step 8b | `~/.local/bin/claude` |
| agy | `curl -fsSL https://antigravity.google/cli/install.sh \| bash` | step 5 (needs a keychain first) | `~/.local/bin/agy` |
| fx | `curl -fsSL https://fx.sh/setup.sh \| bash` | none; uses the keys from step 6 | `~/.local/bin/fx` |

### 5. Keychain, then agy (bot shell)

agy stores its token in the macOS keychain, and a user who never logged in to the desktop has none: agy's first run then shows a "Keychain Not Found ... antigravity" dialog. Click Cancel and create the keychain from the shell, using the bot's password from step 2:

```bash
read -rs "KP?keychain password: "; K=~/Library/Keychains/login.keychain-db
security create-keychain -p "$KP" $K && security list-keychains -d user -s $K \
  && security default-keychain -d user -s $K && security login-keychain -d user -s $K \
  && security set-keychain-settings $K && unset KP
~/.local/bin/agy
```

agy prints a Google URL and waits 60 seconds for the pasted code. Then check it:

```bash
~/.local/bin/agy -p="reply ok" --output-format text
```

A reboot locks this keychain, which also holds the bot's claude.ai session (step 8b.4). After one, run `security unlock-keychain ~/Library/Keychains/login.keychain-db` in the bot shell, then check `~/.local/bin/claude auth status`. Until then the agy rung fails and those loops move to the next agent, and both security loops (claude only) fail every phase.

### 6. Provider keys for fx (operator, then bot shell)

In your own tab, copy only the two keys from your agent-cli env into the bot's. `grep` runs as you, so it reads your file; `sudo` only writes the bot's:

```bash
grep -E "^(export )?(NVIDIA_API_KEY|CEREBRAS_API_KEY)=" ~/.radon/agent-cli/env | sudo /bin/sh -c 'umask 077; d=/Users/_radonbot/.radon; install -d -m 700 -o _radonbot -g staff $d $d/agent-cli && cat > $d/agent-cli/env && chown _radonbot:staff $d/agent-cli/env'
```

On a Mac where you have no such file, create `/Users/_radonbot/.radon/agent-cli/env` in the bot shell with `NVIDIA_API_KEY=` and `CEREBRAS_API_KEY=` lines instead. Then, in the bot shell:

```bash
RB="$(mktemp -d)" && /usr/bin/git clone --depth 1 https://github.com/joemccann/radon.git "$RB"
/bin/bash "$RB/scripts/agent_cli_bootstrap.sh"
/bin/bash "$RB/scripts/agent_cli_bootstrap.sh" --check
/bin/rm -rf "$RB"
```

Every line must read `OK` with a `/Users/_radonbot/...` path: codex, grok, nvidia, cerebras, fx. A `MISSING` line names its fix. agy is not in this check; step 5 covers it.

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
5. As the bot, open https://github.com/settings/tokens/new?scopes=repo&description=radon-runner. Keep only `repo` ticked, set Expiration to Custom with a date one year out, then Generate. Copy the `ghp_...` value into your keychain without echoing it:

   ```bash
   read -rs "T?token: "; security add-generic-password -U -s github-radon-runner-bot-token -a radon-runner-bot -w "$T"; unset T
   ```

6. Check the token acts as the bot with only `repo`:

   ```bash
   T=$(security find-generic-password -s github-radon-runner-bot-token -w)
   curl -sI -H "Authorization: token $T" https://api.github.com/user | grep -iE 'x-oauth-scopes|token-expiration'
   curl -s -H "Authorization: token $T" https://api.github.com/user | grep '"login"'; unset T
   ```

   Expect `x-oauth-scopes: repo`, your chosen expiry, and `"login": "radon-runner-bot"`.

### 8. Runner secrets (operator)

Write the token into the bot's secrets file straight from your keychain:

```bash
security find-generic-password -s github-radon-runner-bot-token -w | sudo /bin/sh -c 'umask 077; read -r t; f=/Users/_radonbot/.radon-runner.env; { grep -v "^GH_TOKEN=" "$f"; printf "GH_TOKEN=%s\n" "$t"; } > "$f.new" && chown _radonbot:staff "$f.new" && mv "$f.new" "$f"'
```

Then add Pushover in the bot shell with `nano ~/.radon-runner.env`, setting `PUSHOVER_USER=` and `PUSHOVER_TOKEN=`. Check all three are set without printing them:

```bash
grep -oE '^(GH_TOKEN=ghp_|PUSHOVER_USER=.|PUSHOVER_TOKEN=.)' ~/.radon-runner.env    # 3 lines
```

### 8b. Security loops (operator and bot shell)

The `security` and `security-deepsec` loops need the bot's own Claude Code on the claude.ai subscription, a deploy key for the private reports repository, their night-to-night state and DeepSec's workspace. The first three steps retire the old per-loop LaunchAgents; skip them on a Mac that never ran the wrappers.

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

4. Bot shell, install Claude Code and sign in with the claude.ai subscription account (`/login`; the keychain from step 5 holds the session):

   ```bash
   curl -fsSL https://claude.ai/install.sh | bash && ~/.local/bin/claude
   claude auth status   # loggedIn true, subscription auth (not an API key)
   claude plugin install claude-security@claude-plugins-official --scope user && claude plugin list --json
   claude models && /opt/homebrew/bin/codex login status && gitleaks version   # expect 8.30.1
   ```

5. Bot shell, the reports deploy key:

   ```bash
   ssh-keygen -t ed25519 -N '' -C radon-runner-reports -f ~/.radon-runner-reports-key && chmod 600 ~/.radon-runner-reports-key
   ```

   Operator tab, register it as a write key on the private repository:

   ```bash
   sudo cat /Users/_radonbot/.radon-runner-reports-key.pub | gh repo deploy-key add - -R joemccann/radon-security-reports --allow-write -t radon-runner-bot
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
   bot find $B/security-deepsec/keep/data/radon -name '*.json' -exec sed -i '' 's#/Users/asymmetricholdingsltd./radon-weekend/radon-security-deepsec#/Users/_radonbot/radon-runner/work/security-deepsec#g' {} +
   ```

9. Bot shell, unbundle the held branches into each loop's private bare repository, then bootstrap DeepSec (rail 8: the operator does this, never a nightly run):

   ```bash
   for l in security security-deepsec; do git init -q --bare ~/radon-runner/state/$l/held.git && git --git-dir=$HOME/radon-runner/state/$l/held.git fetch -q ~/radon-runner/state/$l/held.bundle 'refs/heads/*:refs/heads/*' && rm ~/radon-runner/state/$l/held.bundle; done
   cd ~/radon-runner/state/security-deepsec/keep/.deepsec && npm ci && ./node_modules/.bin/deepsec --version   # expect 2.3.8
   ```

10. Operator tab, after the PR that moved the loops merges, install all six loops (step 2), then smoke one:

    ```bash
    sudo launchctl kickstart system/com.radon.runner.security && sudo -u _radonbot tail -f /Users/_radonbot/radon-runner/logs/security/$(date +%F).log
    ```

11. After three green nights, operator tab, retire the old deploy key and the wrapper state:

    ```bash
    gh repo deploy-key list -R joemccann/radon-security-reports   # note the old key's id
    gh repo deploy-key delete <old-key-id> -R joemccann/radon-security-reports && rm ~/radon-weekend/.security-reports-deploy-key ~/radon-weekend/.security-reports-deploy-key.pub && rm -rf ~/radon-weekend/radon-security ~/radon-weekend/radon-security-deepsec ~/radon-weekend/radon-security-deepsec.pre-provision-20260915125818 ~/radon-weekend/.gitdirs/security*.git ~/radon-weekend/.gitdirs-agent/security*.git ~/radon-weekend/.security-nightly-scratch ~/radon-weekend/.security-deepsec-scratch ~/radon-weekend/.security-deliver ~/radon-weekend/.security-deepsec-deliver ~/radon-weekend/.security-reports ~/radon-weekend/venv-security ~/radon-weekend/venv-security-deepsec ~/radon-weekend/.runner-state/radon-security*
    ```

### 9. Smoke test (bot shell)

```bash
/bin/bash /usr/local/radon-runner/run_loop.sh documentation
```

It runs in the foreground for up to `TIMEOUT_SECS` (3 hours for documentation); the 2026-09-28 run took 15 minutes. Do not stop it to read the log. Follow it from your own tab instead (the log directory is mode 700, so your account needs `sudo -u`):

```bash
sudo -u _radonbot tail -f /Users/_radonbot/radon-runner/logs/documentation/$(date +%F).log
```

It passes when the log starts with `starting grok`, a draft PR on `documentation/<date>` appears with author `radon-runner-bot` (`gh pr list --author radon-runner-bot --state all`), and a `radon documentation: done via <agent>` Pushover arrives.

### 10. Clean up

`exit` the bot shell. Step 2 and step 6 already removed their clones.

## Operate

| Task | Command |
|---|---|
| Read last night's log | `sudo -u _radonbot tail -60 /Users/_radonbot/radon-runner/logs/documentation/$(date +%F).log` |
| launchd's own output (start failures) | `sudo -u _radonbot tail /Users/_radonbot/radon-runner/launchd-documentation.log` |
| Is the job loaded, when did it last run | `sudo launchctl print system/com.radon.runner.documentation \| grep -E 'state\|last exit'` |
| Run a loop now | `sudo launchctl kickstart system/com.radon.runner.documentation` |
| Change `run_loop.sh` or a loop `.env` | merge to `main`, then repeat step 2 on the mini (clone into a fresh `mktemp -d` directory, `sudo install.sh <loop>`, e.g. `sudo install.sh documentation`, then remove the clone); the installed copy does not change until you do. The prompt is read from `main` every night and needs no reinstall |
| Rotate the GitHub token (before its expiry) | step 7.5 to 7.6, then step 8 |
| Release a held security P0/P1 | `sudo -u _radonbot sh -c 'echo "released: <finding-id>" >> ~/radon-runner/state/<loop>/scratch/<run-id>/run-record.md'` |
| Read a security loop's private report | the Pushover page's "Open private report" link (`joemccann/radon-security-reports`) |
| After a reboot | `security unlock-keychain ~/Library/Keychains/login.keychain-db` in the bot shell, then `~/.local/bin/claude auth status`. Until then agy fails and both security loops fail every phase |
| Re-sign a CLI | the bot shell, then the sign-in command from step 4 |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `sudo: a password is required` from an agent or script | `sudo` needs your password at a terminal | Run the command yourself in Terminal |
| "Keychain Not Found ... antigravity" dialog | The bot has no login keychain | Cancel, then step 5 |
| `--check` passes but paths show your home | You ran it in your own tab | Rerun it in the bot shell |
| GitHub signup: "email ... already associated with an account" | One email per GitHub account | Use a different address |
| Signup form shows `joemccann` | Browser autofill | Clear the username and password fields, paste the bot's |
| PR author is `joemccann`, not the bot | `GH_TOKEN` holds your token | Replace it with the bot's (step 8) |
| Log stops at `starting agy`, then the next agent | Keychain locked after a reboot | Unlock it (Operate) |
| A security loop's log shows claude `not logged in` on every rung, every phase `FAILED` | Keychain locked after a reboot, so the claude.ai session is unreadable | Unlock it, then `~/.local/bin/claude auth status` (Operate); re-sign with step 8b.4 if still logged out |
| `no timeout or gtimeout in /opt/homebrew/bin:/usr/bin:/bin` | coreutils missing | `brew install coreutils` |

## Loop config

`scripts/runner/loops/<loop>.env` sets `BRANCH_PREFIX`, `PROMPT`, `AGENTS` (agent names in order; `fx:<provider>` for fx, `claude:<model>` for claude, which always runs with `--effort medium`), `TIMEOUT_SECS`, `SCHEDULE_HOUR` and `SCHEDULE_MINUTE`. Adding a loop is one `.env`, one prompt and one `install.sh <loop>`.

Optional knobs (empty means off; the security loops use them):

| Knob | Effect |
|---|---|
| `AGENT_ENV` | `NAME=value` words exported for the resolver, the hooks and the agent |
| `AGENT_UNSET` | Names unset before anything runs. Each one found set is logged as `IGNORING: <NAME>`, never with its value |
| `ALLOWED_AGENTS` | A rung whose agent is not listed is `REFUSED` (exit 2, one page) before cloning |
| `AGENTS_RESOLVER` | A root-owned script under `/usr/local/radon-runner`, run as `/opt/homebrew/bin/python3.13 -I <script> --rungs`; its stdout replaces `AGENTS`. Empty or failed output keeps `AGENTS` and is logged |
| `KEEP_PATHS` | Relative paths moved into `state/<loop>/keep/` before the re-clone and back after it. Whatever is in `keep/` is always restored, so a crash leaves nothing behind and an operator can seed it |
| `PHASES` | `name:secs ...`: one agent session per phase with its own timeout and a header carrying `Phase:` and `State:`. Every phase runs whatever the earlier rc; each pages `radon <loop> <phase>`. The runner exits 75 if any phase is not OK and 2 if one was refused |
| `PRE_RUN` / `POST_RUN` | Root-owned hooks under `/usr/local/radon-runner`, run around each phase with `LOOP`, `PHASE`, `WORK`, `LOOP_STATE`, `BRANCH` (and, after it, `PHASE_RC`, `PHASE_LOG`, `PHASE_START_MARK`), the runner's `PATH`, and a 900s cap. A non-zero `PRE_RUN` skips the agent (`REFUSED`); `POST_RUN` prints `status=` (`OK...` means success; the last one printed wins), `pr_url=` and `report_url=` for the page. A `POST_RUN` that prints no `status=` (timed out, crashed, missing) fails the phase. With `PRE_RUN` set, the header tells the agent to branch from `HEAD` (the base the hook pinned), not `origin/main` |
| `GH_GUARD=1` | Puts `/usr/local/radon-runner/guard/<loop>/gh` first on the agent's `PATH`: every `pr` / `api` / `issue` / `alias` call goes through `nightly_pr_guard.py` |

Every agent gets `RADON_RUNNER_LOOP_STATE` (the loop's state directory), `RADON_RUNNER_PIDFILE` (list a detached job's pid there and the runner reaps it), `RADON_RUNNER_AGENT` and `RADON_RUNNER_MODEL`. After each agent the runner kills its process group, then every bot process whose cwd is in the loop's clone or state and every declared pid that started during the phase, except one that is, or descends from, another loop's live runner. On SIGTERM, including one that arrives while a hook runs, it does the same, pages `KILLED`, gives `POST_RUN` ten seconds and exits 143. The LaunchDaemon sets `DISABLE_AUTOUPDATER=1` and `ExitTimeOut` 60. PR lookup ignores fork PRs.

## Migration from the per-loop wrappers

| Loop | State |
|---|---|
| documentation | Cut over: runs at 03:00 on branch `documentation/<date>` as `_radonbot`; the old wrapper, setup script, LaunchAgent and skill are deleted |
| ci-performance | Cut over: runs at 00:20 on branch `ci-performance/<date>`; `scripts/ci_performance_nightly.sh` and its LaunchAgent are deleted |
| testing | Cut over: runs at 00:10 on branch `testing/<date>` (4h budget, for the closing three rounds of the full gates); `scripts/testing_weekend.sh`, its setup script, LaunchAgent, skill and portable prompts are deleted. It gets no scoped Turso or Unusual Whales key: the suites need none |
| reliability | Cut over: runs at 00:00 on branch `reliability/<date>`; `scripts/reliability_weekend.sh`, its setup script, LaunchAgent, skill and portable prompts are deleted. Full pytest, `cloud/tests` and Vitest run in PR CI; the night runs focused suites and the drills |
| security | Cut over: runs at 00:40 on branch `security/<date>`, claude only, three phases (audit 2h, remediate 6h, deliver 3h), same dead-man label `security-nightly`; `scripts/security_nightly.sh`, its setup script, LaunchAgent, skill and the shared ladder shim are deleted, and the rails live in `scripts/runner/hooks/security_{pre,post}.sh` |
| security-deepsec | Cut over: runs at 00:50 on branch `security-deepsec/<date>`, audit 8h, dead-man label `security-deepsec`, `.deepsec/` and `data/radon/` kept across re-clones; `scripts/security_deepsec_nightly.sh`, its LaunchAgent and skill are deleted |

Cutover per loop: compare three nights of shadow PRs, set `BRANCH_PREFIX` to the old prefix, unload the old LaunchAgent, then delete the old wrapper, setup script, helpers and their tests. The security loops cut over directly with no shadow nights: two runners would both post the dead-man and deliver on the same branch. The CLI env-drift check and the stable Claude copy went with them (the pre-run hook runs the drift check; the bot has its own claude).

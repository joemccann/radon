# Nightly agent runner

One loop run is what the operator would do by hand: open an agent CLI in a fresh clone of `main`, paste the loop's prompt, walk away. `scripts/runner/run_loop.sh <loop>` does exactly that:

1. Take the loop's lock (a live run makes the new fire exit 0; a dead run's lock is reclaimed).
2. Delete the previous clone and `git clone` `main` again, so nothing an agent left behind (files, `.git` hooks, config) survives to the next night.
3. Prepend a header (date, branch `<BRANCH_PREFIX>/<date>`, time budget) to `.claude/runner-prompts/<loop>.md` from that clone.
4. Run the first agent in `AGENTS` under `timeout`. The next agent runs only if one exits non-zero; a timeout ends the night. Anything the agent left running is killed with its process group.
5. Look up the PR for the branch and send one Pushover message with the agent's `RESULT:` line and the PR link.

The agent does the whole job in one session: audit, fix, push the branch, open a draft PR, watch CI. The prompt owns what the loop does; the runner owns nothing else.

## Safety boundary

Safety is where the runner runs, not what the script checks:

| Control | Enforced by |
|---|---|
| Agent cannot read operator files, ssh keys, `~/.radon`, Keychain | Dedicated macOS user `_radonbot`, standard (non-admin), hidden |
| Agent cannot edit the runner | `run_loop.sh` and `loops/*.env` installed root-owned in `/usr/local/radon-runner` |
| No production credential in the clone | The clone gets no `.env`. `~/.radon-runner.env` may hold only `GH_TOKEN`, `PUSHOVER_USER` and `PUSHOVER_TOKEN`. The agent runs as `_radonbot`, so it can read that file. The runner unsets the Pushover keys in the agent process; `GH_TOKEN` stays so the agent can push. Do not put a production credential or an admin token in that file or anywhere in the bot's home |
| Agent cannot merge or push `main` | A separate GitHub machine account with the Write role on this repo only, used through its classic `repo` token, plus a `main` ruleset requiring 1 approval with the Repository admin role as bypass actor. A token of the operator's own account would not do: it acts as the admin and bypasses the ruleset |

The ruleset is applied at cutover. Before then every nightly loop still runs as the operator, whose admin token bypasses it anyway, and `scripts/codemap_nightly.sh` merges its own PR with that token.

## Set up a new Mac

The whole install, start to finish, on a Mac that has never run Radon. Allow about 45 minutes. Everything runs in Terminal as the operator (an admin account); a step says when to switch to the bot shell. You never need to log in to the bot's desktop.

Where things live when you are done:

| Path | Owner | Holds |
|---|---|---|
| `/usr/local/radon-runner/run_loop.sh`, `loops/<loop>.env` | root | The runner and each loop's config. The agent cannot edit them |
| `/Library/LaunchDaemons/com.radon.runner.<loop>.plist` | root | The nightly schedule, run as `_radonbot` |
| `/Users/_radonbot/.radon-runner.env` | bot, 600 | `GH_TOKEN`, `PUSHOVER_USER`, `PUSHOVER_TOKEN`. The agent can read it (it runs as the bot); the runner unsets the Pushover keys in the agent process. Never a production or admin credential |
| `/Users/_radonbot/.radon/agent-cli/env` | bot, 600 | `NVIDIA_API_KEY`, `CEREBRAS_API_KEY` for the fx agents |
| `/Users/_radonbot/radon-runner/work/<loop>` | bot | Tonight's clone, deleted and re-cloned every run |
| `/Users/_radonbot/radon-runner/logs/<loop>/<date>.log` | bot, 700 | The run log, kept 14 days |
| Operator login keychain: `github-radon-runner-bot`, `github-radon-runner-bot-token` | operator | The bot's GitHub password and token, for rotation |

### 1. Prerequisites (operator)

```bash
# Homebrew first if the Mac has none: https://brew.sh
brew install git gh coreutils python@3.13 uv bun
brew install --cask codex
sudo pmset -a sleep 0 displaysleep 10     # the Mac must stay awake at night
```

`coreutils` provides `timeout`; the runner exits 69 without it. `codex` is shared from Homebrew; the other agent CLIs are installed per user in step 4.

### 2. Install the runner (operator)

```bash
/usr/bin/git clone https://github.com/joemccann/radon.git /tmp/radon-runner-install
/bin/bash /tmp/radon-runner-install/scripts/runner/install.sh --print-plist documentation   # review
sudo /bin/bash /tmp/radon-runner-install/scripts/runner/install.sh documentation
```

It asks for your sudo password, then `User password:` for the new `_radonbot` account. Choose a password and save it (your password manager, or `security add-generic-password -s radon-runnerbot-login -a _radonbot -w`); step 5 reuses it. The `No clear text password ... FDE` warning and `Home directory is assigned (not created!)` are expected: the script creates the home right after. It ends with `installed com.radon.runner.documentation`.

The account is hidden, so it does not appear on the login screen. Check it:

```bash
id _radonbot && ls -ld /Users/_radonbot /usr/local/radon-runner/run_loop.sh
```

### 3. Open the bot shell

Open a second Terminal tab for this. Steps 4, 5, 6 and 9 run in it:

```bash
sudo -u _radonbot -H /bin/zsh -l
```

The prompt shows `_radonbot@...`. Before running a check, confirm its output paths start with `/Users/_radonbot`. The same command in your own tab checks your account instead.

### 4. Agent CLIs (bot shell)

Install and sign in to every agent the loop's `AGENTS` names (`grok codex agy fx:nvidia fx:cerebras` for documentation). The sign-ins print a URL or a device code: open it in your own browser, signed in to the account that owns the subscription.

| Agent | Install (bot shell) | Sign in (bot shell) | Binary the runner uses |
|---|---|---|---|
| grok | `curl -fsSL https://x.ai/cli/install.sh \| bash` | `~/.grok/bin/grok`, approve the device code | `~/.grok/bin/grok` |
| codex | shared, step 1 | `/opt/homebrew/bin/codex login` (or `codex login --device-auth`) | `/opt/homebrew/bin/codex` |
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

A reboot locks this keychain. After one, run `security unlock-keychain ~/Library/Keychains/login.keychain-db` in the bot shell; until then the agy rung fails and the loop moves to the next agent.

### 6. Provider keys for fx (operator, then bot shell)

In your own tab, copy only the two keys from your agent-cli env into the bot's. `grep` runs as you, so it reads your file; `sudo` only writes the bot's:

```bash
grep -E "^(export )?(NVIDIA_API_KEY|CEREBRAS_API_KEY)=" ~/.radon/agent-cli/env | sudo /bin/sh -c 'umask 077; d=/Users/_radonbot/.radon; install -d -m 700 -o _radonbot -g staff $d $d/agent-cli && cat > $d/agent-cli/env && chown _radonbot:staff $d/agent-cli/env'
```

On a Mac where you have no such file, create `/Users/_radonbot/.radon/agent-cli/env` in the bot shell with `NVIDIA_API_KEY=` and `CEREBRAS_API_KEY=` lines instead. Then, in the bot shell:

```bash
/bin/bash /tmp/radon-runner-install/scripts/agent_cli_bootstrap.sh
/bin/bash /tmp/radon-runner-install/scripts/agent_cli_bootstrap.sh --check
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

### 9. Smoke test (bot shell)

```bash
/bin/bash /usr/local/radon-runner/run_loop.sh documentation
```

It runs in the foreground for up to `TIMEOUT_SECS` (3 hours for documentation); the 2026-09-28 run took 15 minutes. Do not stop it to read the log. Follow it from your own tab instead (the log directory is mode 700, so your account needs `sudo -u`):

```bash
sudo -u _radonbot tail -f /Users/_radonbot/radon-runner/logs/documentation/$(date +%F).log
```

It passes when the log starts with `starting grok`, a draft PR on `documentation-runner/<date>` appears with author `radon-runner-bot` (`gh pr list --author radon-runner-bot --state all`), and a `radon documentation: done via <agent>` Pushover arrives.

### 10. Clean up

`exit` the bot shell, then `/bin/rm -rf /tmp/radon-runner-install`.

## Operate

| Task | Command |
|---|---|
| Read last night's log | `sudo -u _radonbot tail -60 /Users/_radonbot/radon-runner/logs/documentation/$(date +%F).log` |
| launchd's own output (start failures) | `sudo -u _radonbot tail /Users/_radonbot/radon-runner/launchd-documentation.log` |
| Is the job loaded, when did it last run | `sudo launchctl print system/com.radon.runner.documentation \| grep -E 'state\|last exit'` |
| Run a loop now | `sudo launchctl kickstart system/com.radon.runner.documentation` |
| Change `run_loop.sh` or a loop `.env` | merge to `main`, then repeat step 2 (clone and `sudo install.sh <loop>`). The prompt is read from `main` every night and needs no reinstall |
| Rotate the GitHub token (before its expiry) | step 7.5 to 7.6, then step 8 |
| After a reboot | `security unlock-keychain ~/Library/Keychains/login.keychain-db` in the bot shell (agy only) |
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
| `no timeout or gtimeout on PATH` | coreutils missing | `brew install coreutils` |

## Loop config

`scripts/runner/loops/<loop>.env` sets `BRANCH_PREFIX`, `PROMPT`, `AGENTS` (agent names in order; `fx:<provider>` for fx), `TIMEOUT_SECS`, `SCHEDULE_HOUR` and `SCHEDULE_MINUTE`. Adding a loop is one `.env`, one prompt and one `install.sh <loop>`.

## Migration from the per-loop wrappers

| Loop | State |
|---|---|
| documentation | Shadow: runs at 03:00 on branch `documentation-runner/<date>` next to `scripts/documentation_nightly.sh` |
| ci-performance, testing, reliability, security, DeepSec | Not started; still on `scripts/<loop>_*.sh` (see `docs/operations.md`) |

Cutover per loop: compare three nights of shadow PRs, set `BRANCH_PREFIX` to the old prefix, unload the old LaunchAgent, then delete the old wrapper, setup script, helpers and their tests.

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
| No production credential in reach | The clone gets no `.env`; `~/.radon-runner.env` holds only `GH_TOKEN` and Pushover keys (hidden from the agent) |
| Agent cannot merge or push `main` | A separate GitHub machine account with the Write role on this repo only, used through its classic `repo` token, plus a `main` ruleset requiring 1 approval with the Repository admin role as bypass actor. A token of the operator's own account would not do: it acts as the admin and bypasses the ruleset |

The ruleset is applied at cutover. Before then every nightly loop still runs as the operator, whose admin token bypasses it anyway, and `scripts/codemap_nightly.sh` merges its own PR with that token.

## Install (on the always-on Mac)

Run these in Terminal on the mini, logged in as the operator. The installer runs from a throwaway clone, never from a loop clone an agent can write.

1. Get the runner (before #778 merges, add `--branch feat/simple-loop-runner`):

   ```bash
   /usr/bin/git clone https://github.com/joemccann/radon.git /tmp/radon-runner-install
   ```

2. Review the LaunchDaemon, then install (asks for your sudo password, then a new password for `_radonbot`):

   ```bash
   /bin/bash /tmp/radon-runner-install/scripts/runner/install.sh --print-plist documentation
   sudo /bin/bash /tmp/radon-runner-install/scripts/runner/install.sh documentation
   ```

   This creates `/Users/_radonbot`, `/usr/local/radon-runner/run_loop.sh`, `/usr/local/radon-runner/loops/documentation.env`, `/Users/_radonbot/.radon-runner.env` and `/Library/LaunchDaemons/com.radon.runner.documentation.plist`.

3. Open a shell as the bot. Every step below runs in it:

   ```bash
   sudo -u _radonbot -H /bin/zsh -l
   ```

4. Log in each CLI the loop's `AGENTS` names. Binaries are per user, so install them into the bot's home with the same installers used for the operator. The runner finds them at these paths:

   | Agent | Binary | Login |
   |---|---|---|
   | grok | `/Users/_radonbot/.grok/bin/grok` | run it once and sign in, or put `XAI_API_KEY` in its environment |
   | codex | `/opt/homebrew/bin/codex` (shared Homebrew install) | `/opt/homebrew/bin/codex login` |
   | agy | `/Users/_radonbot/.local/bin/agy` | run it once and sign in |
   | fx | `/Users/_radonbot/.local/bin/fx` | `curl -fsSL https://fx.sh/setup.sh \| bash`, then the provider keys below |

   Browser sign-ins need a GUI session: use Fast User Switching into `_radonbot` once for those.

5. Provider keys for `fx:nvidia` and `fx:cerebras`: put `NVIDIA_API_KEY=` and `CEREBRAS_API_KEY=` in `/Users/_radonbot/.radon/agent-cli/env` (mode 600), then write the fx and grok provider configs and check every CLI:

   ```bash
   /bin/bash /tmp/radon-runner-install/scripts/agent_cli_bootstrap.sh
   /bin/bash /tmp/radon-runner-install/scripts/agent_cli_bootstrap.sh --check
   ```

6. GitHub identity: create a machine account (for example `radon-runner-bot`) and add it to `joemccann/radon` as a collaborator with the Write role. Signed in as that account, create a classic token with only the `repo` scope. Put it in `/Users/_radonbot/.radon-runner.env` as `GH_TOKEN=`, with the bot's own `PUSHOVER_USER=` and `PUSHOVER_TOKEN=`:

   ```bash
   /usr/bin/nano /Users/_radonbot/.radon-runner.env
   ```

7. Smoke test, then read the log:

   ```bash
   /bin/bash /usr/local/radon-runner/run_loop.sh documentation
   /bin/cat /Users/_radonbot/radon-runner/logs/documentation/$(date +%F).log
   ```

8. Leave the bot shell (`exit`) and delete the install clone: `/bin/rm -rf /tmp/radon-runner-install`.

Re-run step 2 after changing `run_loop.sh` or a loop's `.env`. The prompt is read from `main` every night and needs no reinstall.

## Loop config

`scripts/runner/loops/<loop>.env` sets `BRANCH_PREFIX`, `PROMPT`, `AGENTS` (agent names in order; `fx:<provider>` for fx), `TIMEOUT_SECS`, `SCHEDULE_HOUR` and `SCHEDULE_MINUTE`. Adding a loop is one `.env`, one prompt and one `install.sh <loop>`.

## Migration from the per-loop wrappers

| Loop | State |
|---|---|
| documentation | Shadow: runs at 03:00 on branch `documentation-runner/<date>` next to `scripts/documentation_nightly.sh` |
| ci-performance, testing, reliability, security, DeepSec | Not started; still on `scripts/<loop>_*.sh` (see `docs/operations.md`) |

Cutover per loop: compare three nights of shadow PRs, set `BRANCH_PREFIX` to the old prefix, unload the old LaunchAgent, then delete the old wrapper, setup script, helpers and their tests.

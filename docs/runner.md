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
| Agent cannot merge or push `main` | Fine-grained GitHub token for this repo only (Contents, Pull requests, Issues: read/write; Actions: read) plus a `main` ruleset requiring 1 approval with the operator as bypass actor |

The ruleset is applied at cutover. Before then every nightly loop still runs as the operator, whose admin token bypasses it anyway, and `scripts/codemap_nightly.sh` merges its own PR with that token.

## Install (on the always-on Mac)

```bash
sudo scripts/runner/install.sh documentation     # creates _radonbot on first use (prompts for its password)
scripts/runner/install.sh --print-plist documentation   # review the LaunchDaemon without root
```

One-time manual steps, as the bot user (`sudo -u _radonbot -H zsh -l`):

1. Install and log in each CLI named in the loop's `AGENTS` (for example `grok`, `codex login --device-auth`, `agy`, and `~/.fx/settings.json` for fx providers). Browser-based logins need a GUI session: use Fast User Switching into the bot account once.
2. Create the fine-grained token above and put it in `~/.radon-runner.env` as `GH_TOKEN=`, with the bot's own Pushover keys.
3. Smoke test: `bash /usr/local/radon-runner/run_loop.sh documentation`, then read `~/radon-runner/logs/documentation/<date>.log`.

Re-run `install.sh` after changing `run_loop.sh` or a loop's `.env`; the prompt is read from `main` every night and needs no reinstall.

## Loop config

`scripts/runner/loops/<loop>.env` sets `BRANCH_PREFIX`, `PROMPT`, `AGENTS` (agent names in order; `fx:<provider>` for fx), `TIMEOUT_SECS`, `SCHEDULE_HOUR` and `SCHEDULE_MINUTE`. Adding a loop is one `.env`, one prompt and one `install.sh <loop>`.

## Migration from the per-loop wrappers

| Loop | State |
|---|---|
| documentation | Shadow: runs at 03:00 on branch `documentation-runner/<date>` next to `scripts/documentation_nightly.sh` |
| ci-performance, testing, reliability, security, DeepSec | Not started; still on `scripts/<loop>_*.sh` (see `docs/operations.md`) |

Cutover per loop: compare three nights of shadow PRs, set `BRANCH_PREFIX` to the old prefix, unload the old LaunchAgent, then delete the old wrapper, setup script, helpers and their tests.

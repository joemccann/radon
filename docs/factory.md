# Radon software factory

Foreman under `factory/` is the GitHub-issue SDLC loop for `joemccann/radon`. It is a Vercel eve app, not a Hetzner unit. It stops at a **draft pull request**. A person marks ready and merges. Merge is the deploy trigger.

It does **never merge**. It does **never push main**. Direct pushes to `main` / `master` are refused in `factory/agent/lib/github/git-remote.ts`.

## Intake

- Intake label `factory`. Unattended run. Labeler must have triage or above.
- `@mention` the factory bot on an issue or PR (owners, members, collaborators).
- Red CI on a `factory/` branch the factory itself pushed: one diagnosis + fix loop, capped.

Do not label P1 incidents `factory`. That loop is [`grok-page-responder.md`](grok-page-responder.md).

## In scope

UI, copy, tests, docs, indicator vertical slices that follow `.claude/skills/new-indicator`, CI/test-only changes, reliability findings that do not touch IB.

## Stop classes (Classifier `needs_clarification`, Reviewer `reject`)

- IB Gateway, 2FA, docker, or host control of the broker
- placing, modifying, or cancelling a live order
- the trading halt or order-routing caps
- production .env, Clerk, Turso tokens, IB_FLEX, UW_TOKEN, archive keys
- deploy.sh, systemd control-plane, privileged VPS helpers
- a P1 incident or watchdog page

## Rails the stations must follow

- Red/green TDD. Focused `bunx vitest` / `python3.13 scripts/run_pytest_affected.py`.
- `git add` by path. Never `git add -A`.
- No Playwright / chrome-cdp in the sandbox. Live browser is a human step.
- Never load production secrets into the sandbox. Setup is `scripts/factory_sandbox_setup.sh` (bun + pip only).
- Factory brain writes: trusted callers only. Unattended runs cannot poison it.

## Deploy the factory app

From `factory/`, Node 24, with a Vercel project linked:

1. `pnpm install && pnpm validate`
2. `vercel connect create github --triggers` then attach at `/eve/v1/github`
3. Set `FACTORY_REPO=joemccann/radon`, `FACTORY_LABEL=factory`, `FACTORY_BRANCH_PREFIX=factory/`, `FACTORY_SETUP_COMMAND=bash scripts/factory_sandbox_setup.sh`
4. Create a Blob store. `eve deploy`
5. Install the GitHub App on `joemccann/radon` with contents, issues, and pull request write.
6. First live run: a docs or test-only issue. You merge.

Linear is not wired. Do not set a Linear connector.

Upstream template: `vercel-labs/eve-software-factory-template` at the SHA in `factory/UPSTREAM`.

## Operate

Failed run: flawed (prompt/eval), blocked (sandbox/dep), or manual (correct refusal). Fix the factory, not the ticket, unless the ticket was wrong.

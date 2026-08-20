# Contributing

Radon is a proprietary, single-operator trading system. This guide is for the operator.

The canonical developer runbook is `CLAUDE.md`. Read it before making changes. For a map of the tools that author this project (agents, session tooling, verification gates), see `DEVELOPMENT.md`. Documentation index: `docs/README.md`. Security reports: `SECURITY.md`. Clones are unsupported: `SUPPORT.md`.

## Workflow

- All work commits to `main`.
- Red/green TDD is required: write a failing test first, make it pass, then commit.
  - Web: Vitest.
  - Python: pytest.
- Docs stay a thin index. `docs/owners.json` maps path globs to one owner file. If you change a mapped path, update an owner in the same commit or write `docs-skip: <reason>` in the message. Do not add a changelog to README.

## CI and deploy

- The CI gate is `.github/workflows/ci.yml` (Vitest + pytest). It must be green before the deploy job runs.
- Run tests per the README "Tests" section before pushing.

## Operations

See `docs/operations.md` for service and deploy operations.

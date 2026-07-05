# Contributing

Radon is a proprietary, single-operator trading system. This guide is for the operator.

The canonical developer runbook is `CLAUDE.md`. Read it before making changes. For a map of the tools that author this project (agents, session tooling, verification gates), see `DEVELOPMENT.md`.

## Workflow

- All work commits to `main`. Never commit to the beta branch unless explicitly directed.
- Red/green TDD is required: write a failing test first, make it pass, then commit.
  - Web: Vitest.
  - Python: pytest.

## CI and deploy

- The CI gate is `.github/workflows/ci.yml` (Vitest + pytest). It must be green before the deploy job runs.
- Run tests per the README "Tests" section before pushing.

## Operations

See `docs/operations.md` for service and deploy operations.

# Contributing

Radon is an open-source (Apache-2.0 OR MIT), single-operator trading system. This guide is for the operator.

Contributions are dual licensed: unless you explicitly state otherwise, any contribution you intentionally submit for inclusion in this work, as defined in the Apache-2.0 license, is licensed as Apache-2.0 OR MIT, without any additional terms or conditions.

The canonical developer runbook is `CLAUDE.md`. Read it before making changes. For a map of the tools that author this project (agents, session tooling, verification gates), see `DEVELOPMENT.md`. Documentation index: `docs/README.md`. Security reports: `SECURITY.md`. Clones are unsupported: `SUPPORT.md`.

## Workflow

- Commit on a task branch and open a pull request against `main`. The body follows `.github/pull_request_template.md` (Summary, What changed, Test plan, Risk and rollback, Operator Next Steps, Checklist); the `PR body template` check fails any other shape, including `gh pr create --body` text that skipped the template. Never push directly to `main`. Follow the repository review requirements and verify all applicable checks on the latest PR head before merge. The separate operator release coordinator may use the [standing unattended release policy](docs/unattended-releases.md); the nightly machine account still cannot merge or deploy.
- Red/green TDD is required: write a failing test first, make it pass, then commit.
  - Web: Vitest.
  - Python: pytest.
- Docs stay a thin index. `docs/owners.json` maps path globs to one owner file. If you change a mapped path, update an owner in the same commit or write `docs-skip: <reason>` in the message. Do not add a changelog to README.

## CI and deploy

- The CI gate is `.github/workflows/ci.yml` (Vitest + pytest). It must be green before the deploy job runs.
- `.github/CODEOWNERS` is how we require owner review for CI workflow changes. `/.github/workflows/` and `/.github/actions/` are owned by `@joemccann`, and the existing `* @joemccann` default stays. The repository ruleset's "require code owner review" setting is what turns that file into an enforced check. Editing CODEOWNERS does not change the setting.
- Run tests per the README "Tests" section before pushing.

## Operations

See `docs/operations.md` for service and deploy operations.

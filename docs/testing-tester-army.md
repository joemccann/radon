# TesterArmy flow tests

Radon's browser projects have independent [TesterArmy e2e](https://github.com/tester-army/e2e)
packages at `web/tester-army` and `site/tester-army`. Their locked SDK/browser
dependencies are separate from the existing Playwright suites. The existing
financial, authorization and deployment gates remain in place.
The existing financial Playwright smoke and the TesterArmy workflow are
independent browser checks; neither is a dependency of the production deploy job.

## Install and run

Use Node.js 22.12 or newer and the repository's Bun version. From the root:

```sh
env -i PATH="$PATH" HOME="$HOME" bun install --frozen-lockfile
env -i PATH="$PATH" HOME="$HOME" bun install --frozen-lockfile --cwd web
env -i PATH="$PATH" HOME="$HOME" npm ci --prefix site
env -i PATH="$PATH" HOME="$HOME" npm ci --prefix web/tester-army
env -i PATH="$PATH" HOME="$HOME" npm ci --prefix site/tester-army
```

Install Chromium through each isolated package so its version matches the
engine. When both packages pin the same Playwright version, the second command
reuses the cache.

```sh
(cd web/tester-army && env -i PATH="$PATH" HOME="$HOME" npx playwright install chromium)
(cd site/tester-army && env -i PATH="$PATH" HOME="$HOME" npx playwright install chromium)
env -i PATH="$PATH" HOME="$HOME" npm test --prefix web/tester-army
env -i PATH="$PATH" HOME="$HOME" npm test --prefix site/tester-army
```

Each runner owns a separate localhost application process and `.next-tester-army`
build directory. The workstation defaults to port 3341; marketing uses 3342.
The config starts the app, waits for readiness, and stops its process on exit.
Tests use deterministic SDK locators and assertions and require no model login.
The local commands strip inherited credentials from the runner process. Add
only explicit toolchain or synthetic fixture variables when overriding them.

Reports, JUnit, screenshots and failure evidence live in each package's
ignored `.e2e` directory. Read `.e2e/report.json` before rerunning a failure:

```sh
env -i PATH="$PATH" HOME="$HOME" npm test --prefix web/tester-army -- --last-failed
env -i PATH="$PATH" HOME="$HOME" npm test --prefix site/tester-army -- --last-failed
```

## CI

`.github/workflows/tester-army.yml` runs one job per browser project on relevant
PRs, pushes to main and manual dispatch. It installs the engine's browser,
prebuilds Next.js using the project's existing compile mode, sets
`TESTER_ARMY_SERVER_MODE=start`, and retains each runner's report and artifacts.
The workstation job also runs the output-trace audit regressions, and its normal
build audits the selected `NEXT_DIST_DIR`. It uses no production credentials
and adds no model provider requirement. Both SDK configs disable replay caching
and retain failure traces.

## Coverage and verification boundaries

Route rendering is a smoke check. Interactive journeys must assert their
observable state changes, data, navigation or submitted payloads. Project
matrices describe which journeys have behavioral coverage and which routes
have only rendering evidence. Existing Playwright regression suites retain
their own coverage and CI curation ledger.

The workstation browser tests intercept API and realtime transports with
synthetic fixtures. Mutations must remain intercepted. Backend HTTP contracts
use isolated subprocess/cache replacements; their matrix is
`scripts/api/tests/workstation-flow-matrix.md`. These boundaries allow order,
error, retry and research workflows to run without changing a broker account
or a production database. They do not establish live broker execution,
provider availability, private account configuration or Clerk sign-in.

Run relevant backend tests in an environment installed from the pinned Python
requirements. PyJWT 2.15.0 is required for the existing JWKS client controls.
Do not run pytest and Vitest concurrently on the laptop; both suites retain
the repository's worker budgets.

Run the complete unit configuration with the root Vitest installation, which
matches the root coverage provider and CI. The web package's `test` command
filters to `web/tests` and cannot establish full repository coverage.

```sh
env -i PATH="$PATH" HOME="$HOME" NODE_ENV=test ASSISTANT_MOCK=1 \
  RADON_API_URL=http://127.0.0.1:1 \
  TURSO_DB_URL= TURSO_AUTH_TOKEN= TURSO_DEMO_DB_URL= TURSO_DEMO_AUTH_TOKEN= \
  node node_modules/vitest/vitest.mjs run --config vitest.config.ts --coverage
```

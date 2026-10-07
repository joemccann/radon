# TesterArmy workstation verification

Official SDK: https://github.com/tester-army/e2e. Pinned `e2e@0.16.0`,
`@e2e-dev/web@0.11.2`, `playwright@1.63.0` run in this isolated package.
Radon's existing Playwright 1.58.2 suites and CI curation remain independent.
The existing financial browser smoke job is non-gating; this suite does not
change the status of historical held-out browser specs.

From the repository root after the regular Bun install:

```sh
env -i PATH="$PATH" HOME="$HOME" npm ci --prefix web/tester-army
env -i PATH="$PATH" HOME="$HOME" npm --prefix web/tester-army exec -- playwright install chromium
env -i PATH="$PATH" HOME="$HOME" npm --prefix web/tester-army test
```

Requires Node >=22.12 and Bun. `pretest` regenerates ignored `fixtures.json`
from the existing Clear Playwright fixture builders. This avoids native ESM
resolution errors for extensionless application TypeScript imports and avoids
committing a duplicated 1.3 MB data snapshot. `bun web/tester-army/generate-fixtures.ts`
regenerates it for `e2e list` or direct CLI use.

The default server is localhost:3341 with `.next-tester-army`. Override
`TESTER_ARMY_PORT` for another loopback port. `TESTER_ARMY_SERVER_MODE=start`
uses a prebuilt Next server on 127.0.0.1; build with the matching
`NEXT_DIST_DIR=.next-tester-army` and test Clerk stub first. CI uses that mode.
A generated authless header token is shared by coordinator, workers and server.
No model or agent provider is configured. Deterministic SDK locators drive
all actions and assertions. `surfaceOf` accesses Playwright only for transport
fixtures, browser initialization, fixed fixture clock, and screenshot positioning/geometry.

All API traffic is intercepted, unknown APIs respond 503, external browser
requests are aborted, and `RADON_API_URL=http://127.0.0.1:1` isolates server-owned
FastAPI requests. Order confirmations assert only intercepted payloads. No
live orders, credentials, third-party login, or external messages are exercised.

Evidence: `.e2e/report.json`, `.e2e/junit.xml`, `.e2e/summary.md`, and per-attempt
screenshots/traces in `.e2e/artifacts`. These are ignored local artifacts and
uploaded by the additive workflow. Route render checks are smoke coverage,
not a claim that every possible interactive or broker behavior was verified.

See [FLOW-COVERAGE.md](FLOW-COVERAGE.md) for every route and explicit limits.

## Retained Playwright verification

The regular suite contains 202 `*.spec.ts` files. Its existing collection guard
excludes standalone `*.test.js`. Run independently with the original Playwright
1.58.2 after stopping this SDK server; port 3000 is needed by historical absolute
URL assertions. Check that port 3000 is free before launching because the original
config permits server reuse. From `web/`, use a sanitized environment:

```sh
mkdir -p /tmp/radon-legacy-shots
RADON_TEST_CLERK_KEY=pk_test_cmFkb24tZTJlLXN0dWIuY2xlcmsuYWNjb3VudHMuZGV2JA # gitleaks:allow -- synthetic publishable identifier, no credential
env -i PATH="$PATH" HOME="$HOME" RADON_API_URL=http://127.0.0.1:1 \
  TURSO_DB_URL= TURSO_AUTH_TOKEN= TURSO_DEMO_DB_URL= TURSO_DEMO_AUTH_TOKEN= \
  NEXT_DIST_DIR=.next-tester-army \
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY="$RADON_TEST_CLERK_KEY" \
  npm run build
env -i PATH="$PATH" HOME="$HOME" RADON_API_URL=http://127.0.0.1:1 \
  TURSO_DB_URL= TURSO_AUTH_TOKEN= TURSO_DEMO_DB_URL= TURSO_DEMO_AUTH_TOKEN= \
  NEXT_DIST_DIR=.next-tester-army PLAYWRIGHT_PORT=3000 PLAYWRIGHT_BASE_HOST=127.0.0.1 \
  PLAYWRIGHT_WEBSERVER_CMD='node node_modules/next/dist/bin/next start -p 3000' \
  FX_SHOT_DIR=/tmp/radon-legacy-shots VIXCOR_SHOT_DIR=/tmp/radon-legacy-shots \
  node node_modules/@playwright/test/cli.js test --config playwright.config.ts
```

The config shares its generated authless token with workers and the server.
The SDK is additive: existing financial, authorization, and realtime gates remain.
Provider-backed sign-in/operator checks require a dedicated authorized environment;
these guarded pages are inventoried rather than simulated.

Chromium delegates mobile-only files to the iPhone project while retaining the
mixed mobile-combo switcher desktop case. Current-base collection comparison:
951 executions before routing, 852 after; all 849 unique cases across 202 files
remain. The restored portfolio-sync revalidation case then raises final collection
to 853 executions and 850 unique cases. No test is removed by this device routing.

The production output audit honors NEXT_DIST_DIR while retaining its original
file-count, byte, and forbidden-data controls. Run
`node --test web/scripts/audit-output-traces.test.mjs` from the repository root.

Current-base verification is recorded in the pull request. Historical recovery
notes in the curation ledger are dated evidence from earlier runs, not claims
about this branch's current head.

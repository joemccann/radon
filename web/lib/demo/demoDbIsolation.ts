// REL-245 (R-653): in-code demo/prod DB cross-check.
//
// The CI guard (scripts/ci/check_demo_isolation.py) is currently SKIPPED
// pending secrets, so a demo deploy carrying the PROD `TURSO_DB_URL` would
// serve the operator's live working orders and full portfolio to any trial
// user. This module makes the serving path itself fail closed: routes that
// resolve a demo principal call `demoDbIsolationViolation()` BEFORE any DB
// read, and refuse / fall back to fixtures when the DB is not marked demo.
//
// Marker mirrors PROD_DB_MARKER in scripts/ci/check_demo_isolation.py: on the
// demo VM, TURSO_DB_URL names the separate demo database and never contains
// this substring. An unset URL is not a violation here — getDb() already
// throws on it and every caller fails closed to fixtures / 503.

import { AsyncLocalStorage } from "node:async_hooks";

export const PROD_DB_MARKER = "radon-joemccann";

type EnvLike = Record<string, string | undefined>;

/** Non-null message when a demo principal must NOT be served DB rows. */
export function demoDbIsolationViolation(
  env: EnvLike = process.env,
): string | null {
  const url = env.TURSO_DB_URL ?? "";
  if (url.includes(PROD_DB_MARKER)) {
    return (
      `TURSO_DB_URL contains the prod marker "${PROD_DB_MARKER}" — ` +
      "demo principal resolved against the production database; refusing to serve DB rows."
    );
  }
  return null;
}

// F20260917-C04/C08: the route-level guard above only covered the two routes
// that called it. The check is ALSO enforced inside the shared `dbExecute`
// chokepoint (lib/dbExecute.ts), keyed on the DB marker, so blotter/RSC reads
// and the assistant's direct-Turso journal/portfolio tools inherit it. A
// caller is demo-scoped when the deployment itself is the demo VM
// (NEXT_PUBLIC_RADON_DEMO=1) or the current async execution scope was opened
// for a demo principal via `runWithDemoDbPrincipal`.

const demoPrincipalScope = new AsyncLocalStorage<true>();

/** Marks every DB call inside `fn` as executing for a demo principal. */
export function runWithDemoDbPrincipal<T>(fn: () => T): T {
  return demoPrincipalScope.run(true, fn);
}

export class DemoDbIsolationError extends Error {
  readonly code = "DEMO_DB_ISOLATION";
}

/** Throws when a demo-scoped caller would execute against a prod-marked DB. */
export function assertDemoDbIsolation(env: EnvLike = process.env): void {
  const demoScoped =
    demoPrincipalScope.getStore() === true || env.NEXT_PUBLIC_RADON_DEMO === "1";
  if (!demoScoped) return;
  const violation = demoDbIsolationViolation(env);
  if (violation) throw new DemoDbIsolationError(violation);
}

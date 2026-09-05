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

export const PROD_DB_MARKER = "radon-joemccann";

type EnvLike = { TURSO_DB_URL?: string };

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

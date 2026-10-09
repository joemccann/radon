/**
 * Browser-facing time budgets, derived from the edge.
 *
 * Caddy's catch-all `handle` (cloud/caddy/Caddyfile) sets
 * response_header_timeout 30s, and that bound is pinned by
 * cloud/tests/test_caddy_edge_timeouts.py. A Next route that waits on FastAPI
 * longer than this never delivers its own fallback: Caddy has already sent the
 * browser a 504. Pinned against the Caddyfile by
 * web/tests/edge-budget-contract.test.ts.
 */
export const EDGE_RESPONSE_HEADER_TIMEOUT_MS = 30_000;

/**
 * How long a producer-sync POST (/api/portfolio, /api/orders) waits on the
 * FastAPI sync before serving the latest Turso snapshot instead. FastAPI runs
 * the sync as a shielded single-flight task, so abandoning the wait does not
 * cancel it: the sync still lands in Turso and the next poll picks it up.
 */
export const PRODUCER_SYNC_WAIT_MS = 20_000;

/** Browser abort for a producer-sync POST: past the route's own deadline
 *  (sync wait plus the fallback read), short of the edge. */
export const BROWSER_PRODUCER_SYNC_TIMEOUT_MS = 28_000;

/** Re-read delay after a producer-sync POST reports the IB job still running
 *  server-side. Long enough for a typical sync to land in Turso, short of the
 *  30s poll. Shared by usePortfolio and useOrders (REL-323 / R-734). */
export const SYNC_PENDING_REPOLL_MS = 10_000;

/**
 * FastAPI /health bounds its gateway probe at 2.5s, so a healthy upstream
 * answers well inside this. It stays below the dashboard's 5s health abort so
 * the route answers before its caller has gone away.
 */
export const ADMIN_HEALTH_UPSTREAM_TIMEOUT_MS = 4_000;

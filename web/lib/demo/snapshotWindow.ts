// Freshness window for snapshot-backed routes, widened for demo principals.
//
// Demo snapshot rows have no writer of their own: radon-demo-mirror.timer
// (cloud/services/radon-demo-mirror.timer, daily 21:45 UTC with a 23:30 retry)
// copies the latest prod rows into the demo Turso. A demo row is therefore as
// old as the prod row was at mirror time PLUS the time since the last mirror.
// The worst case is a weekday-only prod writer over a weekend: its Friday
// evening row stays newest until Monday evening (~72h), and the mirror can add
// up to 24h on top. 96h covers that. Anything older means the mirror itself is
// down, which demo users should see as stale like anyone else.
//
// Operator, authenticated, and test principals keep the route's own window
// unchanged; the helper only ever widens, never narrows.

import type { RoutePrincipal } from "@/lib/routeAccess";
import { requestPrincipalKind } from "./requestPrincipal";

export const DEMO_SNAPSHOT_MAX_AGE_MS = 96 * 60 * 60_000;

export function demoSnapshotMaxAgeMs(
  operatorMaxAgeMs: number,
  principalKind: RoutePrincipal["kind"] | null,
): number {
  return principalKind === "demo"
    ? Math.max(operatorMaxAgeMs, DEMO_SNAPSHOT_MAX_AGE_MS)
    : operatorMaxAgeMs;
}

/** Resolve the request's principal and return the window `dbFirstRead` should apply. */
export async function snapshotMaxAgeMs(operatorMaxAgeMs: number): Promise<number> {
  return demoSnapshotMaxAgeMs(operatorMaxAgeMs, await requestPrincipalKind());
}

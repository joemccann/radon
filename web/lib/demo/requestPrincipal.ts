// Principal kind for routes that rely on the middleware perimeter.
//
// No rate budget is passed, so a route gains no limiter it did not already
// have, and a denied resolution returns null so the caller keeps its operator
// behavior (middleware still owns the rejection).

import { requireRouteAccess, type RoutePrincipal } from "@/lib/routeAccess";

export async function requestPrincipalKind(): Promise<RoutePrincipal["kind"] | null> {
  const access = await requireRouteAccess();
  return access.ok ? access.principal.kind : null;
}

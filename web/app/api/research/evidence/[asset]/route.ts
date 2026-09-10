import { requireRouteAccess } from "@/lib/routeAccess";
import { RadonApiError, radonFetch } from "@/lib/radonApi";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const radonCapability = "read";
const headers = { "Cache-Control": "private, no-store", "Vary": "Cookie, Authorization", "X-Content-Type-Options": "nosniff" };

export async function GET(request: Request, context: { params: Promise<{ asset: string }> }) {
  const access = await requireRouteAccess(request, { operatorOnly: true });
  if (!access.ok) return access.response;
  const { asset } = await context.params;
  if (!/^[a-f0-9]{64}\.json$/.test(asset)) return Response.json({ error: "Evidence not found" }, { status: 404, headers });
  const query = new URL(request.url).searchParams.get("query");
  if (query !== null && (query.trim().length === 0 || query.length > 200)) {
    return Response.json({ error: "Query must contain 1..200 characters" }, { status: 400, headers });
  }
  try {
    const data = await radonFetch(`/newsfeed/research/evidence/${asset}${query === null ? "" : `?query=${encodeURIComponent(query)}`}`, {
      token: access.principal.token, cache: "no-store", signal: request.signal,
    });
    return Response.json(data, { headers });
  } catch (error) {
    const status = error instanceof RadonApiError ? error.status : 503;
    return Response.json({ error: "Research evidence unavailable" }, { status, headers });
  }
}

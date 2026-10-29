import { requireRouteAccess } from "@/lib/routeAccess";

export const runtime = "nodejs";
export const radonCapability = "internal";

/** Retained for stale clients after Newsfeed Share rewriting was retired. */
export async function POST(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, {
    operatorOnly: true,
    rate: { key: "newsfeed-share", limit: 10, windowMs: 60_000 },
    durableRateTier: "D",
  });
  if (!access.ok) return access.response;
  return Response.json(
    { error: "Newsfeed share rewriting has been retired." },
    { status: 410, headers: { "Cache-Control": "no-store" } },
  );
}

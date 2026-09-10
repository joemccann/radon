import { requireRouteAccess } from "@/lib/routeAccess";
import { radonFetchResponse, RadonApiError } from "@/lib/radonApi";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
// Binary private documents are not a chat call_api JSON surface.
export const radonCapability = "internal";
const privateHeaders = { "Cache-Control": "private, no-store", "Vary": "Cookie, Authorization", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox" };
export async function GET(request: Request, context: { params: Promise<{ asset: string }> }) {
  const access = await requireRouteAccess(request, { operatorOnly: true });
  if (!access.ok) return access.response;
  const { asset } = await context.params;
  if (!/^[a-f0-9]{64}\.(png|pdf|json)$/.test(asset)) {
    return new Response("Not found", { status: 404, headers: privateHeaders });
  }
  try {
    const upstream = await radonFetchResponse(`/newsfeed/research/files/${asset}`, {
      token: access.principal.token, cache: "no-store", signal: request.signal,
    });
    return new Response(upstream.body, {
      headers: { ...privateHeaders,
        "Content-Type": asset.endsWith(".json") ? "application/json" : asset.endsWith(".pdf") ? "application/pdf" : "image/png",
        "Content-Disposition": `inline; filename="${asset}"`,
      },
    });
  } catch (error) {
    const status = error instanceof RadonApiError ? error.status : 503;
    return new Response(status === 404 ? "Not found" : "Research media unavailable", { status, headers: privateHeaders });
  }
}

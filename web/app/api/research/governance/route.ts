import { requireRouteAccess } from "@/lib/routeAccess";
import { readGovernanceReport } from "@/lib/assistant/governance";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const radonCapability = "read";

export async function GET(request: Request) {
  const access = await requireRouteAccess(request, { operatorOnly: true });
  if (!access.ok) return access.response;
  return Response.json(await readGovernanceReport(), {
    headers: {
      "Cache-Control": "private, no-store",
      "Vary": "Cookie, Authorization",
      "X-Content-Type-Options": "nosniff",
      ...(new URL(request.url).searchParams.get("download") === "1"
        ? { "Content-Disposition": 'attachment; filename="radon-governance-audit.json"' } : {}),
    },
  });
}

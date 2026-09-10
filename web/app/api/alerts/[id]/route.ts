import { NextResponse } from "next/server";
import { dbExecute, describeDbError } from "@/lib/dbExecute";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { requireRouteAccess } from "@/lib/routeAccess";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export const radonCapability = "mutate.workspace";

export async function DELETE(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const requestId = getRequestId();
  const access = await requireRouteAccess(req, {
    rate: { key: "alerts-delete", limit: 20, windowMs: 60_000 },
    durableRateTier: "C",
  });
  if (!access.ok) return access.response;

  const { id } = await params;
  const ruleId = decodeURIComponent(id).trim();

  try {
    await dbExecute(
      {
        sql: `DELETE FROM alert_rules WHERE id = ? AND user_id = ?`,
        args: [ruleId, access.principal.userId],
      },
      { label: "alerts" },
    );
    return setNoStoreResponseHeaders(NextResponse.json({ ok: true }), requestId);
  } catch (err) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 503, code: "DB_UNAVAILABLE", message: "Alert store temporarily unavailable", detail: describeDbError(err), requestId }),
      requestId,
    );
  }
}

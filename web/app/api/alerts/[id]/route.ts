import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";
import { dbExecute, describeDbError } from "@/lib/dbExecute";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const requestId = getRequestId();
  const { userId } = await auth();
  if (!userId) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 401, code: "UNAUTHORIZED", message: "Sign in required", requestId }),
      requestId,
    );
  }

  const { id } = await params;
  const ruleId = decodeURIComponent(id).trim();

  try {
    await dbExecute(
      {
        sql: `DELETE FROM alert_rules WHERE id = ? AND user_id = ?`,
        args: [ruleId, userId],
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

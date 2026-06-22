import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";
import { getDb } from "@/lib/db";
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
    const db = getDb();
    await db.execute({
      sql: `DELETE FROM alert_rules WHERE id = ? AND user_id = ?`,
      args: [ruleId, userId],
    });
    return setNoStoreResponseHeaders(NextResponse.json({ ok: true }), requestId);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 500, code: "INTERNAL_ERROR", message: "Failed to delete alert rule", detail: message, requestId }),
      requestId,
    );
  }
}

import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";
import { dbExecute, describeDbError } from "@/lib/dbExecute";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ symbol: string }> },
): Promise<Response> {
  const requestId = getRequestId();
  const { userId } = await auth();
  if (!userId) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 401, code: "UNAUTHORIZED", message: "Sign in required", requestId }),
      requestId,
    );
  }

  const { symbol } = await params;
  const normalized = decodeURIComponent(symbol).trim().toUpperCase();

  try {
    await dbExecute(
      {
        sql: `DELETE FROM user_watchlist WHERE user_id = ? AND symbol = ?`,
        args: [userId, normalized],
      },
      { label: "watchlist" },
    );
    return setNoStoreResponseHeaders(NextResponse.json({ ok: true, watched: false }), requestId);
  } catch (err) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 503, code: "DB_UNAVAILABLE", message: "Watchlist store temporarily unavailable", detail: describeDbError(err), requestId }),
      requestId,
    );
  }
}

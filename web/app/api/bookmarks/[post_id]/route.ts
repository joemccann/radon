import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";
import { dbExecute, describeDbError } from "@/lib/dbExecute";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ post_id: string }> },
): Promise<Response> {
  const requestId = getRequestId();
  const { userId } = await auth();
  if (!userId) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 401, code: "UNAUTHORIZED", message: "Sign in required", requestId }),
      requestId,
    );
  }

  const { post_id } = await params;
  const postId = decodeURIComponent(post_id).trim();

  try {
    await dbExecute(
      {
        sql: `DELETE FROM bookmarks WHERE user_id = ? AND post_id = ?`,
        args: [userId, postId],
      },
      { label: "bookmarks" },
    );
    return setNoStoreResponseHeaders(NextResponse.json({ ok: true, bookmarked: false }), requestId);
  } catch (err) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 503, code: "DB_UNAVAILABLE", message: "Bookmark store temporarily unavailable", detail: describeDbError(err), requestId }),
      requestId,
    );
  }
}

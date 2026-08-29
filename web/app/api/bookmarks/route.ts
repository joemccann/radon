import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";
import { dbExecute, describeDbError } from "@/lib/dbExecute";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type BookmarkRow = {
  id: string;
  post_id: string;
  snapshot: string | null;
  saved_at: string;
};

function parseSnapshot(json: string | null): unknown {
  if (!json) return null;
  try {
    return JSON.parse(json);
  } catch {
    return null;
  }
}

function rowToBookmark(row: BookmarkRow) {
  return {
    id: row.id,
    post_id: row.post_id,
    snapshot: parseSnapshot(row.snapshot),
    saved_at: row.saved_at,
  };
}

export const radonCapability = { GET: "read", POST: "mutate.workspace" };

export async function GET(): Promise<Response> {
  const requestId = getRequestId();
  const { userId } = await auth();
  if (!userId) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 401, code: "UNAUTHORIZED", message: "Sign in required", requestId }),
      requestId,
    );
  }
  try {
    const result = await dbExecute(
      {
        sql: `SELECT id, post_id, snapshot, saved_at
            FROM bookmarks
            WHERE user_id = ?
            ORDER BY saved_at DESC`,
        args: [userId],
      },
      { label: "bookmarks" },
    );
    const bookmarks = result.rows.map((r) => rowToBookmark(r as unknown as BookmarkRow));
    return setNoStoreResponseHeaders(NextResponse.json({ bookmarks }), requestId);
  } catch (err) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 503, code: "DB_UNAVAILABLE", message: "Bookmark store temporarily unavailable", detail: describeDbError(err), requestId }),
      requestId,
    );
  }
}

export async function POST(req: Request): Promise<Response> {
  const requestId = getRequestId();
  const { userId } = await auth();
  if (!userId) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 401, code: "UNAUTHORIZED", message: "Sign in required", requestId }),
      requestId,
    );
  }

  let body: { post_id?: unknown; snapshot?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 400, code: "BAD_REQUEST", message: "Invalid JSON body", requestId }),
      requestId,
    );
  }

  if (typeof body.post_id !== "string" || body.post_id.trim().length === 0) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 400, code: "VALIDATION_ERROR", message: "post_id is required", requestId }),
      requestId,
    );
  }

  const postId = body.post_id.trim();
  const snapshot = body.snapshot === undefined || body.snapshot === null
    ? null
    : JSON.stringify(body.snapshot);

  try {
    await dbExecute(
      {
        sql: `INSERT OR IGNORE INTO bookmarks (id, user_id, post_id, snapshot, saved_at)
            VALUES (?, ?, ?, ?, datetime('now'))`,
        args: [crypto.randomUUID(), userId, postId, snapshot],
      },
      { label: "bookmarks" },
    );
    return setNoStoreResponseHeaders(NextResponse.json({ ok: true, bookmarked: true }), requestId);
  } catch (err) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 503, code: "DB_UNAVAILABLE", message: "Bookmark store temporarily unavailable", detail: describeDbError(err), requestId }),
      requestId,
    );
  }
}

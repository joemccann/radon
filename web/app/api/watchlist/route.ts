import { NextResponse } from "next/server";
import { auth } from "@clerk/nextjs/server";
import { dbExecute, describeDbError } from "@/lib/dbExecute";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type WatchlistRow = {
  id: string;
  symbol: string;
  sector: string | null;
  added_at: string;
};

function rowToWatch(row: WatchlistRow) {
  return {
    id: row.id,
    symbol: row.symbol,
    sector: row.sector ?? null,
    added_at: row.added_at,
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
        sql: `SELECT id, symbol, sector, added_at
            FROM user_watchlist
            WHERE user_id = ?
            ORDER BY added_at DESC`,
        args: [userId],
      },
      { label: "watchlist" },
    );
    const watchlist = result.rows.map((r) => rowToWatch(r as unknown as WatchlistRow));
    return setNoStoreResponseHeaders(NextResponse.json({ watchlist }), requestId);
  } catch (err) {
    // 503, not 500: a DB outage is transient and the client hook keeps its
    // last-good watchlist on any !res.ok.
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 503, code: "DB_UNAVAILABLE", message: "Watchlist store temporarily unavailable", detail: describeDbError(err), requestId }),
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

  let body: { symbol?: unknown; sector?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 400, code: "BAD_REQUEST", message: "Invalid JSON body", requestId }),
      requestId,
    );
  }

  if (typeof body.symbol !== "string" || body.symbol.trim().length === 0) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 400, code: "VALIDATION_ERROR", message: "symbol is required", requestId }),
      requestId,
    );
  }

  const symbol = body.symbol.trim().toUpperCase();
  const sector = typeof body.sector === "string" && body.sector.trim().length > 0
    ? body.sector.trim()
    : null;

  try {
    await dbExecute(
      {
        sql: `INSERT OR IGNORE INTO user_watchlist (id, user_id, symbol, sector, added_at)
            VALUES (?, ?, ?, ?, datetime('now'))`,
        args: [crypto.randomUUID(), userId, symbol, sector],
      },
      { label: "watchlist" },
    );
    return setNoStoreResponseHeaders(NextResponse.json({ ok: true, watched: true }), requestId);
  } catch (err) {
    return setNoStoreResponseHeaders(
      jsonApiError({ status: 503, code: "DB_UNAVAILABLE", message: "Watchlist store temporarily unavailable", detail: describeDbError(err), requestId }),
      requestId,
    );
  }
}

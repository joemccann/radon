import { NextRequest, NextResponse } from "next/server";
import { normalizeSymbolList, type PriceData, type WSMessage } from "@/lib/pricesProtocol";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const WS_SERVER_URL = process.env.IB_REALTIME_WS_URL || "ws://localhost:8765";

/**
 * GET /api/prices
 * 
 * Legacy SSE endpoint removed.
 */
export const radonCapability = { GET: "read", POST: "read" };

export async function GET(): Promise<Response> {
  return NextResponse.json(
    {
      error: "This endpoint is deprecated. Connect directly to the websocket server with ws://... rather than SSE."
    },
    { status: 405 }
  );
}

/**
 * POST /api/prices
 * 
 * Get a one-time snapshot of prices for the given symbols.
 */
export async function POST(): Promise<Response> {
  return NextResponse.json(
    {
      error: "This endpoint is deprecated. The client should connect directly to the websocket server for snapshots."
    },
    { status: 405 }
  );
}

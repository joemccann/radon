// TradingView alert webhook (docs/tradingview-integration.md, Phase 1).
//
// Handler order is the design:
//   1. path token mismatch, or a body secret that is present but wrong
//      -> 401, write nothing
//      A chart alert's default message has no secret. The path token is
//      the authenticator. Rejecting that message is a 401 TradingView
//      does not retry, so the fire is lost.
//   2. body over 16 KiB                   -> 413, write nothing
//   3. INSERT the raw body BEFORE any parse
//   4. parse; failure leaves parsed columns NULL and records parse_error
//   5. 200 for everything past step 1: TradingView never retries, so a
//      4xx/5xx after auth would only hide the loss.
// Nothing runs after the response: no Pushover, no FastAPI call, no symbol
// resolution. scripts/tv_alerts_drain.py digests the rows. Never places or
// routes an order.

import { NextResponse } from "next/server";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { dbExecute } from "@/lib/dbExecute";
import { clientIp } from "@/lib/rateLimit";
import {
  extractBodySecret,
  parseTvAlertBody,
  redactSecret,
  secretMatches,
  TV_WEBHOOK_MAX_BYTES,
} from "@/lib/tvWebhook";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const radonCapability = "internal";

// Two statements must fit inside TradingView's 3s request timeout.
const INSERT_TIMEOUT_MS = 1_500;
const UPDATE_TIMEOUT_MS = 1_000;

export async function POST(
  request: Request,
  { params }: { params: Promise<{ token: string }> },
): Promise<Response> {
  const requestId = getRequestId();
  const reply = (body: Record<string, unknown>, status = 200) =>
    setNoStoreResponseHeaders(NextResponse.json(body, { status }), requestId);
  const unauthorized = () => reply({ error: "Unauthorized.", requestId }, 401);

  const { token } = await params;
  if (!secretMatches(token, process.env.TV_WEBHOOK_PATH_TOKEN)) return unauthorized();

  const declared = Number(request.headers.get("content-length") ?? "0");
  if (declared > TV_WEBHOOK_MAX_BYTES) return reply({ error: "Payload too large.", requestId }, 413);
  const raw = await request.text();
  if (Buffer.byteLength(raw, "utf8") > TV_WEBHOOK_MAX_BYTES) {
    return reply({ error: "Payload too large.", requestId }, 413);
  }

  // Empty means the message did not carry a secret (TradingView's default
  // crossing text). A presented secret must match; a wrong one still 401s.
  const presentedSecret = extractBodySecret(raw);
  if (presentedSecret !== "" && !secretMatches(presentedSecret, process.env.TV_WEBHOOK_SECRET)) {
    return unauthorized();
  }

  let id: number;
  try {
    const inserted = await dbExecute(
      {
        sql: "INSERT INTO tv_alert_events (received_at, source_ip, raw_body) VALUES (?, ?, ?) RETURNING id",
        // Never persist the body secret (CWE-312): redact before the write.
        args: [new Date().toISOString(), clientIp(request), redactSecret(raw, process.env.TV_WEBHOOK_SECRET)],
      },
      { timeoutMs: INSERT_TIMEOUT_MS, label: "tv-webhook-insert" },
    );
    id = Number(inserted.rows[0]?.id);
  } catch (err) {
    console.error(`[tv-webhook] insert failed, alert lost: ${err instanceof Error ? err.message : String(err)}`);
    return reply({ stored: false, requestId });
  }

  const { fields, error } = parseTvAlertBody(raw);
  try {
    await dbExecute(
      {
        sql:
          "UPDATE tv_alert_events SET symbol = ?, exchange = ?, price = ?, interval = ?, " +
          "alert_name = ?, bar_time = ?, sent_at = ?, parse_error = ? WHERE id = ?",
        args: [
          fields?.symbol ?? null,
          fields?.exchange ?? null,
          fields?.price ?? null,
          fields?.interval ?? null,
          fields?.alert_name ?? null,
          fields?.bar_time ?? null,
          fields?.sent_at ?? null,
          error,
          id,
        ],
      },
      { timeoutMs: UPDATE_TIMEOUT_MS, label: "tv-webhook-parse" },
    );
  } catch (err) {
    // The raw row is already stored; the drain can still count it.
    console.warn(`[tv-webhook] parse update failed for id=${id}: ${err instanceof Error ? err.message : String(err)}`);
  }
  return reply({ stored: true, requestId });
}

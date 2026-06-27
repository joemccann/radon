import { NextResponse } from "next/server";
import { RadonApiError, radonFetch } from "@/lib/radonApi";
import {
  EMPTY_ORDERS,
  readOrdersSnapshotFromDb,
} from "@/lib/orders/readOrdersFromDb";
import {
  getRequestId,
  jsonApiError,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import {
  firstPlaceOrderSchemaErrorMessage,
  normalizeOptionRight,
} from "@/lib/placeOrderBodySchema";
import { getMarketStateFromDate } from "@/lib/serviceHealthWindows";
import { resolveDemoOrderDecision } from "@/lib/demo/orderBlockade";

export const runtime = "nodejs";

type ComboLeg = {
  expiry: string;
  strike: number;
  right: "C" | "P";
  action: "BUY" | "SELL";
  ratio: number;
  limitPrice?: number;
};

type PlaceBody = {
  type: "stock" | "option" | "combo" | "future";
  symbol: string;
  action: "BUY" | "SELL";
  quantity: number;
  limitPrice: number;
  tif?: "DAY" | "GTC";
  /** Allow the order to fill OUTSIDE regular trading hours. Omitted → the route
   *  auto-enables it when the market is not in RTH so after-hours orders work. */
  outsideRth?: boolean;
  expiry?: string;
  strike?: number;
  right?: "C" | "P";
  legs?: ComboLeg[];
  /** Futures: caller passes IB conId (preferred — from /futures/chain) or expiry+exchange. */
  conId?: number;
  exchange?: string;
};

async function readOrdersSnapshotBestEffort() {
  try {
    return await readOrdersSnapshotFromDb();
  } catch {
    return EMPTY_ORDERS;
  }
}

export async function POST(request: Request): Promise<Response> {
  const requestId = getRequestId();
  try {
    let parsed: unknown;
    try {
      parsed = await request.json();
    } catch {
      return setNoStoreResponseHeaders(
        jsonApiError({
          message: "Invalid JSON body",
          status: 400,
          code: "BAD_REQUEST",
          requestId,
        }),
        requestId,
      );
    }

    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return setNoStoreResponseHeaders(
        jsonApiError({
          message: "Request body must be a JSON object",
          status: 400,
          code: "BAD_REQUEST",
          requestId,
        }),
        requestId,
      );
    }

    const schemaErr = firstPlaceOrderSchemaErrorMessage(parsed);
    if (schemaErr) {
      return setNoStoreResponseHeaders(
        jsonApiError({
          message: schemaErr,
          status: 400,
          code: "VALIDATION_ERROR",
          requestId,
        }),
        requestId,
      );
    }

    const body = parsed as PlaceBody;
    body.type = body.type ?? "stock";

    // Demo blockade (Phase 3): a demo user's order never reaches the IB path —
    // it is forwarded to the paper-fill engine. Active demo → paper; expired →
    // 403 (the middleware also blocks expired demo users; this is defense in
    // depth). Non-demo users fall straight through to the real path below.
    const demoDecision = await resolveDemoOrderDecision();
    if (demoDecision.action === "block-expired") {
      return setNoStoreResponseHeaders(
        jsonApiError({
          message: "Demo trial expired — order not placed.",
          status: 403,
          code: "UNAUTHORIZED",
          requestId,
        }),
        requestId,
      );
    }
    if (demoDecision.action === "paper") {
      const paperResult = await radonFetch<Record<string, unknown>>(
        "/paper/place",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          timeout: 30_000,
        },
      );
      const orders = await readOrdersSnapshotBestEffort();
      return setNoStoreResponseHeaders(
        NextResponse.json({ ...paperResult, demo: true, paper: true, orders, requestId }),
        requestId,
      );
    }

    // Chain UI sends CALL/PUT; IB + naked-short guard expect C/P
    if (body.type === "option" && body.right != null) {
      body.right = normalizeOptionRight(body.right as unknown as string);
    }
    if (body.type === "combo" && body.legs) {
      body.legs = body.legs.map((leg) => ({
        ...leg,
        right: normalizeOptionRight(leg.right as unknown as string),
      }));
    }

    // Required fields (schema ensures presence; trim rejects whitespace-only symbol)
    if (!body.symbol?.trim() || !body.action) {
      return setNoStoreResponseHeaders(
        jsonApiError({
          message: "Required: symbol, action, quantity, limitPrice",
          status: 400,
          code: "BAD_REQUEST",
          requestId,
        }),
        requestId,
      );
    }

    // Validate quantity: must be positive integer
    if (body.quantity == null || body.quantity <= 0 || !Number.isFinite(body.quantity)) {
      return setNoStoreResponseHeaders(
        jsonApiError({
          message: "quantity must be a positive number",
          status: 400,
          code: "BAD_REQUEST",
          requestId,
        }),
        requestId,
      );
    }

    // Signed combo prices are valid: IB combo pricing preserves credit/debit sign.
    // Single-leg stock/option orders must remain strictly positive.
    const comboSignedPrice = body.type === "combo";
    const limitPriceInvalid = comboSignedPrice
      ? body.limitPrice == null || body.limitPrice === 0 || !Number.isFinite(body.limitPrice)
      : body.limitPrice == null || body.limitPrice <= 0 || !Number.isFinite(body.limitPrice);
    if (limitPriceInvalid) {
      return setNoStoreResponseHeaders(
        jsonApiError({
          message: comboSignedPrice
            ? "combo limitPrice must be a non-zero number"
            : "limitPrice must be a positive number",
          status: 400,
          code: "BAD_REQUEST",
          requestId,
        }),
        requestId,
      );
    }

    if (body.type === "option" && (!body.expiry || !body.strike || !body.right)) {
      return setNoStoreResponseHeaders(
        jsonApiError({
          message: "Options require: expiry, strike, right",
          status: 400,
          code: "BAD_REQUEST",
          requestId,
        }),
        requestId,
      );
    }

    if (body.type === "combo" && (!body.legs || body.legs.length < 2)) {
      return setNoStoreResponseHeaders(
        jsonApiError({
          message: "Combo orders require 'legs' array with 2+ entries",
          status: 400,
          code: "BAD_REQUEST",
          requestId,
        }),
        requestId,
      );
    }

    const orderPayload = {
      type: body.type || "stock",
      symbol: body.symbol.toUpperCase(),
      action: body.action,
      quantity: body.quantity,
      limitPrice: body.limitPrice,
      tif: body.tif || "DAY",
      // Auto-enable extended-hours (outsideRth) when the market is NOT in RTH so
      // after-hours orders actually work instead of IB holding them to the next
      // open. An explicit caller value wins. Applies the same to extended/closed
      // (a closed-session order becomes eligible for the next extended session).
      outsideRth: body.outsideRth ?? getMarketStateFromDate() !== "open",
      ...(body.type === "option"
        ? {
            expiry: body.expiry,
            strike: body.strike,
            right: body.right,
            // Index options need conId + exchange="CBOE" to disambiguate
            // from weeklies (VIXW) and related roots. The chain endpoint
            // hands these back.
            ...(body.conId != null ? { conId: body.conId } : {}),
            ...(body.exchange ? { exchange: body.exchange } : {}),
          }
        : {}),
      ...(body.type === "future"
        ? {
            // Futures: prefer conId (unambiguous, from /futures/chain).
            // Falls back to expiry+exchange if the chain wasn't called.
            ...(body.conId != null ? { conId: body.conId } : {}),
            ...(body.expiry ? { expiry: body.expiry } : {}),
            ...(body.exchange ? { exchange: body.exchange } : {}),
          }
        : {}),
      ...(body.type === "combo" && body.legs
        ? {
            legs: body.legs.map((l) => ({
              expiry: l.expiry,
              strike: l.strike,
              right: l.right,
              action: l.action,
              ratio: l.ratio,
              ...(l.limitPrice != null ? { limitPrice: l.limitPrice } : {}),
            })),
          }
        : {}),
    };

    const orderResult = await radonFetch<Record<string, unknown>>("/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(orderPayload),
      // 30s = 25s FastAPI script budget + 5s network/transport slack.
      // ib_place_order.py polls IB up to 12s for combo confirmation
      // before returning either ok-with-permId or the explicit
      // "stuck-in-PendingSubmit" error. Setting this below the
      // script timeout would abort before the error could surface.
      timeout: 30_000,
    });

    // IB silent rejection: order was submitted but immediately cancelled/inactive.
    const REJECTED_STATUSES = new Set(["Cancelled", "ApiCancelled", "Inactive", "Unknown"]);
    const initialStatus = orderResult.initialStatus as string | undefined;
    if (initialStatus && REJECTED_STATUSES.has(initialStatus)) {
      const reason = initialStatus === "Unknown"
        ? `no acknowledgement (${initialStatus}) — order may not have reached IB`
        : initialStatus;
      return setNoStoreResponseHeaders(
        jsonApiError({
          message: `Order rejected by IB: ${reason}`,
          status: 502,
          code: "UPSTREAM_ERROR",
          detail: JSON.stringify(orderResult),
          requestId,
        }),
        requestId,
      );
    }

    // Refresh orders after placement
    try {
      await radonFetch("/orders/refresh", { method: "POST", timeout: 10_000 });
    } catch {
      // Non-fatal — order was placed, refresh failed
    }
    const orders = await readOrdersSnapshotBestEffort();

    const response = NextResponse.json({
      status: "ok",
      orderId: orderResult.orderId,
      permId: orderResult.permId,
      initialStatus: orderResult.initialStatus,
      message: orderResult.message,
      orders,
      requestId,
    });
    return setNoStoreResponseHeaders(response, requestId);
  } catch (error) {
    if (error instanceof RadonApiError) {
      return setNoStoreResponseHeaders(
        jsonApiError({
          message: error.detail,
          status: error.status,
          code: error.status >= 500 ? "UPSTREAM_ERROR" : undefined,
          requestId,
        }),
        requestId,
      );
    }
    const message = error instanceof Error ? error.message : "Order placement failed";
    return setNoStoreResponseHeaders(
      jsonApiError({
        message,
        status: 500,
        code: "INTERNAL_ERROR",
        requestId,
      }),
      requestId,
    );
  }
}

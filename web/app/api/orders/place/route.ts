import { requireRouteAccess } from "@/lib/routeAccess";

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
import {
  AUTH_UNAVAILABLE_MESSAGE,
  resolveDemoOrderDecision,
} from "@/lib/demo/orderBlockade";
import {
  runIdempotentOrder,
  contentKey,
  CONTENT_HASH_TTL_MS,
  CLIENT_KEY_TTL_MS,
  IndeterminatePlacementError,
} from "@/lib/orders/orderIdempotency";

export const runtime = "nodejs";

/** Raised inside the idempotent placement when IB silently rejects the order, so
 *  the key is cleared (rejection is retryable) and the outer handler renders the
 *  502 envelope. */
class OrderRejectedError extends Error {
  constructor(
    readonly result: Record<string, unknown>,
    readonly initialStatus: string,
  ) {
    super(`Order rejected by IB: ${initialStatus}`);
    this.name = "OrderRejectedError";
  }
}

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
  limitPrice?: number;
  orderType?: "LMT" | "STP" | "STP LMT";
  stopPrice?: number;
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
  /** Optional client idempotency key (see placeOrderBodySchema). */
  idempotencyKey?: string;
};

async function readOrdersSnapshotBestEffort() {
  try {
    return await readOrdersSnapshotFromDb();
  } catch {
    return EMPTY_ORDERS;
  }
}

/** Operator instruction for an order whose fate at IB is unknown. Never phrased
 *  as a plain failure — a plain failure invites the retry that double-places. */
const INDETERMINATE_PLACEMENT_MESSAGE =
  "Order status unknown: the request to IB timed out. The order may have reached IB. Check open orders before re-placing.";

function indeterminatePlacementDetail(error: IndeterminatePlacementError): string {
  if (error.deduplicated) {
    return "A duplicate submit inside the idempotency window returned the first attempt's indeterminate result. The order was NOT sent again.";
  }
  const cause = error.cause;
  const reason =
    cause instanceof Error ? `${cause.name}: ${cause.message}` : "upstream request aborted";
  return `Placement was not confirmed (${reason}). The idempotency key is held so an identical resubmit cannot double-place.`;
}

export async function POST(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, {
    operatorOnly: true,
    demoBlockadeRoute: true,
    rate: { key: "orders/place:route", limit: 5, windowMs: 60_000 },
    durableRateTier: "C",
  });
  if (!access.ok) return access.response;
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
    //
    // Cohort UNKNOWN (Clerk threw) → 503, never the real path (T-018). The
    // order is explicitly refused rather than placed on a guess: silently
    // routing an unknown caller to IB is the exact failure this gate exists to
    // prevent, and silently papering a real operator's order is no better.
    const demoDecision = await resolveDemoOrderDecision();
    if (demoDecision.action === "auth-unavailable") {
      return setNoStoreResponseHeaders(
        jsonApiError({
          message: `${AUTH_UNAVAILABLE_MESSAGE} Order not placed. Retry in a moment.`,
          status: 503,
          code: "UPSTREAM_ERROR",
          requestId,
        }),
        requestId,
      );
    }
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
          message: "Required: symbol, action, quantity",
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
    // STP uses stopPrice only; STP LMT needs both. Combo + STP LMT is not an IB product.
    const orderType = body.orderType ?? "LMT";
    const comboSignedPrice = body.type === "combo";
    if (orderType === "STP LMT" && comboSignedPrice) {
      return setNoStoreResponseHeaders(
        jsonApiError({
          message: "STP LMT is not supported on combo orders",
          status: 400,
          code: "BAD_REQUEST",
          requestId,
        }),
        requestId,
      );
    }
    const stopInvalid =
      (orderType === "STP" || orderType === "STP LMT")
      && (body.stopPrice == null || body.stopPrice <= 0 || !Number.isFinite(body.stopPrice));
    if (stopInvalid) {
      return setNoStoreResponseHeaders(
        jsonApiError({
          message: "stopPrice must be a positive number",
          status: 400,
          code: "BAD_REQUEST",
          requestId,
        }),
        requestId,
      );
    }
    const limitRequired = orderType !== "STP";
    const limitPriceInvalid = limitRequired && (comboSignedPrice
      ? body.limitPrice == null || body.limitPrice === 0 || !Number.isFinite(body.limitPrice)
      : body.limitPrice == null || body.limitPrice <= 0 || !Number.isFinite(body.limitPrice));
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
      ...(orderType !== "LMT" ? { orderType } : {}),
      ...(body.stopPrice != null ? { stopPrice: body.stopPrice } : {}),
      ...(body.limitPrice != null ? { limitPrice: body.limitPrice } : {}),
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

    // Idempotency: an explicit client key (precise, long TTL) or a content hash
    // of the order payload (short TTL) dedups double-clicks / client retries so
    // the same real-money order is never placed twice. See orderIdempotency.ts.
    const clientKey =
      typeof body.idempotencyKey === "string" && body.idempotencyKey.trim()
        ? body.idempotencyKey.trim().slice(0, 200)
        : null;
    const principalKey = access.principal.userId;
    const idemKey = clientKey
      ? `u:${principalKey}:k:${clientKey}`
      : `u:${principalKey}:${contentKey(orderPayload)}`;
    const idemTtl = clientKey ? CLIENT_KEY_TTL_MS : CONTENT_HASH_TTL_MS;

    let placement;
    try {
      placement = await runIdempotentOrder(idemKey, idemTtl, async () => {
        const result = await radonFetch<Record<string, unknown>>("/orders/place", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(orderPayload),
          // 30s = 25s FastAPI script budget + 5s network/transport slack.
          // ib_place_order.py polls IB up to 12s for combo confirmation before
          // returning either ok-with-permId or the explicit
          // "stuck-in-PendingSubmit" error. Below the script timeout would abort
          // before the error could surface.
          timeout: 30_000,
        });
        // IB silent rejection: submitted but immediately cancelled/inactive.
        const REJECTED_STATUSES = new Set(["Cancelled", "ApiCancelled", "Inactive", "Unknown"]);
        const status = result.initialStatus as string | undefined;
        if (status && REJECTED_STATUSES.has(status)) {
          throw new OrderRejectedError(result, status);
        }
        return result;
      });
    } catch (placeErr) {
      if (placeErr instanceof OrderRejectedError) {
        const reason =
          placeErr.initialStatus === "Unknown"
            ? `no acknowledgement (${placeErr.initialStatus}) — order may not have reached IB`
            : placeErr.initialStatus;
        return setNoStoreResponseHeaders(
          jsonApiError({
            message: `Order rejected by IB: ${reason}`,
            status: 502,
            code: "UPSTREAM_ERROR",
            detail: JSON.stringify(placeErr.result),
            requestId,
          }),
          requestId,
        );
      }
      if (placeErr instanceof IndeterminatePlacementError) {
        // The 30s client abort fires after FastAPI has the request (25s budget),
        // so IB may already hold a live order. The generic 500 INTERNAL_ERROR
        // envelope reads as "nothing happened" and invites the resubmit that
        // double-places — answer with the ambiguity, explicitly.
        return setNoStoreResponseHeaders(
          jsonApiError({
            message: INDETERMINATE_PLACEMENT_MESSAGE,
            status: 504,
            code: "UPSTREAM_TIMEOUT_ORDER_INDETERMINATE",
            detail: indeterminatePlacementDetail(placeErr),
            requestId,
          }),
          requestId,
        );
      }
      throw placeErr; // RadonApiError / others → outer catch
    }
    const orderResult = placement.value;

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
      // Flag suppressed duplicates so the UI/operator can see a double-submit was
      // collapsed rather than silently dropped.
      ...(placement.deduplicated ? { deduplicated: true } : {}),
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

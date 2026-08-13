import { requireRouteAccess } from "@/lib/routeAccess";

import { NextResponse } from "next/server";
import { RadonApiError, radonFetch } from "@/lib/radonApi";
import {
  getRequestId,
  jsonApiError,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import {
  firstPlaceOrderSchemaErrorMessage,
  normalizeOptionRight,
} from "@/lib/placeOrderBodySchema";
import {
  AUTH_UNAVAILABLE_MESSAGE,
  resolveDemoOrderDecision,
} from "@/lib/demo/orderBlockade";

/**
 * POST /api/orders/whatif — read-only IB margin preview.
 *
 * Same order body shape as /orders/place, but it NEVER routes an order. It
 * forwards to FastAPI POST /orders/whatif → ib_place_order.py --whatif →
 * IB whatIfOrder, and returns the real initMargin (used to fill the
 * "UNAVAILABLE — IB what-if required" gap for undefined-risk combos). No orders
 * snapshot, no /orders/refresh, no rejection check — there is no order.
 */
export const runtime = "nodejs";

type ComboLeg = {
  expiry: string;
  strike: number;
  right: "C" | "P";
  action: "BUY" | "SELL";
  ratio: number;
  limitPrice?: number;
};

type WhatIfBody = {
  type: "stock" | "option" | "combo" | "future";
  symbol: string;
  action: "BUY" | "SELL";
  quantity: number;
  limitPrice: number;
  tif?: "DAY" | "GTC";
  outsideRth?: boolean;
  expiry?: string;
  strike?: number;
  right?: "C" | "P";
  legs?: ComboLeg[];
  conId?: number;
  exchange?: string;
};

const UNAVAILABLE = { initMargin: null, maintMargin: null, source: "unavailable" as const };

export async function POST(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, {
    operatorOnly: true,
    rate: { key: "orders/whatif:route", limit: 10, windowMs: 60_000 },
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
        jsonApiError({ message: "Invalid JSON body", status: 400, code: "BAD_REQUEST", requestId }),
        requestId,
      );
    }

    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return setNoStoreResponseHeaders(
        jsonApiError({ message: "Request body must be a JSON object", status: 400, code: "BAD_REQUEST", requestId }),
        requestId,
      );
    }

    const schemaErr = firstPlaceOrderSchemaErrorMessage(parsed);
    if (schemaErr) {
      return setNoStoreResponseHeaders(
        jsonApiError({ message: schemaErr, status: 400, code: "VALIDATION_ERROR", requestId }),
        requestId,
      );
    }

    const body = parsed as WhatIfBody;
    body.type = body.type ?? "stock";

    // Demo users have no live IB account — the paper engine computes no IB
    // margin, so surface "unavailable" rather than forwarding anywhere. Same
    // for an UNKNOWN cohort (Clerk threw): a preview is informational, so it
    // degrades instead of reaching the operator's live account on a guess
    // (T-018). Anything that is not an explicit `allow` stops here.
    const demoDecision = await resolveDemoOrderDecision();
    if (demoDecision.action !== "allow") {
      return setNoStoreResponseHeaders(
        NextResponse.json({
          ...UNAVAILABLE,
          warning:
            demoDecision.action === "auth-unavailable"
              ? `Margin preview unavailable. ${AUTH_UNAVAILABLE_MESSAGE}`
              : "Margin preview requires a live IB account",
          requestId,
        }),
        requestId,
      );
    }

    if (body.type === "option" && body.right != null) {
      body.right = normalizeOptionRight(body.right as unknown as string);
    }
    if (body.type === "combo" && body.legs) {
      body.legs = body.legs.map((leg) => ({
        ...leg,
        right: normalizeOptionRight(leg.right as unknown as string),
      }));
    }

    if (!body.symbol?.trim() || !body.action) {
      return setNoStoreResponseHeaders(
        jsonApiError({ message: "Required: symbol, action, quantity, limitPrice", status: 400, code: "BAD_REQUEST", requestId }),
        requestId,
      );
    }
    if (body.type === "combo" && (!body.legs || body.legs.length < 2)) {
      return setNoStoreResponseHeaders(
        jsonApiError({ message: "Combo orders require 'legs' array with 2+ entries", status: 400, code: "BAD_REQUEST", requestId }),
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
      ...(body.type === "option"
        ? {
            expiry: body.expiry,
            strike: body.strike,
            right: body.right,
            ...(body.conId != null ? { conId: body.conId } : {}),
            ...(body.exchange ? { exchange: body.exchange } : {}),
          }
        : {}),
      ...(body.type === "future"
        ? {
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

    const data = await radonFetch<Record<string, unknown>>("/orders/whatif", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(orderPayload),
      // 15s = 12s FastAPI script budget (connect+qualify+whatIf, no confirm-poll)
      // + 3s transport slack.
      timeout: 15_000,
    });

    const response = NextResponse.json({
      initMargin: data.initMargin ?? null,
      maintMargin: data.maintMargin ?? null,
      commission: data.commission ?? null,
      commissionCurrency: data.commissionCurrency ?? null,
      warning: data.warning ?? null,
      source: data.source ?? "ib",
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
    const message = error instanceof Error ? error.message : "What-if margin preview failed";
    return setNoStoreResponseHeaders(
      jsonApiError({ message, status: 500, code: "INTERNAL_ERROR", requestId }),
      requestId,
    );
  }
}

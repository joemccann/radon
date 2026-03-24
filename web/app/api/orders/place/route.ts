import { NextResponse } from "next/server";
import { readDataFile } from "@tools/data-reader";
import { OrdersData } from "@tools/schemas/ib-orders";
import { radonFetch } from "@/lib/radonApi";
import { checkNakedShortRisk } from "@/lib/nakedShortGuard";
import type { NakedShortPortfolio } from "@/lib/nakedShortGuard";
import {
  getRequestId,
  jsonApiError,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import {
  firstPlaceOrderSchemaErrorMessage,
  normalizeOptionRight,
} from "@/lib/placeOrderBodySchema";

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
  type: "stock" | "option" | "combo";
  symbol: string;
  action: "BUY" | "SELL";
  quantity: number;
  limitPrice: number;
  tif?: "DAY" | "GTC";
  expiry?: string;
  strike?: number;
  right?: "C" | "P";
  legs?: ComboLeg[];
};

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

    // Naked short guard — block orders that would create naked short exposure
    const portfolioResult = await readDataFile("data/portfolio.json");
    if (portfolioResult?.ok) {
      const guard = checkNakedShortRisk(body, portfolioResult.data as NakedShortPortfolio);
      if (!guard.allowed) {
        return setNoStoreResponseHeaders(
          jsonApiError({
            message: `Naked short blocked: ${guard.reason}`,
            status: 403,
            code: "VALIDATION_ERROR",
            requestId,
          }),
          requestId,
        );
      }
    } else {
      console.warn("[orders/place] Could not load portfolio for naked short guard:", portfolioResult?.error ?? "unknown error");
    }

    const orderPayload = {
      type: body.type || "stock",
      symbol: body.symbol.toUpperCase(),
      action: body.action,
      quantity: body.quantity,
      limitPrice: body.limitPrice,
      tif: body.tif || "DAY",
      ...(body.type === "option" ? { expiry: body.expiry, strike: body.strike, right: body.right } : {}),
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
      timeout: 20_000,
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
    const ordersResult = await readDataFile("data/orders.json", OrdersData);

    const response = NextResponse.json({
      status: "ok",
      orderId: orderResult.orderId,
      permId: orderResult.permId,
      initialStatus: orderResult.initialStatus,
      message: orderResult.message,
      orders: ordersResult.ok ? ordersResult.data : null,
      requestId,
    });
    return setNoStoreResponseHeaders(response, requestId);
  } catch (error) {
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

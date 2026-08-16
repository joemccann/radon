/**
 * Client-safe interpretation of a 2xx POST /api/orders/place response
 * (REL-027 / R-051).
 *
 * The route collapses a duplicate submit inside the idempotency window into
 * the FIRST order's result and flags it `deduplicated: true`. Rendering that
 * as a plain "Order placed" success is the half-position bug: the operator
 * believes two orders are live. Every order-entry surface must route its
 * place response through here so a suppressed submit renders as a
 * warning-toned, explicitly-worded state instead.
 *
 * Must stay importable from client components: no node:* imports and no
 * dependency on `orderIdempotency.ts` (which pulls node:fs).
 */

export type PlaceOrderTone = "success" | "warning";

export interface PlaceOrderFeedback {
  tone: PlaceOrderTone;
  deduplicated: boolean;
  message: string;
}

export function placeOrderFeedback(
  response: unknown,
  placedMessage: string,
): PlaceOrderFeedback {
  if (!isDeduplicated(response)) {
    return { tone: "success", deduplicated: false, message: placedMessage };
  }
  return {
    tone: "warning",
    deduplicated: true,
    message: suppressedSubmitMessage(response),
  };
}

function isDeduplicated(response: unknown): response is Record<string, unknown> {
  return (
    typeof response === "object" &&
    response !== null &&
    (response as { deduplicated?: unknown }).deduplicated === true
  );
}

function suppressedSubmitMessage(response: Record<string, unknown>): string {
  const orderId = response.orderId;
  const ref =
    typeof orderId === "number" || typeof orderId === "string"
      ? ` (order #${orderId})`
      : "";
  return (
    "Duplicate submit suppressed: this matches an order placed moments ago" +
    `${ref}. It was NOT sent again. Check open orders before resubmitting.`
  );
}

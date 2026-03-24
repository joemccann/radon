import { Type, type Static } from "@sinclair/typebox";
import { Value } from "@sinclair/typebox/value";

/** IB + chain UI use C/P; OrderBuilder sends CALL/PUT for HTTP clarity — both allowed here. */
export const OptionRightSchema = Type.Union([
  Type.Literal("C"),
  Type.Literal("P"),
  Type.Literal("CALL"),
  Type.Literal("PUT"),
]);

/** Combo leg as submitted to POST /api/orders/place */
export const PlaceOrderComboLegSchema = Type.Object({
  expiry: Type.String(),
  strike: Type.Number(),
  right: OptionRightSchema,
  action: Type.Union([Type.Literal("BUY"), Type.Literal("SELL")]),
  ratio: Type.Number(),
  limitPrice: Type.Optional(Type.Number()),
  /** Echoed from chain builder — ignored by IB bridge after normalization */
  symbol: Type.Optional(Type.String()),
  secType: Type.Optional(Type.String()),
});

/** Top-level place-order body (structural validation only — business rules stay in the route). */
export const PlaceOrderBodySchema = Type.Object({
  type: Type.Optional(Type.Union([
    Type.Literal("stock"),
    Type.Literal("option"),
    Type.Literal("combo"),
  ])),
  symbol: Type.String({ minLength: 1 }),
  action: Type.Union([Type.Literal("BUY"), Type.Literal("SELL")]),
  quantity: Type.Number(),
  limitPrice: Type.Number(),
  tif: Type.Optional(Type.Union([Type.Literal("DAY"), Type.Literal("GTC")])),
  expiry: Type.Optional(Type.String()),
  strike: Type.Optional(Type.Number()),
  right: Type.Optional(OptionRightSchema),
  legs: Type.Optional(Type.Array(PlaceOrderComboLegSchema)),
});

export type PlaceOrderBodyValidated = Static<typeof PlaceOrderBodySchema>;

/** Normalize to IB `C` / `P` for guards and `ib_place_order.py`. */
export function normalizeOptionRight(r: string): "C" | "P" {
  const u = String(r).toUpperCase();
  if (u === "C" || u === "CALL") return "C";
  if (u === "P" || u === "PUT") return "P";
  throw new Error(`Invalid option right: ${r}`);
}

export function firstPlaceOrderSchemaErrorMessage(raw: unknown): string | null {
  if (!Value.Check(PlaceOrderBodySchema, raw)) {
    const first = [...Value.Errors(PlaceOrderBodySchema, raw)][0];
    if (!first) return "Invalid request body";
    const segment = first.path.replace(/^\//, "").split("/")[0];
    if (segment) return `${segment}: ${first.message}`;
    return first.message;
  }
  return null;
}

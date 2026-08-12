/** IB TWS order types Radon can place for single-leg protective stops. */

export type IbOrderType = "LMT" | "STP" | "STP LMT";

export const IB_ORDER_TYPES: readonly IbOrderType[] = ["LMT", "STP", "STP LMT"];

export function parseIbOrderType(raw: unknown): IbOrderType {
  const normalized = String(raw ?? "LMT").trim().toUpperCase().replace(/[_-]+/g, " ");
  if (normalized === "STP" || normalized === "STOP" || normalized === "STOP MARKET") return "STP";
  if (
    normalized === "STP LMT"
    || normalized === "STOP LIMIT"
    || normalized === "STPLMT"
    || normalized === "STOP LMT"
  ) {
    return "STP LMT";
  }
  return "LMT";
}

export function isStopOrderType(orderType: IbOrderType): boolean {
  return orderType === "STP" || orderType === "STP LMT";
}

export function pricesValidForOrderType(args: {
  orderType: IbOrderType;
  limitPrice: number;
  stopPrice: number;
  comboSigned?: boolean;
}): boolean {
  const stopOk = Number.isFinite(args.stopPrice) && args.stopPrice > 0;
  const limitOk = args.comboSigned
    ? Number.isFinite(args.limitPrice) && args.limitPrice !== 0
    : Number.isFinite(args.limitPrice) && args.limitPrice > 0;
  if (args.orderType === "STP") return stopOk;
  if (args.orderType === "STP LMT") return stopOk && limitOk;
  return limitOk;
}

/** Price fed to risk math: trigger for STP, worst fill for STP LMT / LMT. */
export function riskPriceForOrderType(
  orderType: IbOrderType,
  limitPrice: number,
  stopPrice: number,
): number {
  return orderType === "STP" ? stopPrice : limitPrice;
}

export function ibPlaceFields(
  orderType: IbOrderType,
  limitPrice: number,
  stopPrice: number,
): Record<string, unknown> {
  if (orderType === "STP") return { orderType: "STP", stopPrice };
  if (orderType === "STP LMT") {
    return { orderType: "STP LMT", stopPrice, limitPrice };
  }
  return { limitPrice };
}

export function paperPlaceFields(
  orderType: IbOrderType,
  limitPrice: number,
  stopPrice: number,
): Record<string, unknown> {
  if (orderType === "STP" || orderType === "STP LMT") {
    return {
      order_type: "STOP",
      stop_price: stopPrice,
      ...(orderType === "STP LMT" ? { limit_price: limitPrice } : {}),
    };
  }
  return { order_type: "LIMIT", limit_price: limitPrice };
}

export function orderTypeLabel(orderType: IbOrderType): string {
  if (orderType === "STP") return "Stop";
  if (orderType === "STP LMT") return "Stop Limit";
  return "Limit";
}

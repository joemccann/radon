import { Type, type Static } from "@sinclair/typebox";

// ── Input (maps to argparse) ──────────────────────────────────────────

export const IBOrdersInput = Type.Object({
  host: Type.Optional(Type.String({ description: "TWS/Gateway host" })),
  port: Type.Optional(Type.Number({ description: "TWS/Gateway port" })),
  clientId: Type.Optional(Type.Number({ description: "Client ID" })),
  sync: Type.Optional(Type.Boolean({ description: "Sync orders into Turso" })),
});

export type IBOrdersInput = Static<typeof IBOrdersInput>;

// ── Output (matches Turso orders snapshot shape) ──────────────────────

const OrderComboLeg = Type.Object({
  conId: Type.Number(),
  ratio: Type.Number(),
  action: Type.String(),
  symbol: Type.Optional(Type.String()),
  strike: Type.Optional(Type.Union([Type.Number(), Type.Null()])),
  right: Type.Optional(Type.Union([Type.String(), Type.Null()])),
  expiry: Type.Optional(Type.Union([Type.String(), Type.Null()])),
});

const OrderContract = Type.Object({
  conId: Type.Union([Type.Number(), Type.Null()]),
  symbol: Type.String(),
  secType: Type.String(),
  strike: Type.Union([Type.Number(), Type.Null()]),
  right: Type.Union([Type.String(), Type.Null()]),
  expiry: Type.Union([Type.String(), Type.Null()]),
  comboLegs: Type.Optional(Type.Array(OrderComboLeg)),
});

const OpenOrder = Type.Object({
  orderId: Type.Number(),
  permId: Type.Number(),
  symbol: Type.String(),
  contract: OrderContract,
  action: Type.String(),
  orderType: Type.String(),
  totalQuantity: Type.Number(),
  limitPrice: Type.Union([Type.Number(), Type.Null()]),
  auxPrice: Type.Union([Type.Number(), Type.Null()]),
  status: Type.String(),
  filled: Type.Number(),
  remaining: Type.Number(),
  avgFillPrice: Type.Union([Type.Number(), Type.Null()]),
  tif: Type.String(),
  outsideRth: Type.Optional(Type.Boolean({ description: "Allow fill outside regular trading hours" })),
});

const ExecutedOrder = Type.Object({
  execId: Type.String(),
  symbol: Type.String(),
  contract: OrderContract,
  side: Type.String(),
  quantity: Type.Number(),
  avgPrice: Type.Union([Type.Number(), Type.Null()]),
  commission: Type.Union([Type.Number(), Type.Null()]),
  realizedPNL: Type.Union([Type.Number(), Type.Null()]),
  time: Type.String(),
  exchange: Type.String(),
});

export const OrdersData = Type.Object({
  last_sync: Type.String(),
  open_orders: Type.Array(OpenOrder),
  executed_orders: Type.Array(ExecutedOrder),
  open_count: Type.Number(),
  executed_count: Type.Number(),
});

export type OrdersData = Static<typeof OrdersData>;

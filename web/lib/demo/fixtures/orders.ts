import type { OrdersSnapshot } from "@/lib/orders/readOrdersFromDb";
import type { ExecutedOrder } from "@/lib/types";
import { compactDate, marketDateKey, nextFridayDateKey } from "./time";

function demoExecutions(now: Date): ExecutedOrder[] {
  const dayKey = marketDateKey(now).replaceAll("-", "");
  const optionExpiry = compactDate(nextFridayDateKey(marketDateKey(now), 105));
  const time = now.toISOString();
  return [
    {
      execId: `DEMO-${dayKey}-STK`,
      account_id: "DEMO",
      permId: 91_001,
      orderId: 51_001,
      clientId: 0,
      orderRef: "demo-equity-entry",
      symbol: "AAPL",
      contract: { conId: 265_598, symbol: "AAPL", secType: "STK", strike: null, right: null, expiry: null },
      side: "BOT",
      quantity: 40,
      avgPrice: 232.18,
      commission: 0.35,
      realizedPNL: 0,
      time,
      exchange: "NASDAQ",
    },
    {
      execId: `DEMO-${dayKey}-OPT-C`,
      account_id: "DEMO",
      permId: 91_002,
      orderId: 51_002,
      clientId: 0,
      orderRef: "demo-call-entry",
      symbol: "MSFT",
      contract: { conId: 901_002, symbol: "MSFT", secType: "OPT", strike: 530, right: "C", expiry: optionExpiry },
      side: "BOT",
      quantity: 2,
      avgPrice: 8.4,
      commission: 1.3,
      realizedPNL: 0,
      time,
      exchange: "SMART",
    },
    {
      execId: `DEMO-${dayKey}-OPT-P`,
      account_id: "DEMO",
      permId: 91_003,
      orderId: 51_003,
      clientId: 0,
      orderRef: "demo-hedge-entry",
      symbol: "QQQ",
      contract: { conId: 901_003, symbol: "QQQ", secType: "OPT", strike: 555, right: "P", expiry: optionExpiry },
      side: "BOT",
      quantity: 1,
      avgPrice: 6.15,
      commission: 0.65,
      realizedPNL: 0,
      time,
      exchange: "SMART",
    },
  ];
}

/** Retains demo working orders while supplying a stable current-session blotter. */
export function buildDemoOrders(base: OrdersSnapshot, now: Date = new Date()): OrdersSnapshot {
  const executedOrders = demoExecutions(now);
  return {
    ...base,
    last_sync: now.toISOString(),
    open_orders: base.open_orders,
    open_count: base.open_orders.length,
    executed_orders: executedOrders,
    executed_count: executedOrders.length,
  };
}

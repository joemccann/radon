"use client";

import Link from "next/link";
import type { OrdersData, OpenOrder, ExecutedOrder } from "@/lib/types";
import { useViewport } from "@/lib/useViewport";

type Props = {
  orders: OrdersData | null;
};

function fmtTime(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function describeOrder(o: OpenOrder): string {
  const c = o.contract;
  const action = o.action || "";
  const qty = Math.abs(o.totalQuantity ?? 0);
  const limit = o.limitPrice != null ? `@ $${o.limitPrice.toFixed(2)}` : "";
  if (c.secType === "BAG") {
    return `${action} ${qty}× ${c.symbol} combo ${limit}`.trim();
  }
  if (c.secType === "OPT") {
    const right = c.right === "C" ? "Call" : c.right === "P" ? "Put" : c.right ?? "";
    return `${action} ${qty}× ${c.symbol} ${right} $${c.strike} ${limit}`.trim();
  }
  return `${action} ${qty}× ${c.symbol} ${limit}`.trim();
}

function describeFill(f: ExecutedOrder): string {
  const c = f.contract;
  const action = f.side || "";
  const qty = Math.abs(f.quantity ?? 0);
  const price = f.avgPrice != null ? `@ $${f.avgPrice.toFixed(2)}` : "";
  if (c.secType === "BAG") {
    return `${action} ${qty}× ${c.symbol} combo ${price}`.trim();
  }
  if (c.secType === "OPT") {
    const right = c.right === "C" ? "Call" : c.right === "P" ? "Put" : c.right ?? "";
    return `${action} ${qty}× ${c.symbol} ${right} $${c.strike} ${price}`.trim();
  }
  return `${action} ${qty}× ${c.symbol} ${price}`.trim();
}

/**
 * OrdersSnapshotCard — compressed "what am I working on right now" view.
 * Top 3 open orders + top 3 of today's fills, with click-through to /orders.
 */
export function OrdersSnapshotCard({ orders }: Props) {
  const { isMobile, hasMounted } = useViewport();
  const mobile = isMobile && hasMounted;

  const open = (orders?.open_orders ?? []).slice(0, 3);
  const recent = (orders?.executed_orders ?? []).slice(0, 3);
  const hasAny = open.length > 0 || recent.length > 0;

  if (mobile) {
    // Mobile: drop the panel-eyebrow/title header; section toggle already labels
    // this block. Flatten into a single-column stacked list with a see-all link.
    return (
      <div className="snap-mobile-orders">
        {!hasAny ? (
          <div className="snapshot-card__empty">No open or filled orders today.</div>
        ) : (
          <>
            {open.length > 0 && (
              <ul className="snapshot-list__items">
                {open.map((o) => (
                  <li key={`o-${o.permId || o.orderId}`} className="snapshot-list__row snap-mobile-row">
                    <span className="snapshot-list__row-desc">{describeOrder(o)}</span>
                    <span className="snapshot-list__row-meta snap-mobile-badge snap-mobile-badge--working">
                      {o.status ?? "Working"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {recent.length > 0 && (
              <ul className="snapshot-list__items">
                {recent.map((f, i) => (
                  <li key={`f-${f.execId || i}`} className="snapshot-list__row snap-mobile-row">
                    <span className="snapshot-list__row-desc">{describeFill(f)}</span>
                    <span className="snapshot-list__row-meta">{fmtTime(f.time)}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
        <Link className="snap-mobile-see-all tap-target" href="/orders">
          All orders
        </Link>
      </div>
    );
  }

  return (
    <section className="snapshot-card">
      <span className="panel-edge-trace" aria-hidden />
      <header className="snapshot-card__header">
        <p className="panel-eyebrow">Orders / 03</p>
        <h3 className="panel-title">Working &amp; Filled</h3>
        <Link className="snapshot-card__see-all" href="/orders">All orders →</Link>
      </header>

      {!hasAny ? (
        <div className="snapshot-card__empty">No open or filled orders today.</div>
      ) : (
        <div className="snapshot-card__split">
          <div className="snapshot-list">
            <p className="snapshot-list__kicker">
              Working
              <span className="snapshot-list__count">{orders?.open_orders.length ?? 0}</span>
            </p>
            {open.length === 0 ? (
              <div className="snapshot-list__empty">No working orders.</div>
            ) : (
              <ul className="snapshot-list__items">
                {open.map((o) => (
                  <li key={`o-${o.permId || o.orderId}`} className="snapshot-list__row">
                    <span className="snapshot-list__row-desc">{describeOrder(o)}</span>
                    <span className="snapshot-list__row-meta">{o.status ?? "—"}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="snapshot-list">
            <p className="snapshot-list__kicker">
              Today&apos;s Fills
              <span className="snapshot-list__count">{orders?.executed_orders.length ?? 0}</span>
            </p>
            {recent.length === 0 ? (
              <div className="snapshot-list__empty">No fills today.</div>
            ) : (
              <ul className="snapshot-list__items">
                {recent.map((f, i) => (
                  <li key={`f-${f.execId || i}`} className="snapshot-list__row">
                    <span className="snapshot-list__row-desc">{describeFill(f)}</span>
                    <span className="snapshot-list__row-meta">{fmtTime(f.time)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

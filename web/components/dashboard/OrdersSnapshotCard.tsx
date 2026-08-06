"use client";

import type { CSSProperties } from "react";
import Link from "next/link";
import type { OrdersData, OpenOrder, ExecutedOrder } from "@/lib/types";
import { useViewport } from "@/lib/useViewport";

type Props = {
  orders: OrdersData | null;
};

/** Soft session density cap for the working-orders edge gauge. */
const WORKING_EDGE_CAP = 12;

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
  const workingCount = orders?.open_orders.length ?? 0;
  const filledCount = orders?.executed_orders.length ?? 0;

  const edgeLevel =
    orders != null
      ? Math.max(0, Math.min(100, (workingCount / WORKING_EDGE_CAP) * 100))
      : null;
  const edgeStyle =
    edgeLevel != null
      ? ({ ["--edge-level"]: `${edgeLevel.toFixed(1)}%` } as CSSProperties)
      : undefined;

  const sessionEt = new Date().toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "America/New_York",
  });

  if (mobile) {
    // Mobile: section toggle owns the short device label. Flatten lists.
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
      <span className="panel-edge-trace" style={edgeStyle} aria-hidden />
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
              <span className="snapshot-list__count">{workingCount}</span>
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
              <span className="snapshot-list__count">{filledCount}</span>
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
      <footer className="panel-meta-rail" aria-label="Orders calibration">
        <div className="panel-meta-rail-item">
          <span className="k">working</span>
          <span className="v">{workingCount}</span>
        </div>
        <div className="panel-meta-rail-item">
          <span className="k">filled.today</span>
          <span className="v">{filledCount}</span>
        </div>
        <div className="panel-meta-rail-item">
          <span className="k">session</span>
          <span className="v">{sessionEt} ET</span>
        </div>
      </footer>
    </section>
  );
}

"use client";

import { classifyTicks } from "@/lib/book/depthDerivations";
import type { Trade } from "@/lib/pricesProtocol";
import type { OrderPrefill } from "@/lib/TickerDetailContext";
import { fmtDepthPrice } from "./depthFormat";

type PriceClick = (p: Omit<OrderPrefill, "nonce">) => void;

/**
 * Render the tape `time` as HH:MM:SS ET. Accepts either an ISO-8601 string
 * (what the relay emits) or a unix-seconds string. Falls through to the raw
 * value if it parses to neither.
 */
function fmtTapeTime(time: string): string {
  const ms = /^\d+$/.test(time.trim()) ? Number(time) * 1000 : Date.parse(time);
  if (!Number.isFinite(ms) || ms <= 0) return time;
  return new Date(ms).toLocaleTimeString("en-US", {
    hour12: false,
    timeZone: "America/New_York",
  });
}

/**
 * Time & Sales tape. `trades` arrives OLDEST-first (the relay's ring-buffer
 * order); classifyTicks must see it that way so each print is tick-tested
 * against the chronologically prior one. We then reverse so the NEWEST print
 * renders at the top, the montage-tape convention.
 * `visible` drives the reflow-aware opacity on the parent.
 */
export function TimeAndSales({
  trades,
  visible,
  onPriceClick,
}: {
  trades: Trade[];
  visible: boolean;
  onPriceClick?: PriceClick;
}) {
  const rows = classifyTicks(trades).reverse();

  return (
    <div className={`book-tape${visible ? "" : " book-tape-hidden"}`}>
      <div className="book-colhead">
        <span>Price</span>
        <span className="r">Shares</span>
        <span className="r">Mkt</span>
        <span className="r">Time</span>
      </div>
      <div className="book-tape-rows">
        {rows.map((trade, i) => {
          // Click-to-fill: tick-test tone infers side (uptick traded into the
          // offer -> BUY, downtick -> SELL). Flat/unknown -> price-only (action
          // omitted) since tone is a heuristic, not the true aggressor side.
          const action = trade.tone === "up" ? "BUY" : trade.tone === "down" ? "SELL" : undefined;
          const clickable = onPriceClick != null;
          // No reveal animation on tape rows: prints stream in realtime and a
          // per-row entry animation makes the whole tape flash/repaint on every
          // tick. Key by print content (+ index to disambiguate dup prints) so
          // React reconciles the list instead of remounting it.
          return (
            <div
              className={`book-trow${clickable ? " book-row-fill" : ""}`}
              data-testid="book-tape-row"
              key={`${trade.time}-${trade.price}-${trade.size}-${trade.exchange ?? ""}-${i}`}
              {...(clickable
                ? {
                    role: "button" as const,
                    tabIndex: 0,
                    "aria-label": `Fill ticket: ${fmtDepthPrice(trade.price)}`,
                    title: `Fill ticket: ${fmtDepthPrice(trade.price)}`,
                    onClick: () =>
                      onPriceClick({ price: trade.price, action, quantity: trade.size, source: "tape" }),
                  }
                : {})}
            >
              <span className={`book-t-px ${trade.tone}`}>{fmtDepthPrice(trade.price)}</span>
              <span className="book-t-sz">{trade.size}</span>
              <span className="book-t-mkt">{trade.exchange ?? "--"}</span>
              <span className="book-t-time">{fmtTapeTime(trade.time)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

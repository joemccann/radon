"use client";

import { type ReactNode, useEffect, useState } from "react";
import type { DepthBook, Trade } from "@/lib/pricesProtocol";
import type { OrderPrefill } from "@/lib/TickerDetailContext";
import { DepthMontage } from "./DepthMontage";
import { LadderDOM } from "./LadderDOM";
import { TimeAndSales } from "./TimeAndSales";
import { fmtDepthPrice, fmtSpread } from "./depthFormat";
import { deriveBookHeader } from "@/lib/book/depthDerivations";

const TAPE_STORAGE_KEY = "radon:book:tape";

type InstrumentKind = "stock" | "option" | "future" | "combo";

export type OrderBookProps = {
  /** Display symbol in the window head, e.g. "RKLB" or "ES ESM6". */
  symbolLabel: string;
  /** Resolved instrument kind (depth.kind wins upstream). */
  kind: InstrumentKind;
  /** Live depth-of-book for the focused subject; null until it arrives. */
  depth: DepthBook | null;
  /** Time & Sales tape rows (Phase 1 may seed from last-trade). */
  trades: Trade[];
  /** L1 scalars for the window head. */
  last: number | null;
  lastLabel?: string;
  bid: number | null;
  ask: number | null;
  /** The existing L1 panel, rendered when depth is absent or unentitled. */
  l1Fallback: ReactNode;
  /** Click-to-fill: a depth level or tape print was clicked. Omitted = no fill. */
  onPriceClick?: (p: Omit<OrderPrefill, "nonce">) => void;
};

function readTapePreference(): boolean {
  if (typeof window === "undefined") return true;
  try {
    const stored = window.localStorage.getItem(TAPE_STORAGE_KEY);
    return stored == null ? true : stored === "on";
  } catch {
    return true;
  }
}

/**
 * Depth-of-book container. Owns the window head (symbol / kind / LAST·BID·ASK·
 * SPRD + feed pill), the Time & Sales show/hide toggle, and the reflowing
 * body grid. Dispatches the left pane by kind: no live entitled depth ->
 * the existing L1 panel; futures -> centered ladder; else -> two-sided montage.
 */
export function OrderBook({
  symbolLabel,
  kind,
  depth,
  trades,
  last,
  lastLabel = "LAST",
  bid,
  ask,
  l1Fallback,
  onPriceClick,
}: OrderBookProps) {
  const [tapeVisible, setTapeVisible] = useState(true);

  useEffect(() => {
    setTapeVisible(readTapePreference());
  }, []);

  const toggleTape = () => {
    setTapeVisible((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(TAPE_STORAGE_KEY, next ? "on" : "off");
      } catch {
        /* storage unavailable — keep in-memory state */
      }
      return next;
    });
  };

  const hasDepth = depth?.entitled === true;
  const kindLabel = kind === "future"
    ? "FUTURE"
    : kind === "option"
      ? "OPTION"
      : kind === "combo"
        ? "IMPLIED SPREAD"
        : "STOCK";
  const tapeAvailable = kind !== "combo";

  // The window head reads from the DEPTH BOOK when one is entitled — it is the
  // authoritative source on this tab (the separate L1 feed can deliver corrupt
  // / negative scalars for thin instruments). Falls back to the passed-in L1
  // scalars only when there is no entitled depth book (the L1 fallback path).
  const head = deriveBookHeader(depth, { bid, ask, last, lastLabel });

  const left = !hasDepth ? (
    l1Fallback
  ) : kind === "future" ? (
    <LadderDOM book={depth} last={head.last} onPriceClick={onPriceClick} />
  ) : (
    <DepthMontage book={depth} onPriceClick={onPriceClick} />
  );

  return (
    <div className="book-window">
      <div className="book-window-head">
        <span className="book-sym">
          {symbolLabel}
          <span className="book-kind">{kindLabel}</span>
        </span>
        <span className="book-head-stat">
          {head.lastLabel} <b>{head.last != null ? fmtDepthPrice(head.last) : "---"}</b>
        </span>
        <span className="book-head-stat bid">
          BID <b>{head.bid != null ? fmtDepthPrice(head.bid) : "---"}</b>
        </span>
        <span className="book-head-stat ask">
          ASK <b>{head.ask != null ? fmtDepthPrice(head.ask) : "---"}</b>
        </span>
        <span className="book-head-stat">
          SPRD <b>{fmtSpread(head.bid, head.ask)}</b>
        </span>
        <span className="book-head-spacer" />
        {depth?.feed && <span className="book-feed-pill">{depth.feed}</span>}
        {tapeAvailable && <button
          type="button"
          className="book-toggle"
          role="switch"
          aria-checked={tapeVisible}
          aria-label="Toggle Time and Sales"
          onClick={toggleTape}
        >
          <span className="book-toggle-track">
            <span className="book-toggle-thumb" />
          </span>
          <span className="book-toggle-text">
            {tapeVisible ? "Tape Shown" : "Tape Hidden"}
          </span>
        </button>}
      </div>
      <div className={`book-body-grid${tapeVisible && tapeAvailable ? "" : " tape-hidden"}`}>
        <div className="book-montage">{left}</div>
        <div className="book-tape-cell">
          <TimeAndSales trades={trades} visible={tapeVisible && tapeAvailable} onPriceClick={onPriceClick} />
        </div>
      </div>
    </div>
  );
}

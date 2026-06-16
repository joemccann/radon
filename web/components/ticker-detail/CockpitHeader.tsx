"use client";

import { useCallback, useMemo, useState } from "react";
import type { PriceData } from "@/lib/pricesProtocol";
import type { PortfolioPosition } from "@/lib/types";
import { fmtPrice } from "@/lib/positionUtils";
import { toneClass } from "@/lib/format";
import { useWatchlist } from "@/lib/useWatchlist";
import { useViewport } from "@/lib/useViewport";
import StarToggle from "@/components/StarToggle";
import type { DeckKey } from "./AssetCockpit";

type CockpitHeaderProps = {
  ticker: string;
  kind: "stock" | "option" | "future";
  /** SINGLE source of truth for last / netΔ / spread. Book owns bid×ask depth. */
  quotePriceData: PriceData | null;
  /** Net-spread flag — combos show a signed net, not a percent move. */
  isSpreadNet?: boolean;
  position: PortfolioPosition | null;
  /** Live WS feed is delivering ticks (depth/quote present). */
  live: boolean;
  onDeckChange: (deck: DeckKey | null) => void;
};

const KIND_LABEL: Record<CockpitHeaderProps["kind"], string> = {
  stock: "STOCK",
  option: "OPTION",
  future: "FUTURE",
};

export default function CockpitHeader({
  ticker,
  kind,
  quotePriceData,
  isSpreadNet,
  position,
  live,
  onDeckChange,
}: CockpitHeaderProps) {
  const { isMobile, hasMounted } = useViewport();
  const mobile = isMobile && hasMounted;

  const { last, deltaPct, spreadAbs, spreadPct } = useMemo(() => {
    const q = quotePriceData;
    const lastVal = q?.last ?? null;
    const close = q?.close ?? null;
    const bid = q?.bid ?? null;
    const ask = q?.ask ?? null;

    const dPct =
      lastVal != null && close != null && close !== 0
        ? ((lastVal - close) / Math.abs(close)) * 100
        : null;

    const sAbs = bid != null && ask != null ? ask - bid : null;
    const mid = bid != null && ask != null ? (ask + bid) / 2 : null;
    const sPct = sAbs != null && mid != null && mid !== 0 ? (sAbs / mid) * 100 : null;

    return { last: lastVal, deltaPct: dPct, spreadAbs: sAbs, spreadPct: sPct };
  }, [quotePriceData]);

  const deltaTone = deltaPct == null ? "" : toneClass(deltaPct);
  const lastLabel = quotePriceData?.lastIsCalculated ? "MARK" : null;

  const chipLabel = position ? position.structure : "FLAT";

  const { isWatched, toggleWatch } = useWatchlist();
  const [watchBusy, setWatchBusy] = useState(false);

  const handleToggleWatch = useCallback(async () => {
    setWatchBusy(true);
    try {
      await toggleWatch(ticker);
    } catch {
      // hook already rolled back the optimistic state
    } finally {
      setWatchBusy(false);
    }
  }, [ticker, toggleWatch]);

  if (mobile) {
    // Mobile: condensed strip — ticker + last + day chg + live dot + position chip.
    // Spread is dropped to keep the single row thumb-readable; position chip links
    // to the p-deck where the full summary lives.
    return (
      <div className="cockpit-head ckh--mobile">
        <span className="ckh-sym mono">{ticker}</span>
        <StarToggle
          active={isWatched(ticker)}
          busy={watchBusy}
          onToggle={handleToggleWatch}
          size="sm"
        />
        {live && <span className="ckh-live" aria-label="Live feed" />}
        <span className="ckh-spacer" />
        <span className={`ckh-last mono ${deltaTone}`}>
          {last != null ? fmtPrice(last) : "---"}
          {lastLabel && <span className="ckh-lastlabel">{lastLabel}</span>}
        </span>
        {deltaPct != null && (
          <span className={`ckh-delta mono ${deltaTone}`}>
            {deltaPct >= 0 ? "+" : ""}
            {Math.abs(deltaPct).toFixed(2)}%
          </span>
        )}
        <button
          type="button"
          className={`ckh-poschip tap-target ${position ? "held" : ""}`}
          onClick={() => onDeckChange("p")}
        >
          {chipLabel}
        </button>
      </div>
    );
  }

  return (
    <div className="cockpit-head">
      <span className="ckh-sym mono">{ticker}</span>
      <StarToggle
        active={isWatched(ticker)}
        busy={watchBusy}
        onToggle={handleToggleWatch}
        size="sm"
      />
      <span className="ckh-kind">{KIND_LABEL[kind]}</span>
      <span className="ckh-sep" />
      <span className={`ckh-last mono ${deltaTone}`}>
        {last != null ? fmtPrice(last) : "---"}
        {lastLabel && <span className="ckh-lastlabel">{lastLabel}</span>}
      </span>
      {deltaPct != null && (
        <span className={`ckh-delta mono ${deltaTone}`}>
          {deltaPct >= 0 ? "▲" : "▼"}
          {Math.abs(deltaPct).toFixed(2)}%
        </span>
      )}
      <span className="ckh-sep" />
      <span className="ckh-spr mono">
        {isSpreadNet ? "NET" : "SPREAD"}{" "}
        {spreadAbs != null ? (
          <>
            <b>{fmtPrice(spreadAbs)}</b>
            {spreadPct != null && <> / {spreadPct.toFixed(2)}%</>}
          </>
        ) : (
          <b>---</b>
        )}
      </span>
      <span className="ckh-sep" />
      {live && <span className="ckh-live">LIVE</span>}
      <span className="ckh-spacer" />
      <button
        type="button"
        className={`ckh-poschip ${position ? "held" : ""}`}
        onClick={() => onDeckChange("p")}
      >
        {chipLabel} <span className="ckh-arr">→</span>
      </button>
    </div>
  );
}

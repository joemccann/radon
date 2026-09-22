"use client";

import RequestError from "@/components/RequestError";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowLeft, BookOpen, ChevronDown, Layers, Newspaper, Table2 } from "lucide-react";
import type { PriceData } from "@/lib/pricesProtocol";
import type { PortfolioPosition } from "@/lib/types";
import { buildQuoteTelemetryModel } from "@/lib/quoteTelemetry";
import { useWatchlist } from "@/lib/useWatchlist";
import StarToggle from "@/components/StarToggle";
import type { DeckKey } from "./AssetCockpit";
import styles from "./ChainInstrumentSidebar.module.css";

export type HeldChainQuote = { priceData: PriceData | null; label?: string; isSpreadNet?: boolean };

type Props = {
  ticker: string;
  position: PortfolioPosition | null;
  underlyingQuote: PriceData | null;
  heldQuote: HeldChainQuote;
  activeDeck?: DeckKey | null;
  onDeckChange: (deck: DeckKey | null) => void;
};

const MORE_VIEWS: { key: DeckKey | null; label: string }[] = [
  { key: null, label: "Book & trade" },
  { key: "r", label: "Ratings" },
  { key: "s", label: "Seasonality" },
  { key: "i", label: "Company" },
  { key: "h", label: "13F holdings" },
  { key: "f", label: "Filings" },
];

export default function ChainInstrumentSidebar({ ticker, position, underlyingQuote, heldQuote, activeDeck = "c", onDeckChange }: Props) {
  const { isWatched, toggleWatch } = useWatchlist();
  const [watchError, setWatchError] = useState<unknown>(null);
  const [watchBusy, setWatchBusy] = useState(false);
  const moreRef = useRef<HTMLDetailsElement>(null);
  const moreSummaryRef = useRef<HTMLElement>(null);
  const activeMore = MORE_VIEWS.find(({ key }) => key !== null && key === activeDeck);
  const selectDeck = (deck: DeckKey | null, fromMore = false) => {
    if (moreRef.current?.open) {
      moreRef.current.open = false;
      if (fromMore) moreSummaryRef.current?.focus();
    }
    onDeckChange(deck);
  };
  // Keep closed/stale labels current even when the quote stream stops.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  const underlying = buildQuoteTelemetryModel(underlyingQuote, null, now);
  const held = buildQuoteTelemetryModel(heldQuote.priceData, null, now);
  const toggle = async () => {
    setWatchBusy(true);
    setWatchError(null);
    try { await toggleWatch(ticker); } catch (error) { setWatchError(error); }
    finally { setWatchBusy(false); }
  };

  return (
    <aside className={styles.sidebar} data-testid="chain-instrument-sidebar" aria-label={`${ticker} instrument`}>
      <RequestError error={watchError} fallback="The watchlist could not be updated. Try again." />
      <Link href="/portfolio" className={styles.back}><ArrowLeft size={14} /> Positions</Link>
      <div className={styles.identity}>
        <h1>{ticker}</h1>
        <StarToggle active={isWatched(ticker)} busy={watchBusy} onToggle={toggle} />
      </div>
      <section className={styles.quote} data-testid="chain-underlying-quote" aria-label="Underlying quote">
        <span className={styles.label}>Underlying · {underlying?.last.label ?? "LAST"}</span>
        <strong>{underlying?.last.value ?? "---"}</strong>
        <span className={underlying?.day.tone ? styles[underlying.day.tone] : styles.label}>
          Day {underlying?.day.value ?? "---"}
        </span>
      </section>
      <nav className={styles.navigation} aria-label="Instrument views">
        <button type="button" aria-current={activeDeck === "c" ? "page" : undefined} onClick={() => selectDeck("c")}><Table2 size={16} /> Options chain <kbd>c</kbd></button>
        <button type="button" aria-current={activeDeck === "p" ? "page" : undefined} onClick={() => selectDeck("p")}><Layers size={16} /> Position <kbd>p</kbd></button>
        <button type="button" aria-current={activeDeck === "n" ? "page" : undefined} onClick={() => selectDeck("n")}><Newspaper size={16} /> News <kbd>n</kbd></button>
        <details
          ref={moreRef}
          className={styles.more}
          onKeyDownCapture={(event) => {
            if (event.key !== "Escape" || !event.currentTarget.open) return;
            event.preventDefault();
            event.stopPropagation();
            event.currentTarget.open = false;
            moreSummaryRef.current?.focus();
          }}
        >
          <summary ref={moreSummaryRef} data-active={Boolean(activeMore)} aria-label={activeMore ? `${activeMore.label}, more instrument views` : "More views"}>
            <BookOpen size={16} /> {activeMore?.label ?? "More views"} <ChevronDown size={14} />
          </summary>
          <div>{MORE_VIEWS.map(({ key, label }) => <button type="button" key={label} aria-current={activeDeck === key ? "page" : undefined} onClick={() => selectDeck(key, true)}>{label}</button>)}</div>
        </details>
      </nav>
      <section className={styles.position} aria-label="Held position">
        <span className={styles.label}>Held position</span>
        {position ? <>
          <b>{position.structure}</b>
          <span className={styles.label}>{position.expiry ? `${position.expiry} · ` : ""}{position.contracts} {position.structure_type === "Stock" ? "shares" : "contracts"}</span>
          <div className={styles.heldQuote} data-testid="chain-held-quote">
            <span className={styles.label}>{heldQuote.isSpreadNet ? "Spread net" : heldQuote.label ?? ticker}</span>
            <div><span>{held?.last.label ?? "LAST"}</span><b>{held?.last.value ?? "---"}</b></div>
            <div><span>Day</span><span className={held?.day.tone ? styles[held.day.tone] : undefined}>{held?.day.value ?? "---"}</span></div>
          </div>
        </> : <span className={styles.label}>No open position</span>}
      </section>
      <button className={styles.bookLink} type="button" aria-current={activeDeck === null ? "page" : undefined} onClick={() => selectDeck(null)}><BookOpen size={15} /> Book & trade <span>Esc</span></button>
    </aside>
  );
}

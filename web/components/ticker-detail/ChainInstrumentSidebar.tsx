"use client";

import { useEffect, useState } from "react";
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
  onDeckChange: (deck: DeckKey | null) => void;
};

const MORE_VIEWS: { key: DeckKey | null; label: string }[] = [
  { key: null, label: "Book & trade" },
  { key: "r", label: "Ratings" },
  { key: "s", label: "Seasonality" },
  { key: "i", label: "Company" },
  { key: "h", label: "13F holdings" },
  { key: "f", label: "Filings" },
  { key: ":", label: "Commands" },
];

export default function ChainInstrumentSidebar({ ticker, position, underlyingQuote, heldQuote, onDeckChange }: Props) {
  const { isWatched, toggleWatch } = useWatchlist();
  const [watchBusy, setWatchBusy] = useState(false);
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
    try { await toggleWatch(ticker); } catch { /* Hook rolls back failed updates. */ }
    finally { setWatchBusy(false); }
  };

  return (
    <aside className={styles.sidebar} data-testid="chain-instrument-sidebar" aria-label={`${ticker} instrument`}>
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
        <button type="button" aria-current="page" onClick={() => onDeckChange("c")}><Table2 size={16} /> Options chain <kbd>c</kbd></button>
        <button type="button" onClick={() => onDeckChange("p")}><Layers size={16} /> Position <kbd>p</kbd></button>
        <button type="button" onClick={() => onDeckChange("n")}><Newspaper size={16} /> News <kbd>n</kbd></button>
        <details className={styles.more}>
          <summary><BookOpen size={16} /> More views <ChevronDown size={14} /></summary>
          <div>{MORE_VIEWS.map(({ key, label }) => <button type="button" key={label} onClick={() => onDeckChange(key)}>{label}</button>)}</div>
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
      <button className={styles.bookLink} type="button" onClick={() => onDeckChange(null)}><BookOpen size={15} /> Book & trade <span>Esc</span></button>
    </aside>
  );
}

"use client";

import type { PortfolioPosition } from "@/lib/types";
import { resolveEntryCost, resolveMarketValue } from "@/lib/positionUtils";
import { fmtUsd, toneClass } from "@/lib/format";

type ActHeldSummaryProps = {
  position: PortfolioPosition;
  /** Opens the full position detail (legs / cards / close-out) in the p-deck. */
  onOpenDeck: () => void;
};

/**
 * Subtle one-line held-position cue for the act column. NOT the position panel:
 * it carries a single line (structure + P&L) and links to the p-deck, keeping the
 * act column as clean as the flat futures view. Full detail lives in the deck.
 */
export default function ActHeldSummary({ position, onOpenDeck }: ActHeldSummaryProps) {
  const marketValue = resolveMarketValue(position);
  const entryCost = resolveEntryCost(position);
  const pnl = marketValue != null && entryCost != null ? marketValue - entryCost : null;

  return (
    <button type="button" className="act-flat" onClick={onOpenDeck}>
      <span>{position.structure}</span>
      <span className="act-flat-hint">
        {pnl != null && (
          <>
            P&amp;L <span className={toneClass(pnl)}>{fmtUsd(pnl)}</span> ·{" "}
          </>
        )}
        open ↑
      </span>
    </button>
  );
}

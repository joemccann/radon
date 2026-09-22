"use client";

import { groupPriceLevels, montageFill, isBestLevel } from "@/lib/book/depthDerivations";
import type { DepthBook, DepthLevel } from "@/lib/pricesProtocol";
import type { OrderPrefill } from "@/lib/TickerDetailContext";
import { fmtDepthPrice } from "./depthFormat";

type MontageLevel = DepthLevel & { firstOfLevel: boolean };

type PriceClick = (p: Omit<OrderPrefill, "nonce">) => void;

/**
 * An implied combo level aggregates every leg-venue pair quoting that net
 * price, so its venue string can carry dozens of entries. Rendering the whole
 * set wraps the cell into a tall block that breaks the montage row rhythm —
 * show the leading pair and count the rest, full set on hover.
 */
function venueCell(venues: string, side: "bid" | "ask", isCombo: boolean) {
  const pairs = isCombo ? venues.split(" | ") : [venues];
  const overflow = pairs.length - 1;
  return (
    <span className="book-mkt" key="m" title={venues}>
      {side === "ask" && overflow > 0 && <span className="book-venue-more">+{overflow}</span>}
      <span className="book-venue-lead">{pairs[0]}</span>
      {side === "bid" && overflow > 0 && <span className="book-venue-more">+{overflow}</span>}
    </span>
  );
}

/**
 * Two-sided montage for stocks (exchange / MPID L2 depth) and options
 * (per-exchange BBO). The two render identically except: stocks draw the
 * price-level edge marker (`firstOfLevel`) and treat index 0 as best; options
 * suppress the edge marker, trust the per-row `nbbo` best-rule, and tag the
 * NBBO-setting venue rows so the inside-of-market reads honestly as L1
 * top-of-book per exchange rather than a stacked depth ladder.
 */
export function DepthMontage({ book, onPriceClick }: { book: DepthBook; onPriceClick?: PriceClick }) {
  const bids: MontageLevel[] = groupPriceLevels(book.bid);
  const asks: MontageLevel[] = groupPriceLevels(book.ask);
  const maxSize = Math.max(
    ...book.bid.map((l) => l.size),
    ...book.ask.map((l) => l.size),
    1,
  );
  const isOption = book.kind === "option";
  const isCombo = book.kind === "combo";

  const row = (level: MontageLevel, side: "bid" | "ask", index: number) => {
    const fill = Math.round(montageFill(level, maxSize) * 55);
    const best = isBestLevel(level, index, book.kind);
    const lvlfirst = !isOption && level.firstOfLevel ? 1 : 0;
    const mkt = level.marketMaker ?? level.exchange ?? "--";
    const nbboTag =
      isOption && level.nbbo ? (
        <span className="book-nbbo-tag" key="n">
          NBBO
        </span>
      ) : null;
    const priceCell = (
      <span className="book-px" key="p">
        {fmtDepthPrice(level.price)}
        {nbboTag}
      </span>
    );
    const cells =
      side === "bid"
        ? [
            venueCell(mkt, side, isCombo),
            <span className="book-shares" key="s">{level.size}</span>,
            priceCell,
          ]
        : [
            priceCell,
            <span className="book-shares" key="s">{level.size}</span>,
            venueCell(mkt, side, isCombo),
          ];
    // Click-to-fill: hitting a BID level = you'd hit the bid -> SELL; an ASK
    // level = you'd lift the offer -> BUY. Price flows to the ticket's limit.
    const action = side === "bid" ? "SELL" : "BUY";
    const clickable = onPriceClick != null;
    return (
      <div
        className={`book-row book-reveal${best ? " best" : ""}${isOption && level.nbbo ? " nbbo" : ""}${clickable ? " book-row-fill" : ""}`}
        data-testid="book-depth-row"
        data-lvlfirst={lvlfirst}
        style={{ ["--fill" as string]: fill, ["--i" as string]: index }}
        key={`${side}-${index}`}
        {...(clickable
          ? {
              role: "button" as const,
              tabIndex: 0,
              "aria-label": `Fill ticket: ${action} ${fmtDepthPrice(level.price)}`,
              title: `Fill ticket: ${action} ${fmtDepthPrice(level.price)}`,
              onClick: () => onPriceClick({ price: level.price, action, source: "montage" }),
            }
          : {})}
      >
        {cells}
      </div>
    );
  };

  return (
    <div className={`book-montage-body${isOption ? " is-option" : ""}${isCombo ? " is-combo" : ""}`}>
      {isOption && (
        <p className="book-montage-note">
          OPRA top of book. Each row is one exchange&apos;s best quote, not stacked
          depth. NBBO marks the venues setting the inside bid and ask.
        </p>
      )}
      {isCombo && (
        <p className="book-montage-note">
          Implied spread book from synchronized leg markets. Prices estimate legging liquidity,
          not a native complex-order-book quote or guaranteed simultaneous fill.
        </p>
      )}
      <div className="book-sides">
        <div className="book-side bid">
          <div className="book-colhead">
            <span>{isOption ? "Exchange" : isCombo ? "Leg venues" : "Market"}</span>
            <span className="r">{isOption ? "Size" : "Shares"}</span>
            <span className="r">Bid</span>
          </div>
          {bids.map((level, i) => row(level, "bid", i))}
        </div>
        <div className="book-side ask">
          <div className="book-colhead">
            <span>Ask</span>
            <span>{isOption ? "Size" : "Shares"}</span>
            <span className="r">{isOption ? "Exchange" : isCombo ? "Leg venues" : "Market"}</span>
          </div>
          {asks.map((level, i) => row(level, "ask", i))}
        </div>
      </div>
    </div>
  );
}

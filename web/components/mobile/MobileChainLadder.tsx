"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { PriceData } from "@/lib/pricesProtocol";
import type { PortfolioData } from "@/lib/types";
import { fmtPrice } from "@/lib/positionUtils";
import {
  formatExpiry,
  daysToExpiry,
  type OrderLeg,
  normalizeComboOrder,
  computeNetOptionQuote,
} from "@/lib/optionsChainUtils";
import BottomSheet from "./BottomSheet";
import MobileOrderTicket from "./MobileOrderTicket";
import SpectralLoader from "@/components/SpectralLoader";

type Strike = {
  strike: number;
  callKey: string;
  putKey: string;
};

type MobileChainLadderProps = {
  ticker: string;
  expirations: string[];
  selectedExpiry: string | null;
  onSelectExpiry: (expiry: string) => void;
  visibleStrikes: Strike[];
  atmStrike: number | null;
  prices: Record<string, PriceData>;
  currentPrice: number | null;
  loading?: boolean;
  orderLegs?: OrderLeg[];
  onAddLeg?: (strike: number, right: "C" | "P", action: "BUY" | "SELL") => void;
  onRemoveLeg?: (id: string) => void;
  onUpdateLeg?: (id: string, updates: Partial<OrderLeg>) => void;
  onClearLegs?: () => void;
  /**
   * Live portfolio snapshot. Threaded down so the mobile ticket's risk gate
   * can fold in held-LONG coverage for SELL legs and stock-backed covered
   * calls — same contract as desktop chain.
   */
  portfolio?: PortfolioData | null;
};

type SelectedCell = {
  strike: number;
  right: "C" | "P";
  data: PriceData | null;
};

function fmtIv(iv: number | null | undefined): string {
  if (iv == null || !Number.isFinite(iv)) return "—";
  return `${(iv * 100).toFixed(1)}%`;
}

function fmtOi(oi: number | null | undefined): string {
  if (oi == null || !Number.isFinite(oi)) return "—";
  if (oi >= 10000) return `${(oi / 1000).toFixed(0)}k`;
  if (oi >= 1000) return `${(oi / 1000).toFixed(1)}k`;
  return String(oi);
}

function fmtLast(last: number | null | undefined): string {
  if (last == null || !Number.isFinite(last) || last <= 0) return "—";
  return fmtPrice(last);
}

function fmtBidAsk(bid: number | null | undefined, ask: number | null | undefined): string {
  const b = bid != null && Number.isFinite(bid) ? fmtPrice(bid) : "—";
  const a = ask != null && Number.isFinite(ask) ? fmtPrice(ask) : "—";
  return `${b} x ${a}`;
}

function fmtGreek(value: number | null | undefined, digits = 3): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}

export default function MobileChainLadder({
  ticker,
  expirations,
  selectedExpiry,
  onSelectExpiry,
  visibleStrikes,
  atmStrike,
  prices,
  currentPrice,
  loading,
  orderLegs = [],
  onAddLeg,
  onRemoveLeg,
  onUpdateLeg,
  onClearLegs,
  portfolio = null,
}: MobileChainLadderProps) {
  const [selected, setSelected] = useState<SelectedCell | null>(null);
  const [ticketOpen, setTicketOpen] = useState(false);
  const ladderRef = useRef<HTMLDivElement>(null);
  const atmRowRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to ATM strike on mount and when expiry/strikes change.
  // scrollIntoView is used for the initial snap; on subsequent expiry
  // changes the manual offsetTop path centers the row within the scroller.
  useEffect(() => {
    if (!atmRowRef.current || !ladderRef.current) return;
    const atm = atmRowRef.current;
    const wrapper = ladderRef.current;
    // Use scrollIntoView on first render (wrapper hasn't scrolled yet);
    // manual offset centering for subsequent changes so the row lands in
    // the middle of the visible window instead of flush to the edge.
    const target = atm.offsetTop - wrapper.clientHeight / 2 + atm.clientHeight / 2;
    if (wrapper.scrollTop === 0) {
      atm.scrollIntoView({ block: "center", behavior: "instant" });
    } else {
      wrapper.scrollTop = Math.max(0, target);
    }
  }, [selectedExpiry, visibleStrikes.length, atmStrike]);

  // Compute live net mid for the pending strip so the operator can see the
  // current market mid without opening the ticket.
  const pendingNetMid = useMemo(() => {
    if (orderLegs.length === 0) return null;
    const normalized = normalizeComboOrder(orderLegs);
    const quote = computeNetOptionQuote(normalized.legs, prices, ticker);
    if (quote.mid == null) return null;
    return quote.mid;
  }, [orderLegs, prices, ticker]);

  const expiryChips = useMemo(() => expirations.slice(0, 24), [expirations]);

  return (
    <div className="mobile-chain" data-testid="mobile-chain">
      <div className="mobile-chain__header">
        <div className="mobile-chain__brand">
          <span className="mobile-chain__ticker">{ticker.toUpperCase()}</span>
          <span className="mobile-chain__price">
            {currentPrice != null ? fmtPrice(currentPrice) : "—"}
          </span>
        </div>
        <div className="mobile-chain__expiry-bar" data-testid="mobile-chain-expiry-bar">
          {expiryChips.map((exp) => {
            const active = exp === selectedExpiry;
            return (
              <button
                key={exp}
                type="button"
                className={`mobile-chain__expiry-chip${active ? " mobile-chain__expiry-chip--active" : ""}`}
                onClick={() => onSelectExpiry(exp)}
                data-testid={`mobile-chain-expiry-${exp}`}
              >
                <span className="mobile-chain__expiry-date">{formatExpiry(exp)}</span>
                <span className="mobile-chain__expiry-dte">{daysToExpiry(exp)}d</span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="mobile-chain__ladder-head">
        <div className="mobile-chain__side-label">CALLS</div>
        <div className="mobile-chain__strike-label">STRIKE</div>
        <div className="mobile-chain__side-label">PUTS</div>
      </div>

      {loading ? (
        <div className="mobile-empty-state" data-testid="mobile-chain-loading">
          <SpectralLoader label="Loading chain" />
        </div>
      ) : visibleStrikes.length === 0 ? (
        <div className="mobile-empty-state" data-testid="mobile-chain-empty">
          <span>No strikes for this expiry.</span>
        </div>
      ) : (
        <div className="mobile-chain__ladder" ref={ladderRef} data-testid="mobile-chain-ladder">
          {visibleStrikes.map(({ strike, callKey, putKey }) => {
            const call = prices[callKey] ?? null;
            const put = prices[putKey] ?? null;
            const isAtm = atmStrike != null && strike === atmStrike;
            const rowClass = `mobile-chain__row${isAtm ? " mobile-chain__row--atm" : ""}`;

            return (
              <div
                key={strike}
                className={rowClass}
                ref={isAtm ? atmRowRef : undefined}
                data-testid={`mobile-chain-row-${strike}`}
              >
                <button
                  type="button"
                  className="mobile-chain__cell mobile-chain__cell--call"
                  onClick={() => setSelected({ strike, right: "C", data: call })}
                  data-testid={`mobile-chain-call-${strike}`}
                  aria-label={`Call ${strike}`}
                >
                  <span className="mobile-chain__last">{fmtLast(call?.last)}</span>
                  <span className="mobile-chain__bid-ask">{fmtBidAsk(call?.bid, call?.ask)}</span>
                  <span className="mobile-chain__meta">
                    <span>{fmtIv(call?.impliedVol)}</span>
                    <span>OI {fmtOi(call?.avgVolume)}</span>
                  </span>
                </button>

                <div className="mobile-chain__strike">{strike}</div>

                <button
                  type="button"
                  className="mobile-chain__cell mobile-chain__cell--put"
                  onClick={() => setSelected({ strike, right: "P", data: put })}
                  data-testid={`mobile-chain-put-${strike}`}
                  aria-label={`Put ${strike}`}
                >
                  <span className="mobile-chain__last">{fmtLast(put?.last)}</span>
                  <span className="mobile-chain__bid-ask">{fmtBidAsk(put?.bid, put?.ask)}</span>
                  <span className="mobile-chain__meta">
                    <span>{fmtIv(put?.impliedVol)}</span>
                    <span>OI {fmtOi(put?.avgVolume)}</span>
                  </span>
                </button>
              </div>
            );
          })}
        </div>
      )}

      {orderLegs.length > 0 ? (
        <button
          type="button"
          className="mobile-chain__pending-strip"
          onClick={() => setTicketOpen(true)}
          data-testid="mobile-chain-pending-strip"
        >
          <span className="mobile-chain__pending-count">
            {orderLegs.length} {orderLegs.length === 1 ? "LEG" : "LEGS"}
          </span>
          {pendingNetMid != null ? (
            <span className="mobile-chain__pending-mid">
              Mid {fmtPrice(Math.abs(pendingNetMid))}
            </span>
          ) : null}
          <span className="mobile-chain__pending-action">Build order &rarr;</span>
        </button>
      ) : null}

      <MobileOrderTicket
        open={ticketOpen && orderLegs.length > 0}
        ticker={ticker}
        legs={orderLegs}
        prices={prices}
        portfolio={portfolio}
        onClose={() => setTicketOpen(false)}
        onRemoveLeg={(id) => onRemoveLeg?.(id)}
        onUpdateLeg={(id, updates) => onUpdateLeg?.(id, updates)}
        onClearLegs={() => onClearLegs?.()}
      />

      {selected ? (
        <BottomSheet
          open
          onClose={() => setSelected(null)}
          title={`${ticker.toUpperCase()} ${selected.strike} ${selected.right === "C" ? "Call" : "Put"}`}
          testId="mobile-chain-detail-sheet"
          footer={
            onAddLeg ? (
              <div className="mobile-chain__detail-actions">
                <button
                  type="button"
                  className="mobile-chain__detail-buy"
                  onClick={() => {
                    onAddLeg(selected.strike, selected.right, "BUY");
                    setSelected(null);
                  }}
                  data-testid="mobile-chain-detail-buy"
                >
                  BUY
                </button>
                <button
                  type="button"
                  className="mobile-chain__detail-sell"
                  onClick={() => {
                    onAddLeg(selected.strike, selected.right, "SELL");
                    setSelected(null);
                  }}
                  data-testid="mobile-chain-detail-sell"
                >
                  SELL
                </button>
              </div>
            ) : null
          }
        >
          <div className="mobile-chain__detail">
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">Last</span>
              <span className="mobile-chain__detail-value">{fmtLast(selected.data?.last)}</span>
            </div>
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">Bid / Ask</span>
              <span className="mobile-chain__detail-value">
                {selected.data?.bid != null ? fmtPrice(selected.data.bid) : "—"} /{" "}
                {selected.data?.ask != null ? fmtPrice(selected.data.ask) : "—"}
              </span>
            </div>
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">Bid Size / Ask Size</span>
              <span className="mobile-chain__detail-value">
                {selected.data?.bidSize ?? "—"} / {selected.data?.askSize ?? "—"}
              </span>
            </div>
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">IV</span>
              <span className="mobile-chain__detail-value">{fmtIv(selected.data?.impliedVol)}</span>
            </div>
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">Delta</span>
              <span className="mobile-chain__detail-value">{fmtGreek(selected.data?.delta)}</span>
            </div>
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">Gamma</span>
              <span className="mobile-chain__detail-value">{fmtGreek(selected.data?.gamma, 4)}</span>
            </div>
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">Theta</span>
              <span className="mobile-chain__detail-value">{fmtGreek(selected.data?.theta)}</span>
            </div>
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">Vega</span>
              <span className="mobile-chain__detail-value">{fmtGreek(selected.data?.vega)}</span>
            </div>
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">Volume</span>
              <span className="mobile-chain__detail-value">{fmtOi(selected.data?.volume)}</span>
            </div>
            <div className="mobile-chain__detail-row">
              <span className="mobile-chain__detail-label">Avg Volume</span>
              <span className="mobile-chain__detail-value">{fmtOi(selected.data?.avgVolume)}</span>
            </div>
          </div>
        </BottomSheet>
      ) : null}
    </div>
  );
}

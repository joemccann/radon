"use client";

import { useEffect, useMemo, useState } from "react";
import { useIndexOptionsChain } from "@/lib/useIndexOptionsChain";
import { useTickerDetailOptional } from "@/lib/TickerDetailContext";
import { optionKey } from "@/lib/pricesProtocol";
import { type OrderRiskInput } from "@/lib/order";
import type { PortfolioData } from "@/lib/types";
import {
  ListedContractOrderForm,
  type ListedOrderFormValues,
} from "./ListedContractOrderForm";

interface IndexOptionOrderFormProps {
  ticker: string;
  /**
   * Live portfolio snapshot. Cash-settled index options (SPX/NDX/VIX) have
   * the same risk shape as equity options for the purposes of this gate
   * (single-leg covered-by-portfolio detection at the option level). Stock
   * coverage is not applicable because the underlying is an index, not
   * deliverable shares. Pass `null` if portfolio is not in scope — the
   * gate renders "Coverage indeterminate" and disables submit.
   */
  portfolio?: PortfolioData | null;
}

type OptionRight = "C" | "P";

function formatExpiry(date: string): string {
  if (date.length === 8) {
    return `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}`;
  }
  return date;
}

/**
 * Index-option order form — Phase 3 surface. Cascading dropdowns:
 *   expiry → right → strike → submit
 *
 * Two-step chain load: first call fetches ALL expirations (no expiry
 * param — quick), second call fetches contracts FOR the selected
 * expiry (filtered server-side). Without the second-step scope the
 * chain returns 1000+ contracts which is overkill for the form.
 *
 * Submits to /api/orders/place with type=option + conId + exchange so
 * IB doesn't pick up VIXW weeklies or other related roots by accident.
 */
export function IndexOptionOrderForm({ ticker, portfolio }: IndexOptionOrderFormProps) {
  const symbol = ticker.toUpperCase();

  // Step 1: expiries (no expiry scope)
  const initial = useIndexOptionsChain(symbol, null);

  const [selectedExpiry, setSelectedExpiry] = useState<string | null>(null);
  const [right, setRight] = useState<OptionRight>("C");
  const [selectedConId, setSelectedConId] = useState<number | null>(null);

  useEffect(() => {
    setSelectedExpiry(null);
    setSelectedConId(null);
    setRight("C");
  }, [symbol]);

  // Default to nearest expiry on load.
  useEffect(() => {
    if (
      initial.data?.symbol.toUpperCase() === symbol &&
      initial.data.expirations.length &&
      selectedExpiry == null
    ) {
      setSelectedExpiry(initial.data.expirations[0]);
    }
  }, [initial.data, selectedExpiry, symbol]);

  // Step 2: contracts scoped to the chosen expiry
  const scoped = useIndexOptionsChain(symbol, selectedExpiry);

  const expiryContracts = useMemo(() => {
    if (!scoped.data || scoped.data.symbol.toUpperCase() !== symbol) return [];
    return scoped.data.contracts.filter(
      (c) =>
        c.right === right &&
        c.symbol.toUpperCase() === symbol &&
        c.lastTradeDateOrContractMonth === selectedExpiry,
    );
  }, [scoped.data, right, selectedExpiry, symbol]);

  // Reset selected strike when expiry or right changes
  useEffect(() => {
    setSelectedConId(null);
  }, [selectedExpiry, right]);

  const selectedContract = useMemo(
    () => expiryContracts.find((c) => c.conId === selectedConId) ?? null,
    [expiryContracts, selectedConId],
  );

  // Quote telemetry for the STRIKE the operator is about to trade. Keyed on the
  // selected contract so the panel never falls back to the cash index, whose
  // bid/ask/high/low describe a different instrument. No quote for that key
  // (relay not subscribed to it) → the shared panel renders its empty state.
  const tickerDetail = useTickerDetailOptional();
  const prices = tickerDetail?.getPrices();
  const quote = useMemo(() => {
    if (!selectedContract) return { priceData: null, label: undefined as string | undefined };
    const key = optionKey({
      symbol,
      expiry: selectedContract.lastTradeDateOrContractMonth,
      strike: selectedContract.strike,
      right: selectedContract.right === "P" ? "P" : "C",
    });
    return {
      priceData: prices?.[key] ?? null,
      label: `${symbol} ${formatExpiry(selectedContract.lastTradeDateOrContractMonth)} $${selectedContract.strike} ${selectedContract.right}`,
    };
  }, [prices, selectedContract, symbol]);

  // Build the chokepoint input. Index options (SPX/NDX/VIX) are cash-settled
  // but the risk model is identical to equity options for the structures the
  // chokepoint covers: single-leg naked SELL CALL → UNBOUNDED; SELL PUT →
  // strike × 100 × N minus premium; LONG → premium debit. Without the gate,
  // SPX SELL CALL silently submitted with no UNBOUNDED warning — the
  // highest-blast-radius gap surfaced by the audit (commit ac6c886).
  const buildRiskInput = useMemo(
    () =>
      ({ action, quantity, limitPrice }: ListedOrderFormValues): OrderRiskInput | null => {
        const price = parseFloat(limitPrice);
        const qty = parseInt(quantity, 10);
        if (!selectedContract || !Number.isFinite(price) || price <= 0 || !Number.isFinite(qty) || qty <= 0) return null;
        const totalCost = Math.abs(price * qty * 100);
        const description = `${action} ${qty} ${selectedContract.localSymbol} @ $${price.toFixed(2)}`;
        return {
          ticker: symbol,
          chainLegs: [
            {
              action,
              right,
              strike: selectedContract.strike,
              // Normalise IB's "YYYYMMDD" expiry to the same string the augmenter expects.
              expiry: selectedContract.lastTradeDateOrContractMonth,
              quantity: qty,
            },
          ],
          netPremium: action === "SELL" ? -price : price,
          description,
          totalCost: action === "SELL" ? -totalCost : totalCost,
          // No live underlying spot is resolved on this surface; a naked-short
          // margin requirement renders "unavailable" rather than a guess.
          underlyingSpot: null,
        };
      },
    [selectedContract, right, symbol],
  );

  const buildSubmit = ({ action, quantity, limitPrice, tif }: ListedOrderFormValues) => {
    if (
      !selectedContract ||
      selectedContract.symbol.toUpperCase() !== symbol ||
      selectedContract.lastTradeDateOrContractMonth !== selectedExpiry
    ) {
      return { error: "Pick a strike" };
    }
    const qty = parseInt(quantity, 10);
    if (!Number.isFinite(qty) || qty <= 0) {
      return { error: "Quantity must be a positive integer" };
    }
    const price = parseFloat(limitPrice);
    if (!Number.isFinite(price) || price <= 0) {
      return { error: "Limit price must be a positive number" };
    }
    return {
      payload: {
        type: "option",
        symbol,
        action,
        quantity: qty,
        limitPrice: price,
        tif,
        expiry: selectedContract.lastTradeDateOrContractMonth,
        strike: selectedContract.strike,
        right: selectedContract.right,
        conId: selectedContract.conId,
        exchange: selectedContract.exchange,
      },
      successText: `${action} ${qty} ${selectedContract.localSymbol} @ ${price} submitted`,
    };
  };

  if (initial.loading) {
    return <div className="futures-form-loading">Loading {symbol} options chain…</div>;
  }
  if (initial.error) {
    return <div className="futures-form-error">Chain error: {initial.error}</div>;
  }
  if (!initial.data || initial.data.expirations.length === 0) {
    return <div className="futures-form-empty">No listed {symbol} options.</div>;
  }

  return (
    <ListedContractOrderForm
      eyebrow={
        <>
          {symbol} Options · {initial.data.exchange} · {initial.data.tradingClass}
        </>
      }
      contractSelector={
        <>
          <label className="futures-form-row">
            <span className="futures-form-label">Expiry</span>
            <select
              value={selectedExpiry ?? ""}
              onChange={(e) => setSelectedExpiry(e.target.value)}
              className="futures-form-select"
            >
              {initial.data.expirations.map((exp) => (
                <option key={exp} value={exp}>
                  {formatExpiry(exp)}
                </option>
              ))}
            </select>
          </label>

          <div className="futures-form-row futures-form-action-row">
            <button
              type="button"
              onClick={() => setRight("C")}
              className={`futures-form-action${right === "C" ? " futures-form-action--active" : ""}`}
            >
              CALL
            </button>
            <button
              type="button"
              onClick={() => setRight("P")}
              className={`futures-form-action${right === "P" ? " futures-form-action--active" : ""}`}
            >
              PUT
            </button>
          </div>

          <label className="futures-form-row">
            <span className="futures-form-label">Strike</span>
            {scoped.loading ? (
              <div className="futures-form-loading">Loading strikes…</div>
            ) : (
              <select
                value={selectedConId ?? ""}
                onChange={(e) => setSelectedConId(parseInt(e.target.value, 10))}
                className="futures-form-select"
              >
                <option value="">Pick a strike</option>
                {expiryContracts.map((c) => (
                  <option key={c.conId} value={c.conId}>
                    ${c.strike} {c.right}
                  </option>
                ))}
              </select>
            )}
          </label>
        </>
      }
      multiplier={100}
      multiplierDisplay="100"
      notionalLabel="Notional (limit × qty × 100)"
      limitPriceLabel="Limit Price (per share, x100 = contract)"
      limitPriceStep={selectedContract?.minTick ?? 0.05}
      priceData={quote.priceData}
      quoteLabel={quote.label}
      buildRiskInput={buildRiskInput}
      portfolio={portfolio}
      surface="index-option-form"
      buildSubmit={buildSubmit}
      submitLabel={(action) => `${action} ${selectedContract?.localSymbol ?? symbol}`}
      submitDisabled={selectedContract == null}
    />
  );
}

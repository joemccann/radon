"use client";

import { useEffect, useMemo, useState } from "react";
import { useTickerDetailOptional } from "@/lib/TickerDetailContext";
import { useFuturesChain, type FuturesChainContract } from "@/lib/useFuturesChain";
import { type LinearOrderRiskInput } from "@/lib/order";
import type { PortfolioData } from "@/lib/types";
import {
  ListedContractOrderForm,
  type ListedOrderFormValues,
} from "./ListedContractOrderForm";

interface FuturesOrderFormProps {
  ticker: string;
  /**
   * Live portfolio snapshot — routes into `<OrderRiskGate>` so SHORT futures
   * land in the same UNBOUNDED treatment as a naked short call. Linear
   * branch (added 2026-05-26 via OrderRiskInput discriminated union).
   */
  portfolio?: PortfolioData | null;
}

function formatExpiry(date: string): string {
  if (date.length === 8) {
    return `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}`;
  }
  if (date.length === 6) {
    return `${date.slice(0, 4)}-${date.slice(4, 6)}`;
  }
  return date;
}

/**
 * Futures order form — phase 2 surface. Replaces the index "not
 * tradeable" notice for symbols Radon supports as futures (VIX
 * currently; SPX / NDX wiring lands later).
 *
 * Flow:
 *   1. useFuturesChain loads the listed contracts via /api/futures/chain
 *   2. User picks an expiry from the dropdown — the form locks to that
 *      contract's conId (avoids re-qualification on the IB side)
 *   3. Submit POSTs to /api/orders/place with type=future + conId
 *
 * Notional banner shows price × contracts × multiplier so the user
 * sees the actual exposure (1 VIX future at 19 = $19,000 notional,
 * ~$5,500 initial margin).
 */
export function FuturesOrderForm({ ticker, portfolio }: FuturesOrderFormProps) {
  const symbol = ticker.toUpperCase();
  const tickerDetail = useTickerDetailOptional();
  const { data, loading, error } = useFuturesChain(symbol);

  const [selectedConId, setSelectedConId] = useState<number | null>(null);

  useEffect(() => setSelectedConId(null), [symbol]);

  // Default to front-month when chain loads.
  useEffect(() => {
    if (data?.symbol.toUpperCase() === symbol && data.contracts.length && selectedConId == null) {
      setSelectedConId(data.contracts[0].conId);
    }
  }, [data, selectedConId, symbol]);

  const selectedContract = useMemo<FuturesChainContract | null>(() => {
    if (!data || data.symbol.toUpperCase() !== symbol || selectedConId == null) return null;
    return data.contracts.find(
      (c) => c.conId === selectedConId && c.symbol.toUpperCase() === symbol,
    ) ?? null;
  }, [data, selectedConId, symbol]);

  const holding = useMemo(() => {
    let heldLong = 0;
    let heldShort = 0;
    let longBasis = 0;
    let shortBasis = 0;
    if (!selectedContract || !portfolio) return { heldLong, heldShort, longBasis, shortBasis };
    for (const position of portfolio.positions) {
      if (position.ticker.toUpperCase() !== symbol) continue;
      for (const leg of position.legs) {
        if (leg.con_id !== selectedContract.conId) continue;
        const contracts = Math.max(0, Math.abs(leg.contracts));
        if (leg.direction === "LONG") {
          heldLong += contracts;
          longBasis += contracts * Math.abs(leg.avg_cost);
        } else {
          heldShort += contracts;
          shortBasis += contracts * Math.abs(leg.avg_cost);
        }
      }
    }
    return { heldLong, heldShort, longBasis, shortBasis };
  }, [portfolio, selectedContract, symbol]);

  // Publish the selected contract's expiry to the depth subject so the relay
  // resolves THIS future (not the front month) under the index key, and the
  // OrderBook ladder follows the ticket. Clear on unmount so the book reverts
  // to front-month when the order form is not shown. Keyed on the expiry string
  // (including the auto-selected default front-month) so a swap re-publishes.
  const selectedExpiry = selectedContract?.lastTradeDateOrContractMonth ?? null;
  const setDepthFutureExpiry = tickerDetail?.setDepthFutureExpiry;
  useEffect(() => {
    if (!setDepthFutureExpiry) return;
    setDepthFutureExpiry(selectedExpiry);
    return () => setDepthFutureExpiry(null);
  }, [selectedExpiry, setDepthFutureExpiry]);

  const multiplier = useMemo(() => {
    if (!selectedContract) return 1000;
    const m = Number(selectedContract.multiplier);
    return Number.isFinite(m) && m > 0 ? m : 1000;
  }, [selectedContract]);

  // Chokepoint input for the linear branch. SHORT futures → UNBOUNDED;
  // LONG futures → bounded by price-to-zero × multiplier. heldQuantity is
  // not yet looked up from the portfolio (rare for futures); a future
  // refinement could scan portfolio for the same conId.
  const buildRiskInput = useMemo(
    () =>
      ({ action, quantity, limitPrice }: ListedOrderFormValues): LinearOrderRiskInput | null => {
        const price = parseFloat(limitPrice);
        const qty = parseInt(quantity, 10);
        if (!selectedContract || !Number.isFinite(price) || price <= 0 || !Number.isFinite(qty) || qty <= 0) return null;
        const closingLong = action === "SELL" && holding.heldLong >= qty;
        const closingShort = action === "BUY" && holding.heldShort >= qty;
        const basis = closingLong && holding.heldLong > 0
          ? holding.longBasis * (qty / holding.heldLong)
          : closingShort && holding.heldShort > 0
            ? holding.shortBasis * (qty / holding.heldShort)
            : null;
        return {
          type: "linear",
          ticker: symbol,
          instrument: "future",
          action,
          quantity: qty,
          limitPrice: price,
          multiplier,
          heldQuantity: holding.heldLong,
          heldShortQuantity: holding.heldShort,
          closeOut: basis == null ? null : { entryCostDollars: basis },
          description: `${action} ${qty} ${selectedContract.localSymbol} @ $${price.toFixed(2)}`,
        };
      },
    [selectedContract, multiplier, symbol, holding],
  );

  const buildSubmit = ({ action, quantity, limitPrice, tif }: ListedOrderFormValues) => {
    if (!selectedContract || selectedContract.symbol.toUpperCase() !== symbol) {
      return { error: "Pick an expiry" };
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
        type: "future",
        symbol,
        action,
        quantity: qty,
        limitPrice: price,
        tif,
        conId: selectedContract.conId,
        exchange: selectedContract.exchange,
      },
      successText: `${action} ${qty} ${selectedContract.localSymbol} @ ${price} submitted`,
    };
  };

  if (loading) {
    return <div className="futures-form-loading">Loading {symbol} futures chain…</div>;
  }
  if (error) {
    return <div className="futures-form-error">Chain error: {error}</div>;
  }
  if (!data || data.contracts.length === 0) {
    return <div className="futures-form-empty">No listed {symbol} futures.</div>;
  }

  return (
    <ListedContractOrderForm
      eyebrow={
        <>
          {symbol} Futures · {data.exchange}
        </>
      }
      contractSelector={
        <label className="futures-form-row">
          <span className="futures-form-label">Expiry</span>
          <select
            value={selectedConId ?? ""}
            onChange={(e) => setSelectedConId(parseInt(e.target.value, 10))}
            className="futures-form-select"
          >
            {data.contracts.map((c) => (
              <option key={c.conId} value={c.conId}>
                {c.localSymbol} — {formatExpiry(c.lastTradeDateOrContractMonth)}
              </option>
            ))}
          </select>
        </label>
      }
      multiplier={multiplier}
      multiplierDisplay={multiplier.toLocaleString()}
      notionalLabel="Notional"
      limitPriceLabel="Limit Price"
      limitPriceStep={selectedContract?.minTick ?? 0.05}
      buildRiskInput={buildRiskInput}
      portfolio={portfolio}
      surface="futures-form"
      buildSubmit={buildSubmit}
      submitLabel={(action) => `${action} ${selectedContract?.localSymbol ?? symbol}`}
      submitDisabled={selectedContract == null}
    />
  );
}

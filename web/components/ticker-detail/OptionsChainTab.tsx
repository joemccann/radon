"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type Ref } from "react";
import { useSearchParams } from "next/navigation";
import type { PriceData, OptionContract } from "@/lib/pricesProtocol";
import { optionKey, normalizeOptionExpiry } from "@/lib/pricesProtocol";
import type { PortfolioData, PortfolioPosition } from "@/lib/types";
import { fmtPrice } from "@/lib/positionUtils";
import OrderErrorBanner from "@/components/OrderErrorBanner";
import SpectralLoader from "@/components/SpectralLoader";
import { useOrderActionsOptional } from "@/lib/OrderActionsContext";
import { useTickerDetail } from "@/lib/TickerDetailContext";
import { useChainPrefetch } from "@/lib/useChainPrefetch";
import { useChainUrlState, parseSideParam, parseStrikesParam, type SideFilter } from "@/lib/useChainUrlState";
import { computeLegImpliedValue } from "@/lib/impliedValue";
import { useRiskFreeRate } from "@/lib/useRiskFreeRate";
import {
  type OrderLeg,
  formatExpiry,
  daysToExpiry,
  detectStructure,
  isBearishRiskReversal,
  computeNetPrice,
  computeNetOptionQuote,
  getComboEntryAction,
  getOrderBuilderStructureKey,
  normalizeComboOrder,
  findAtmStrike,
  getVisibleStrikes,
  ALL_STRIKES,
} from "@/lib/optionsChainUtils";
import {
  OrderPriceStrip,
  OrderRiskGate,
  useOrderRisk,
  type OrderQuoteSide,
  type OrderRiskInput,
} from "@/lib/order";
import { OrderQuoteTelemetry } from "@/components/QuoteTelemetry";
import { comboQuotePriceData } from "@/lib/quoteTelemetry";
import { useViewport } from "@/lib/useViewport";
import MobileChainLadder from "@/components/mobile/MobileChainLadder";
import ComboSkewPanel from "@/components/ComboSkewPanel";
import TicketRiskBlock from "@/components/ticker-detail/TicketRiskBlock";
import { netPremiumForPayoff, payoffAtExpiry, payoffCurve } from "@/lib/order/payoff";
import { placeOrderFeedback } from "@/lib/orders/placeOrderFeedback";

/* ─── Types ─── */

type OptionsChainTabProps = {
  ticker: string;
  prices: Record<string, PriceData>;
  tickerPriceData: PriceData | null;
  focusPosition?: PortfolioPosition | null;
  focusPositionRequested?: boolean;
  /**
   * Full portfolio snapshot. Used by the chain `OrderBuilder` so SELL legs at
   * a different strike than a held LONG (same ticker / expiry / right) compose
   * to a vertical spread instead of flagging "uncovered short".
   */
  portfolio?: PortfolioData | null;
};

type ChainStrike = {
  strike: number;
  callKey: string;
  putKey: string;
};

function optionFetchErrorMessage(data: Record<string, unknown>, fallback: string): string {
  const error = typeof data.error === "string" ? data.error : fallback;
  const detail = typeof data.detail === "string" ? data.detail : "";
  return detail && detail !== error ? `${error}: ${detail}` : error;
}

/* ─── Chain Strike Row ─── */

function StrikeRow({
  ticker,
  expiry,
  strike,
  callKey,
  putKey,
  prices,
  isAtm,
  onClickCall,
  onClickPut,
  atmRef,
  sideFilter,
  riskFreeRate,
  staged,
}: {
  ticker: string;
  expiry: string;
  strike: number;
  callKey: string;
  putKey: string;
  prices: Record<string, PriceData>;
  isAtm: boolean;
  onClickCall: (strike: number, action: "BUY" | "SELL") => void;
  onClickPut: (strike: number, action: "BUY" | "SELL") => void;
  atmRef?: React.Ref<HTMLTableRowElement>;
  sideFilter: "both" | "calls" | "puts";
  riskFreeRate: number;
  /** Which sides of THIS strike are staged in the ticket. */
  staged?: { call: boolean; put: boolean };
}) {
  const callData = prices[callKey] ?? null;
  const putData = prices[putKey] ?? null;

  const callBid = callData?.bid;
  const callAsk = callData?.ask;
  const callMid = callBid != null && callAsk != null ? (callBid + callAsk) / 2 : null;
  const callLast = callData?.last;
  const callVol = callData?.volume;
  const callOI = callData?.avgVolume; // OI not available via WS, placeholder
  const callIV = callData?.impliedVol;
  const callDelta = callData?.delta;

  const putBid = putData?.bid;
  const putAsk = putData?.ask;
  const putMid = putBid != null && putAsk != null ? (putBid + putAsk) / 2 : null;
  const putLast = putData?.last;
  const putVol = putData?.volume;
  const putIV = putData?.impliedVol;
  const putDelta = putData?.delta;

  // Black-Scholes implied (theoretical) per-share price. Reuses the same
  // resolver the dashboard PositionTable uses — same S, σ, K, T, r precedence.
  // contracts is set to 1 because we display per-share, not notional.
  const callImplied = useMemo(
    () =>
      computeLegImpliedValue(
        { ticker, expiry, strike, type: "Call", direction: "LONG", contracts: 1 },
        prices,
        { riskFreeRate },
      ).perContract,
    [ticker, expiry, strike, prices, riskFreeRate],
  );
  const putImplied = useMemo(
    () =>
      computeLegImpliedValue(
        { ticker, expiry, strike, type: "Put", direction: "LONG", contracts: 1 },
        prices,
        { riskFreeRate },
      ).perContract,
    [ticker, expiry, strike, prices, riskFreeRate],
  );

  // Bidirectional reference: a leg staged in the ticket stays visible in the
  // chain row it came from, per side.
  const callTint = staged?.call ? " chain-cell--staged-call" : "";
  const putTint = staged?.put ? " chain-cell--staged-put" : "";
  const rowClass = `chain-row ${isAtm ? "chain-row-atm" : ""}`;
  const showCalls = sideFilter !== "puts";
  const showPuts = sideFilter !== "calls";

  return (
    <tr className={rowClass} ref={atmRef}>
      {/* Call side */}
      {showCalls && (
        <>
          <td className={`chain-cell chain-greek${callTint}`}>{callDelta != null ? callDelta.toFixed(2) : ""}</td>
          <td className={`chain-cell chain-iv${callTint}`}>{callIV != null ? (callIV * 100).toFixed(1) : ""}</td>
          <td
            className={`chain-cell chain-implied${callTint}`}
            title="Black-Scholes implied (theoretical) per-share price"
          >
            {callImplied != null ? fmtPrice(callImplied) : ""}
          </td>
          <td className={`chain-cell chain-vol${callTint}`}>{callVol != null ? callVol.toLocaleString() : ""}</td>
          <td
            className={`chain-cell chain-bid chain-clickable${callTint}`}
            onClick={() => onClickCall(strike, "SELL")}
            title="Sell call"
          >
            {callBid != null ? fmtPrice(callBid) : "---"}
          </td>
          <td
            className={`chain-cell chain-mid chain-clickable${callTint}`}
            onClick={() => onClickCall(strike, "BUY")}
            title="Buy call"
          >
            {callMid != null ? fmtPrice(callMid) : "---"}
          </td>
          <td
            className={`chain-cell chain-ask chain-clickable${callTint}`}
            onClick={() => onClickCall(strike, "BUY")}
            title="Buy call"
          >
            {callAsk != null ? fmtPrice(callAsk) : "---"}
          </td>
          <td className={`chain-cell chain-last${callTint}`}>{callLast != null ? fmtPrice(callLast) : ""}</td>
        </>
      )}

      {/* Strike */}
      <td className={`chain-cell chain-strike ${isAtm ? "chain-strike-atm" : ""}`}>
        {fmtPrice(strike)}
      </td>

      {/* Put side */}
      {showPuts && (
        <>
          <td className={`chain-cell chain-last${putTint}`}>{putLast != null ? fmtPrice(putLast) : ""}</td>
          <td
            className={`chain-cell chain-bid chain-clickable${putTint}`}
            onClick={() => onClickPut(strike, "SELL")}
            title="Sell put"
          >
            {putBid != null ? fmtPrice(putBid) : "---"}
          </td>
          <td
            className={`chain-cell chain-mid chain-clickable${putTint}`}
            onClick={() => onClickPut(strike, "BUY")}
            title="Buy put"
          >
            {putMid != null ? fmtPrice(putMid) : "---"}
          </td>
          <td
            className={`chain-cell chain-ask chain-clickable${putTint}`}
            onClick={() => onClickPut(strike, "BUY")}
            title="Buy put"
          >
            {putAsk != null ? fmtPrice(putAsk) : "---"}
          </td>
          <td className={`chain-cell chain-vol${putTint}`}>{putVol != null ? putVol.toLocaleString() : ""}</td>
          <td
            className={`chain-cell chain-implied${putTint}`}
            title="Black-Scholes implied (theoretical) per-share price"
          >
            {putImplied != null ? fmtPrice(putImplied) : ""}
          </td>
          <td className={`chain-cell chain-iv${putTint}`}>{putIV != null ? (putIV * 100).toFixed(1) : ""}</td>
          <td className={`chain-cell chain-greek${putTint}`}>{putDelta != null ? putDelta.toFixed(2) : ""}</td>
        </>
      )}
    </tr>
  );
}

/* ─── Order Builder Panel ─── */

export function chainOrderSubmitPermitted(
  isValidPrice: boolean,
  riskState: { okToSubmit: boolean } | null,
  isCombo: boolean,
  isDebit: boolean | null,
): boolean {
  return isValidPrice
    && riskState?.okToSubmit === true
    && (!isCombo || isDebit !== null);
}

function OrderBuilder({
  ticker,
  legs,
  prices,
  spot,
  riskFreeRate,
  portfolio,
  builderRef,
  prefillLabel,
  onRemoveLeg,
  onUpdateLeg,
  onClearLegs,
}: {
  ticker: string;
  legs: OrderLeg[];
  prices: Record<string, PriceData>;
  spot?: number | null;
  riskFreeRate?: number;
  portfolio?: PortfolioData | null;
  builderRef?: Ref<HTMLDivElement>;
  prefillLabel?: string | null;
  onRemoveLeg: (id: string) => void;
  onUpdateLeg: (id: string, updates: Partial<OrderLeg>) => void;
  onClearLegs: () => void;
}) {
  const [tif, setTif] = useState<"DAY" | "GTC">("DAY");
  const [limitPrice, setLimitPrice] = useState("");
  const [priceManuallySet, setPriceManuallySet] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const orderActions = useOrderActionsOptional();
  const [confirmStep, setConfirmStep] = useState(false);

  const isCombo = legs.length > 1;
  const normalizedOrder = useMemo(() => (isCombo ? normalizeComboOrder(legs) : null), [isCombo, legs]);
  const pricingLegs = normalizedOrder?.legs ?? legs;
  const structureKey = useMemo(() => getOrderBuilderStructureKey(legs), [legs]);
  const lastStructureKeyRef = useRef("");
  const structure = detectStructure(legs);
  const netPrice = computeNetPrice(pricingLegs, prices);
  const isDebit = netPrice != null ? netPrice > 0 : null;
  const totalQty = normalizedOrder?.quantity ?? (legs.length > 0 ? legs[0].quantity : 1);

  const parsedPrice = parseFloat(limitPrice);
  const isValidPrice = !isNaN(parsedPrice) && (isCombo ? parsedPrice !== 0 : parsedPrice > 0);
  const signedLimitPrice = Number.isFinite(parsedPrice)
    ? isDebit === null
      ? parsedPrice
      : isDebit
        ? Math.abs(parsedPrice)
        : -Math.abs(parsedPrice)
    : NaN;

  // For BID/MID/ASK quote, always use ratio-normalized legs (quantity=1 for single leg)
  // so the quote shows per-unit price, not aggregate (e.g. $1.46 not $73.00 for 50 contracts)
  const quotingLegs = useMemo(() => {
    if (legs.length === 0) return legs;
    return normalizeComboOrder(legs).legs;
  }, [legs]);

  // Compute net BID / ASK / MID from leg WS prices
  const netPrices = useMemo(() => {
    return computeNetOptionQuote(quotingLegs, prices, ticker);
  }, [quotingLegs, prices, ticker]);

  const signedNetPrice = useCallback((value: number | null) => {
    if (value == null) return null;
    // Single-leg orders carry a positive premium (the price you pay/receive
    // for that one option). Sign-flipping is a combo-only concept for
    // expressing net debit/credit. Forcing positive here keeps BID/MID/ASK
    // quote buttons positive, the auto-populated limit positive, and the
    // `isValidPrice` (parsedPrice > 0) check satisfied.
    if (!isCombo) return Math.abs(value);
    return value;
  }, [isCombo]);

  const signedNetPrices = useMemo(() => {
    return {
      bid: signedNetPrice(netPrices.bid),
      mid: signedNetPrice(netPrices.mid),
      ask: signedNetPrice(netPrices.ask),
    };
  }, [netPrices.bid, netPrices.mid, netPrices.ask, signedNetPrice]);

  // One quote for whatever is being ticketed: the contract's own book for a
  // single leg, the net combo book wrapped as a PriceData for a spread. Both
  // feed the same shared quote-telemetry model.
  const quotePriceData = useMemo((): PriceData | null => {
    if (legs.length === 0) return null;
    if (isCombo) {
      const { bid, ask, mid } = signedNetPrices;
      if (bid == null || ask == null) return null;
      return comboQuotePriceData({ symbol: ticker, bid, ask, last: mid });
    }
    const leg = legs[0];
    return prices[optionKey({
      symbol: ticker,
      expiry: leg.expiry,
      strike: leg.strike,
      right: leg.right,
    })] ?? null;
  }, [legs, isCombo, signedNetPrices, prices, ticker]);

  const quoteLabel = useMemo(() => {
    if (legs.length === 0) return "";
    if (isCombo) return `${ticker}${structure ? ` ${structure}` : ""} NET`;
    const leg = legs[0];
    return `${ticker} ${formatExpiry(leg.expiry)} $${leg.strike} ${leg.right === "C" ? "Call" : "Put"}`;
  }, [legs, isCombo, ticker, structure]);

  useEffect(() => {
    if (structureKey === lastStructureKeyRef.current) return;
    lastStructureKeyRef.current = structureKey;
    setPriceManuallySet(false);
    if (!structureKey) {
      setLimitPrice("");
    }
  }, [structureKey]);

  // Auto-populate limit price to mid when prices first become available
  useEffect(() => {
    if (!priceManuallySet && signedNetPrices.mid != null) {
      setLimitPrice(signedNetPrices.mid.toFixed(2));
    }
  }, [signedNetPrices.mid, priceManuallySet, structureKey]);

  // Build the chokepoint input. All risk math + portfolio augmentation now
  // lives in `useOrderRisk` (called by `<OrderRiskGate>` below). The chain
  // hands raw user-entered legs in; the gate folds coverage, fixes per-combo
  // ratios, applies `netPremiumAdjustment` for stock-backed covered calls.
  // The old in-line augmentation + computeOrderRisk has moved behind
  // `@/lib/order/risk` and is ESLint-banned at every call site outside that
  // module. See `tasks/order-risk-chokepoint-refactor.md`.
  const riskInput: OrderRiskInput | null = useMemo(() => {
    if (!isValidPrice) return null;
    const totalCost = parsedPrice * totalQty * 100;
    const description = `${structure || "Option"} @ ${fmtPrice(parsedPrice)}`;
    // Single-leg orders: debit/credit is STRUCTURALLY determined by action,
    // not by the WS net-price probe. SELL → receive premium → credit;
    // BUY → pay premium → debit. Falling back on `isDebit` (computed from
    // `computeNetPrice` on live WS quotes) breaks on weekends / off-hours
    // when bid/ask are null and `isDebit` is `null` — the old code then
    // defaulted to treating the order as a debit and the resulting risk
    // numbers came out with a flipped credit sign (Max Loss = abs(credit)
    // instead of $0). For multi-leg combos `isDebit` is still meaningful
    // because structure determines the sign.
    const isCredit = isCombo
      ? isDebit === false
      : legs[0]?.action === "SELL";
    const netPremium = isCredit ? -Math.abs(parsedPrice) : parsedPrice;
    // `ChainOrderLeg.quantity` MUST be the raw user-entered contract count
    // (see `augmentOrderLegsWithPortfolioCoverage`'s docblock). The augmenter
    // computes the per-combo ratio itself via `gcd(quantities)`.
    //
    // Regression 2026-05-27 (VIX bull call spread): passing `normalizedOrder.legs`
    // here handed in ratio quantities (e.g. 1/1 for a 500/500 spread). The
    // augmenter then computed `comboQuantity = gcd(1, 1) = 1`, collapsing the
    // 500-contract aggregate into per-combo dollars ($880 / $120 instead of
    // $440k / $60k). Always source from raw `legs`. Multi-leg fuzz property
    // P3b in `tests/fuzz/order-risk.fuzz.test.ts` pins the contract going
    // forward.
    const chainLegs = legs.map((l) => ({
      action: l.action,
      right: l.right,
      strike: l.strike,
      expiry: l.expiry,
      quantity: Math.max(1, Math.trunc(l.quantity)),
    }));
    return {
      ticker,
      chainLegs,
      netPremium,
      description,
      totalCost: isCredit ? -totalCost : totalCost,
      // FU7: thread the live net entry quote so `useOrderRisk` renders
      // net-of-cost max-loss/max-gain via the F1 cost model. `netPrices` are
      // per-combo-unit positive magnitudes (computed from each leg's WS quote)
      // — exactly the entry-quote shape the cost model expects. When bid/ask
      // are null (off-hours), the cost model falls back to its estimated
      // half-spread; the risk verdict is unchanged shape, just cost-aware.
      quote: { bid: netPrices.bid, ask: netPrices.ask },
      // Phase-1 margin estimate: thread the already-resolved underlying spot so
      // a naked single-leg short surfaces a Reg-T requirement (not maxLoss).
      underlyingSpot: spot ?? null,
    };
  }, [isValidPrice, parsedPrice, totalQty, structure, isDebit, legs, ticker, netPrices.bid, netPrices.ask, spot]);

  // Pull the resolved state for the coverage chip + (later) submit gating.
  // Calling `useOrderRisk` directly here is equivalent to the gate; the gate
  // wraps both the hook and the summary render below.
  // Per-combo legs for the exact expiry payoff. Ratio-normalised so the curve
  // describes ONE combo, matching the "RISK · PER 1× COMBO" heading.
  const payoffLegs = useMemo(
    () =>
      quotingLegs.map((leg) => ({
        action: leg.action as "BUY" | "SELL",
        right: leg.right as "C" | "P",
        strike: leg.strike,
        quantity: leg.quantity,
      })),
    [quotingLegs],
  );

  const riskState = useOrderRisk(riskInput, portfolio);
  const submitPermitted = chainOrderSubmitPermitted(isValidPrice, riskState, isCombo, isDebit);

  /**
   * The acknowledgement an operator gives is specific to the order in front of
   * them, so it resets whenever they leave the review step or change the
   * order. A stale tick must never arm a different ticket.
   */
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
  useEffect(() => {
    setRiskAcknowledged(false);
  }, [confirmStep, signedLimitPrice, totalQty, structure, legs.length]);

  /**
   * Real figures for the acknowledgement, from the same exact payoff the risk
   * block draws — where the position turns loss-making, and what it costs if
   * the underlying goes to zero. Boilerplate would be easier to ignore.
   */
  const unboundedLoss = useMemo(() => {
    if (riskState?.summary.maxLossUnbounded !== true) return null;
    const premium = netPremiumForPayoff(payoffLegs, isCombo, signedLimitPrice);
    const curve = payoffCurve(payoffLegs, premium, { spot: spot ?? 0 });
    const turn = curve.breakevens.length > 0 ? curve.breakevens[curve.breakevens.length - 1] : null;
    const atZero = payoffAtExpiry(payoffLegs, premium, 0) * 100 * (totalQty || 1);
    const parts: string[] = [];
    if (turn != null) parts.push(`Loss grows without limit beyond ${turn.toFixed(2)}`);
    if (Number.isFinite(atZero) && atZero < 0) {
      parts.push(`and reaches ${fmtPrice(Math.abs(atZero))} at zero`);
    }
    return { sentence: parts.length > 0 ? `${parts.join(" ")}.` : "Loss grows without limit." };
  }, [riskState, payoffLegs, isCombo, signedLimitPrice, spot, totalQty]);

  /** Bounded risk needs no acknowledgement; unbounded risk needs an explicit one. */
  const transmitArmed = unboundedLoss == null || riskAcknowledged;

  const handlePlace = useCallback(async () => {
    if (!confirmStep) {
      if (!submitPermitted) return;
      setConfirmStep(true);
      return;
    }
    if (!submitPermitted) return;
    // Defence in depth: the disabled button is UI, this is the actual gate.
    // An unbounded-risk order never reaches the wire unacknowledged.
    if (!transmitArmed) return;

    setLoading(true);
    setError(null);

    try {
      const isCombo = legs.length > 1;
      const comboOrder = normalizedOrder ?? normalizeComboOrder(legs);
      const body = isCombo
        ? {
            type: "combo",
            symbol: ticker,
            action: getComboEntryAction(comboOrder.legs),
            quantity: totalQty,
            limitPrice: signedLimitPrice,
            tif,
            legs: comboOrder.legs.map((l) => ({
              symbol: ticker,
              secType: "OPT",
              expiry: normalizeOptionExpiry(l.expiry) ?? l.expiry,
              strike: l.strike,
              right: l.right === "C" ? "CALL" : "PUT",
              action: l.action,
              ratio: l.quantity,
              ...(l.limitPrice != null ? { limitPrice: l.limitPrice } : {}),
            })),
          }
        : {
            type: "option",
            symbol: ticker,
            action: legs[0].action,
            quantity: legs[0].quantity,
            limitPrice: parsedPrice,
            tif,
            expiry: normalizeOptionExpiry(legs[0].expiry) ?? legs[0].expiry,
            strike: legs[0].strike,
            right: legs[0].right === "C" ? "CALL" : "PUT",
          };

      const res = await fetch("/api/orders/place", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const json = await res.json();
      if (!res.ok) {
        setError(json.error || "Order placement failed");
      } else {
        const feedback = placeOrderFeedback(json, `Order placed: ${structure || "Option"} on ${ticker}`);
        orderActions?.pushNotification({ type: feedback.tone, message: feedback.message });
        setConfirmStep(false);
      }
    } catch {
      setError("Network error placing order");
    } finally {
      setLoading(false);
    }
  }, [
    confirmStep,
    ticker,
    legs,
    parsedPrice,
    normalizedOrder,
    totalQty,
    tif,
    structure,
    signedLimitPrice,
    submitPermitted,
  ]);

  // OrderPriceStrip prices (combo) or single-leg signed mid
  const stripPrices = useMemo(() => {
    const { bid, ask, mid } = signedNetPrices;
    if (bid == null || ask == null || mid == null) {
      return { bid: null, mid: null, ask: null, spread: null, spreadPct: null, available: false };
    }
    const spread = ask - bid;
    const midAbs = Math.abs(mid);
    const spreadPct = midAbs > 0 ? (Math.abs(spread) / midAbs) * 100 : null;
    return { bid, mid, ask, spread, spreadPct, available: true };
  }, [signedNetPrices]);

  const selectedQuoteSide = useMemo((): OrderQuoteSide | null => {
    if (!isValidPrice || !Number.isFinite(signedLimitPrice)) return null;
    const sides: OrderQuoteSide[] = ["bid", "mid", "ask"];
    for (const side of sides) {
      const v = signedNetPrices[side];
      if (v != null && Math.abs(v - signedLimitPrice) < 0.005) return side;
    }
    return null;
  }, [isValidPrice, signedLimitPrice, signedNetPrices]);

  const applyQuoteSide = useCallback(
    (side: OrderQuoteSide, value: number) => {
      setLimitPrice(value.toFixed(2));
      setPriceManuallySet(true);
      setConfirmStep(false);
    },
    [],
  );

  const riskTeaser = useMemo(() => {
    if (!isValidPrice || riskState == null) return null;
    const s = riskState.summary;
    const creditDebit =
      isDebit === false
        ? `credit ${fmtPrice(Math.abs(signedLimitPrice))}`
        : isDebit === true
          ? `debit ${fmtPrice(Math.abs(signedLimitPrice))}`
          : fmtPrice(signedLimitPrice);
    const notional = fmtPrice(signedLimitPrice * totalQty * 100);
    let maxPart: string;
    if (s.maxLossUnbounded) {
      maxPart = "max loss UNBOUNDED";
    } else if (s.maxLoss != null && Number.isFinite(s.maxLoss)) {
      maxPart = `max loss ${fmtPrice(s.maxLoss)}`;
    } else if (riskState.coverageStatus === "pending") {
      maxPart = "coverage pending";
    } else {
      maxPart = "max loss --";
    }
    return `${creditDebit} · notional ${notional} · ${maxPart}`;
  }, [isValidPrice, riskState, isDebit, signedLimitPrice, totalQty]);

  if (legs.length === 0) return null;

  return (
    <div
      className="order-builder order-builder--rail"
      ref={builderRef}
      data-prefilled={prefillLabel ? "true" : undefined}
    >
      <div className="order-builder-section order-builder-section--structure">
        <div className="order-builder-header">
          <span className="order-builder-title">
            ORDER BUILDER{structure ? ` : ${structure}` : ""}
          </span>
          <button
            type="button"
            className="btn-secondary order-builder-clear"
            onClick={() => {
              onClearLegs();
              setConfirmStep(false);
              setLimitPrice("");
              setPriceManuallySet(false);
              setError(null);
            }}
          >
            Clear
          </button>
        </div>

        {prefillLabel && (
          <div className="order-builder-prefill order-builder-prefill--chip" role="status">
            {prefillLabel}
          </div>
        )}

        {riskState != null && riskState.coveringLegs.length > 0 && (
          <div className="order-builder-coverage" data-testid="order-builder-coverage">
            COVERED BY HELD{" "}
            {riskState.coveringLegs
              .map((l) =>
                l.type === "Option"
                  ? `LONG ${l.contracts}× $${l.strike} ${l.right === "C" ? "Call" : "Put"}`
                  : `${l.shares.toLocaleString()} shares @ $${l.avgCost.toFixed(2)}`,
              )
              .join(" + ")}
          </div>
        )}

        {isBearishRiskReversal(legs) && (
          <div
            className="order-builder-routing-warning"
            role="status"
            data-testid="order-builder-bearish-rr-warning"
          >
            <div className="order-builder-routing-warning__title">
              HEADS-UP: BEARISH RISK REVERSAL ROUTING
            </div>
            <div>
              IB Smart sometimes silently drops this combo. If the order
              sits in PendingSubmit with no permId after submit, place the
              legs separately (SELL the call as one order, BUY the put as
              another). Both transmit fine as singletons.
            </div>
          </div>
        )}
      </div>

      {/* Market: full quote telemetry above the tappable quote surface */}
      {(quotePriceData != null || (isCombo && stripPrices.available)) && (
        <div className="order-builder-section order-builder-section--market">
          <OrderQuoteTelemetry
            priceData={quotePriceData}
            label={quoteLabel}
            density="tight"
          />
          {isCombo && stripPrices.available && (
            <OrderPriceStrip
              prices={stripPrices}
              selected={selectedQuoteSide}
              onSelect={applyQuoteSide}
            />
          )}
        </div>
      )}

      <div className="order-builder-section order-builder-section--analytics">
        <ComboSkewPanel
          ticker={ticker}
          legs={legs}
          prices={prices}
          spot={spot}
          riskFreeRate={riskFreeRate}
          compact
        />
      </div>

      {/* Legs: single editable list (no redundant pills) */}
      <div className="order-builder-section order-builder-section--legs">
        <div className="order-builder-section-label">LEGS</div>
        <div className="order-builder-legs">
          {legs.map((leg) => {
            const key = optionKey({
              symbol: ticker,
              expiry: leg.expiry,
              strike: leg.strike,
              right: leg.right,
            });
            const pd = prices[key];
            const mid = pd?.bid != null && pd?.ask != null ? (pd.bid + pd.ask) / 2 : null;
            const legPrice = leg.priceManuallySet || mid == null ? leg.limitPrice : mid;

            return (
              <div key={leg.id} className="order-builder-leg" data-testid="order-builder-leg">
                <button
                  type="button"
                  className={`order-builder-leg-action ${leg.action === "BUY" ? "order-builder-leg-action--buy" : "order-builder-leg-action--sell"}`}
                  onClick={() => {
                    onUpdateLeg(leg.id, { action: leg.action === "BUY" ? "SELL" : "BUY" });
                    setConfirmStep(false);
                  }}
                  title="Toggle buy/sell"
                >
                  {leg.action}
                </button>
                <span
                  className="order-builder-leg-contract"
                  title={`${ticker} ${formatExpiry(leg.expiry)} ${leg.quantity}x $${leg.strike} ${leg.right === "C" ? "Call" : "Put"}`}
                >
                  {leg.quantity}x ${leg.strike} {leg.right === "C" ? "Call" : "Put"}
                </span>
                <span className="order-builder-leg-expiry">{formatExpiry(leg.expiry)}</span>
                <span className="order-builder-leg-mid" title="Leg mid">
                  {mid != null ? fmtPrice(mid) : "--"}
                </span>
                <input
                  className="order-input order-builder-leg-qty"
                  type="number"
                  min="1"
                  step="1"
                  value={leg.quantity}
                  aria-label="Quantity"
                  onChange={(e) => {
                    const v = parseInt(e.target.value, 10);
                    if (v > 0) {
                      onUpdateLeg(leg.id, { quantity: v });
                      setConfirmStep(false);
                    }
                  }}
                />
                {isCombo && (
                  <div className="order-builder-leg-limit">
                    <span className="order-builder-leg-limit-prefix">$</span>
                    <input
                      className="order-input"
                      type="number"
                      min="0.01"
                      step="0.01"
                      value={legPrice == null ? "" : legPrice}
                      aria-label="Leg limit"
                      onChange={(e) => {
                        const v = parseFloat(e.target.value);
                        onUpdateLeg(leg.id, {
                          limitPrice: Number.isFinite(v) ? v : null,
                          priceManuallySet: true,
                        });
                        setPriceManuallySet(true);
                        setConfirmStep(false);
                      }}
                    />
                  </div>
                )}
                <button
                  type="button"
                  className="order-builder-leg-remove"
                  onClick={() => {
                    onRemoveLeg(leg.id);
                    setConfirmStep(false);
                  }}
                  title="Remove leg"
                >
                  ×
                </button>
              </div>
            );
          })}
        </div>
      </div>

      {/* Ticket: limit, TIF, risk teaser, submit */}
      <div className="order-builder-section order-builder-section--ticket">
        <div className="order-builder-net">
          <div className="order-field order-builder-limit-field">
            <label className="order-label">
              Limit price ({isDebit ? "net debit" : "net credit"})
            </label>
            <div className="modify-price-input-row">
              <span className="modify-price-prefix">$</span>
              <input
                className="modify-price-input"
                type="number"
                step="0.01"
                min={isCombo ? "-100000" : "0.01"}
                value={limitPrice}
                onChange={(e) => {
                  setLimitPrice(e.target.value);
                  setPriceManuallySet(true);
                  setConfirmStep(false);
                }}
                placeholder="0.00"
              />
            </div>
            {/* Single-leg: no top strip; keep compact quote chips */}
            {!isCombo && (
              <div className="modify-quick-buttons">
                <button
                  type="button"
                  className="btn-quick"
                  disabled={signedNetPrices.bid == null}
                  onClick={() => {
                    if (signedNetPrices.bid != null) applyQuoteSide("bid", signedNetPrices.bid);
                  }}
                >
                  BID{signedNetPrices.bid != null ? ` ${signedNetPrices.bid.toFixed(2)}` : ""}
                </button>
                <button
                  type="button"
                  className="btn-quick"
                  disabled={signedNetPrices.mid == null}
                  onClick={() => {
                    if (signedNetPrices.mid != null) applyQuoteSide("mid", signedNetPrices.mid);
                  }}
                >
                  MID{signedNetPrices.mid != null ? ` ${signedNetPrices.mid.toFixed(2)}` : ""}
                </button>
                <button
                  type="button"
                  className="btn-quick"
                  disabled={signedNetPrices.ask == null}
                  onClick={() => {
                    if (signedNetPrices.ask != null) applyQuoteSide("ask", signedNetPrices.ask);
                  }}
                >
                  ASK{signedNetPrices.ask != null ? ` ${signedNetPrices.ask.toFixed(2)}` : ""}
                </button>
              </div>
            )}
            {isValidPrice && (
              <span className="order-builder-notional">
                {fmtPrice(signedLimitPrice * totalQty * 100)} notional
              </span>
            )}
          </div>
        </div>

        <div className="order-field order-builder-tif">
          <label className="order-label">Time in force</label>
          <div className="order-builder-tif-seg" role="group" aria-label="Time in force">
            <button
              type="button"
              className={`order-builder-tif-btn${tif === "DAY" ? " order-builder-tif-btn--active" : ""}`}
              onClick={() => setTif("DAY")}
            >
              DAY
            </button>
            <button
              type="button"
              className={`order-builder-tif-btn${tif === "GTC" ? " order-builder-tif-btn--active" : ""}`}
              onClick={() => setTif("GTC")}
            >
              GTC
            </button>
          </div>
        </div>

        <OrderErrorBanner error={error} />

        {/* Risk sits ABOVE the CTA so unbounded loss is read before the button,
            not after it. Presentation only — the gate below remains the
            chokepoint that decides whether the order may be submitted. */}
        {riskState && (
          <TicketRiskBlock
            legs={payoffLegs}
            netPremium={netPremiumForPayoff(payoffLegs, isCombo, signedLimitPrice)}
            spot={spot ?? 0}
            maxGain={riskState.summary.maxGain ?? null}
            maxLoss={riskState.summary.maxLoss ?? null}
            maxLossUnbounded={riskState.summary.maxLossUnbounded === true}
            marginRequirement={riskState.summary.marginImpact?.requirement ?? null}
            fundsAfter={riskState.summary.marginImpact?.availableAfter ?? null}
            total={riskState.summary.totalCost ?? null}
            totalLabel={riskState.summary.totalLabel ?? "TOTAL"}
            isCredit={netPremiumForPayoff(payoffLegs, isCombo, signedLimitPrice) < 0}
          />
        )}

        {confirmStep && (
          <OrderRiskGate
            input={riskInput}
            portfolio={portfolio}
            surface="chain-builder"
            variant="info"
          />
        )}

        {!confirmStep && riskTeaser && (
          <div className="order-builder-risk-teaser" data-testid="order-builder-risk-teaser">
            {riskTeaser}
          </div>
        )}

        {/* Unbounded risk must be acknowledged before transmit arms. This only
            ever ADDS a condition on top of `submitPermitted` — the risk gate
            stays the chokepoint and this can never loosen it. */}
        {confirmStep && unboundedLoss && (
          <div className="ticket-unbounded" data-testid="ticket-unbounded-warning">
            <label className="ticket-unbounded-row">
              <input
                type="checkbox"
                data-testid="ticket-unbounded-ack"
                checked={riskAcknowledged}
                onChange={(e) => setRiskAcknowledged(e.target.checked)}
              />
              <span>
                MAX LOSS UNBOUNDED. {unboundedLoss.sentence} Acknowledge to enable transmit.
              </span>
            </label>
          </div>
        )}

        <div className="order-submit order-builder-submit">
          {confirmStep ? (
            <div className="order-confirm-row">
              <button
                type="button"
                className="btn-secondary"
                data-testid="ticket-back"
                onClick={() => setConfirmStep(false)}
                disabled={loading}
              >
                Back
              </button>
              <button
                type="button"
                className={`btn-primary ${isDebit === false ? "btn-danger" : ""}`}
                data-testid="ticket-transmit"
                onClick={handlePlace}
                disabled={loading || !submitPermitted || !transmitArmed}
              >
                {loading
                  ? "Transmitting..."
                  : transmitArmed
                    ? `Transmit ${structure || "Order"}`
                    : "Transmit — awaiting acknowledgement"}
              </button>
            </div>
          ) : (
            <>
              <button
                type="button"
                className="btn-primary order-builder-place"
                data-testid="ticket-verify"
                onClick={handlePlace}
                disabled={!submitPermitted}
              >
                Verify order →
              </button>
              <div className="ticket-step-hint">
                <span>STEP 1 OF 2 · TRANSMIT FOLLOWS REVIEW</span>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ─── Main OptionsChainTab ─── */

export default function OptionsChainTab({
  ticker,
  prices,
  tickerPriceData,
  focusPosition = null,
  focusPositionRequested = false,
  portfolio = null,
}: OptionsChainTabProps) {
  const [expirations, setExpirations] = useState<string[]>([]);
  const [selectedExpiry, setSelectedExpiry] = useState<string | null>(null);
  const [strikes, setStrikes] = useState<number[]>([]);
  const [loadingExpiries, setLoadingExpiries] = useState(false);
  const [loadingStrikes, setLoadingStrikes] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [orderLegs, setOrderLegs] = useState<OrderLeg[]>([]);

  /**
   * Strikes staged in the ticket, per side. Drives the chain tint so the
   * operator can see which contracts are in the order without reading the
   * ticket back. Keyed by strike; a call and a put at the same strike are
   * tracked separately.
   */
  const stagedSides = useMemo(() => {
    const map = new Map<number, { call: boolean; put: boolean }>();
    for (const leg of orderLegs) {
      const current = map.get(leg.strike) ?? { call: false, put: false };
      if (leg.right === "C") current.call = true;
      if (leg.right === "P") current.put = true;
      map.set(leg.strike, current);
    }
    return map;
  }, [orderLegs]);

  // Filter state is deep-linked into the URL (?expiry=&side=&strikes=) — seed
  // from the URL on mount, write back on change. See useChainUrlState.
  const chainUrl = useChainUrlState();
  const searchParams = useSearchParams();
  const [strikesPerSide, setStrikesPerSide] = useState(() => chainUrl.initialStrikes);
  const [sideFilter, setSideFilter] = useState<SideFilter>(() => chainUrl.initialSide);
  const { isMobile, hasMounted } = useViewport();
  const showMobileChain = isMobile && hasMounted;
  const riskFreeRate = useRiskFreeRate();
  const atmRef = useRef<HTMLTableRowElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const initialFocusAppliedRef = useRef(false);
  const appliedLegsParamRef = useRef<string | null>(null);
  const orderBuilderRef = useRef<HTMLDivElement>(null);
  const [prefillLabel, setPrefillLabel] = useState<string | null>(null);

  const focusedExpiry = useMemo(
    () => (focusPosition ? normalizeOptionExpiry(focusPosition.expiry) : null),
    [focusPosition],
  );

  // Background prefetch of all expirations for instant switching
  const { cacheStrikes, getCachedStrikes } = useChainPrefetch(
    ticker,
    expirations,
    selectedExpiry,
  );

  // Fetch expirations on mount
  useEffect(() => {
    let cancelled = false;
    initialFocusAppliedRef.current = false;
    appliedLegsParamRef.current = null;
    setPrefillLabel(null);
    setLoadingExpiries(true);
    setError(null);

    fetch(`/api/options/expirations?symbol=${encodeURIComponent(ticker)}`, {
      cache: "no-store",
    })
      .then((res) => res.json())
      .then((data) => {
        if (cancelled) return;
        if (data.error) {
          setError(optionFetchErrorMessage(data, "Option expirations unavailable"));
          setLoadingExpiries(false);
          return;
        }
        const exps: string[] = data.expirations ?? [];
        setExpirations(exps);
        setLoadingExpiries(false);
      })
      .catch(() => {
        if (!cancelled) {
          setError("Failed to fetch expirations");
          setLoadingExpiries(false);
        }
      });

    return () => { cancelled = true; };
  }, [ticker]);

  useEffect(() => {
    if (initialFocusAppliedRef.current) return;
    if (expirations.length === 0) return;
    if (focusPositionRequested && !focusedExpiry) return;

    // Priority: explicit URL deep-link > focused position > first >=7 DTE > first.
    // URL expiry arrives dashed (2026-07-17); compare in compact internal form.
    const urlExpiryCompact = chainUrl.urlExpiry ? normalizeOptionExpiry(chainUrl.urlExpiry) : null;
    const nextExpiry =
      (urlExpiryCompact && expirations.includes(urlExpiryCompact) ? urlExpiryCompact : null) ??
      (focusedExpiry && expirations.includes(focusedExpiry) ? focusedExpiry : null) ??
      expirations.find((expiry) => daysToExpiry(expiry) >= 7) ??
      expirations[0] ??
      null;

    if (nextExpiry) {
      setSelectedExpiry(nextExpiry);
    }
    initialFocusAppliedRef.current = true;
  }, [expirations, focusedExpiry, chainUrl.urlExpiry]);

  useEffect(() => {
    const signature = chainUrl.legsParamRaw ?? "";
    if (!selectedExpiry || !signature || chainUrl.urlLegs.length === 0) return;
    if (appliedLegsParamRef.current === signature) return;

    const requestedExpiry = chainUrl.urlExpiry ? normalizeOptionExpiry(chainUrl.urlExpiry) : null;
    if (requestedExpiry && requestedExpiry !== selectedExpiry) return;

    const nextLegs: OrderLeg[] = chainUrl.urlLegs.map((leg) => ({
      id: `${ticker}_${selectedExpiry}_${leg.strike}_${leg.right}`,
      action: leg.action,
      right: leg.right,
      strike: leg.strike,
      expiry: selectedExpiry,
      quantity: leg.quantity,
      limitPrice: null,
      priceManuallySet: false,
    }));

    setOrderLegs(nextLegs);
    setPrefillLabel(
      searchParams?.get("src") === "vol-cone"
        ? "PREFILLED FROM VOL CONE"
        : "PREFILLED FROM THETA HARVESTER",
    );
    appliedLegsParamRef.current = signature;
  }, [ticker, selectedExpiry, chainUrl.legsParamRaw, chainUrl.urlExpiry, chainUrl.urlLegs, searchParams]);

  useEffect(() => {
    if (!prefillLabel || orderLegs.length === 0) return;
    const scrollToBuilder = () => {
      orderBuilderRef.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
    };
    if (typeof window.requestAnimationFrame !== "function") {
      scrollToBuilder();
      return;
    }
    const raf = window.requestAnimationFrame(scrollToBuilder);
    return () => window.cancelAnimationFrame(raf);
  }, [prefillLabel, orderLegs.length]);

  // Write filter state → URL after commit (preserves tab + other params).
  // Gated until the initial expiry has been resolved so we don't strip a
  // deep-linked ?expiry before it's been validated against the expiry list.
  useEffect(() => {
    if (!initialFocusAppliedRef.current) return;
    chainUrl.syncUrl({ selectedExpiry, side: sideFilter, strikes: strikesPerSide });
    // Depend on the memoized `chainUrl.syncUrl`, NOT the whole `chainUrl` object
    // (recreated every render — would run this effect each render). syncUrl is
    // stable except when the URL/router actually changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedExpiry, sideFilter, strikesPerSide, chainUrl.syncUrl]);

  // Reconcile from external URL changes (back/forward, manual edit) for the
  // mount-seeded filters, mirroring useNewsfeedTagFilter. setState to the same
  // value is a no-op, so this never loops with the write effect above.
  useEffect(() => {
    setSideFilter(parseSideParam(chainUrl.sideParamRaw));
  }, [chainUrl.sideParamRaw]);
  useEffect(() => {
    setStrikesPerSide(parseStrikesParam(chainUrl.strikesParamRaw));
  }, [chainUrl.strikesParamRaw]);

  // Fetch strikes when expiry changes — check prefetch cache first
  useEffect(() => {
    if (!selectedExpiry) return;

    // Use cached strikes if available (from background prefetch)
    const cached = getCachedStrikes(selectedExpiry);
    if (cached) {
      setStrikes(cached);
      setLoadingStrikes(false);
      return;
    }

    let cancelled = false;
    setLoadingStrikes(true);

    fetch(`/api/options/chain?symbol=${encodeURIComponent(ticker)}&expiry=${selectedExpiry}`, {
      cache: "no-store",
    })
      .then((res) => res.json())
      .then((data) => {
        if (cancelled) return;
        if (data.error) {
          setError(optionFetchErrorMessage(data, "Option chain unavailable"));
          setLoadingStrikes(false);
          return;
        }
        const fetchedStrikes: number[] = data.strikes ?? [];
        setStrikes(fetchedStrikes);
        cacheStrikes(selectedExpiry, fetchedStrikes);
        setLoadingStrikes(false);
      })
      .catch(() => {
        if (!cancelled) {
          setError("Failed to fetch strikes");
          setLoadingStrikes(false);
        }
      });

    return () => { cancelled = true; };
    // getCachedStrikes and cacheStrikes are stable refs — omit from deps
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker, selectedExpiry]);

  // Fetch actual previous close when WS last is unavailable (market closed).
  // IB's close tick is the PREVIOUS session's close and can be 2+ days stale
  // on weekends, so we fetch from UW/Yahoo via the previous-close API instead.
  const [prevClose, setPrevClose] = useState<number | null>(null);
  useEffect(() => {
    if (tickerPriceData?.last != null) {
      setPrevClose(null); // live price available, don't need prev close
      return;
    }
    let cancelled = false;
    fetch("/api/previous-close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbols: [ticker] }),
    })
      .then((r) => r.json())
      .then((d) => {
        if (!cancelled && d.closes?.[ticker] != null) {
          setPrevClose(d.closes[ticker]);
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [ticker, tickerPriceData?.last]);

  // Determine ATM strike
  const currentPrice = tickerPriceData?.last ?? prevClose ?? null;
  const priceIsClose = tickerPriceData?.last == null && prevClose != null;
  const atmStrike = useMemo(() => {
    if (currentPrice == null) return null;
    return findAtmStrike(strikes, currentPrice);
  }, [currentPrice, strikes]);

  const focusedStrike = useMemo(() => {
    if (!focusPosition || !focusedExpiry || focusedExpiry !== selectedExpiry) return null;
    const positionStrikes = focusPosition.legs
      .map((leg) => leg.strike)
      .filter((strike): strike is number => strike != null && Number.isFinite(strike) && strike > 0);
    if (positionStrikes.length === 0) return null;
    if (currentPrice == null) return positionStrikes[0];

    return positionStrikes.reduce((closest, strike) => (
      Math.abs(strike - currentPrice) < Math.abs(closest - currentPrice) ? strike : closest
    ));
  }, [focusPosition, focusedExpiry, selectedExpiry, currentPrice]);

  // Filter strikes around ATM
  const visibleStrikes = useMemo<ChainStrike[]>(() => {
    if (!selectedExpiry || strikes.length === 0) return [];
    const anchorStrike = focusedStrike ?? atmStrike;
    const visible = getVisibleStrikes(strikes, anchorStrike, strikesPerSide);
    return visible.map((strike) => ({
      strike,
      callKey: optionKey({ symbol: ticker, expiry: selectedExpiry, strike, right: "C" }),
      putKey: optionKey({ symbol: ticker, expiry: selectedExpiry, strike, right: "P" }),
    }));
  }, [ticker, selectedExpiry, strikes, focusedStrike, atmStrike, strikesPerSide]);

  // Subscribe visible chain contracts for WS price streaming
  const { setChainContracts } = useTickerDetail();
  useEffect(() => {
    if (!selectedExpiry || visibleStrikes.length === 0) {
      setChainContracts([]);
      return;
    }
    // When showing all strikes, cap WS subscriptions to ±50 around ATM to
    // avoid overwhelming the relay with hundreds of simultaneous ticks.
    const WS_CAP = 50;
    const strikesToStream =
      strikesPerSide === ALL_STRIKES
        ? getVisibleStrikes(strikes, focusedStrike ?? atmStrike, WS_CAP)
        : visibleStrikes.map((r) => r.strike);
    const streamSet = new Set(strikesToStream);
    const contracts: OptionContract[] = [];
    for (const row of visibleStrikes) {
      if (!streamSet.has(row.strike)) continue;
      contracts.push({ symbol: ticker, expiry: selectedExpiry, strike: row.strike, right: "C" });
      contracts.push({ symbol: ticker, expiry: selectedExpiry, strike: row.strike, right: "P" });
    }
    setChainContracts(contracts);
    return () => setChainContracts([]);
  }, [ticker, selectedExpiry, visibleStrikes, strikesPerSide, strikes, focusedStrike, atmStrike, setChainContracts]);

  // Center the ATM row inside the chain wrapper only — scrollIntoView would
  // also scroll page-level ancestors, dragging the Order Builder with it.
  useEffect(() => {
    const atmEl = atmRef.current;
    const wrapper = wrapperRef.current;
    if (!atmEl || !wrapper) return;
    const wrapperRect = wrapper.getBoundingClientRect();
    const atmRect = atmEl.getBoundingClientRect();
    const target =
      wrapper.scrollTop +
      (atmRect.top - wrapperRect.top) +
      atmRect.height / 2 -
      wrapper.clientHeight / 2;
    wrapper.scrollTo({ top: Math.max(0, target), behavior: "smooth" });
  }, [visibleStrikes]);

  // Add leg from chain click
  const handleAddLeg = useCallback(
    (strike: number, right: "C" | "P", action: "BUY" | "SELL") => {
      if (!selectedExpiry) return;
      const id = `${ticker}_${selectedExpiry}_${strike}_${right}`;
      // Toggle: if same leg exists with same action, remove it
      const existing = orderLegs.find((l) => l.id === id);
      if (existing) {
        if (existing.action === action) {
          setOrderLegs((prev) => prev.filter((l) => l.id !== id));
          return;
        }
        // Flip action
        setOrderLegs((prev) =>
          prev.map((l) => (l.id === id ? { ...l, action } : l)),
        );
        return;
      }

      const key = optionKey({ symbol: ticker, expiry: selectedExpiry, strike, right });
      const pd = prices[key];
      const mid = pd?.bid != null && pd?.ask != null ? (pd.bid + pd.ask) / 2 : null;

      setOrderLegs((prev) => [
        ...prev,
        {
          id,
          action,
          right,
          strike,
          expiry: selectedExpiry,
          quantity: 1,
          limitPrice: mid,
          priceManuallySet: false,
        },
      ]);
    },
    [ticker, selectedExpiry, orderLegs, prices],
  );

  const handleCallClick = useCallback(
    (strike: number, action: "BUY" | "SELL") => handleAddLeg(strike, "C", action),
    [handleAddLeg],
  );

  const handlePutClick = useCallback(
    (strike: number, action: "BUY" | "SELL") => handleAddLeg(strike, "P", action),
    [handleAddLeg],
  );

  const handleRemoveLeg = useCallback((id: string) => {
    setOrderLegs((prev) => prev.filter((l) => l.id !== id));
  }, []);

  const handleUpdateLeg = useCallback((id: string, updates: Partial<OrderLeg>) => {
    setOrderLegs((prev) =>
      prev.map((l) => (l.id === id ? { ...l, ...updates } : l)),
    );
  }, []);

  const handleClearLegs = useCallback(() => {
    setOrderLegs([]);
    setPrefillLabel(null);
  }, []);

  // Collect option keys the chain needs subscribed
  // (The parent usePrices hook subscribes based on contracts — we'd need
  //  to lift these up. For now the chain shows WS data if already subscribed.)

  if (loadingExpiries) {
    return (
      <div style={{ padding: "24px 0", textAlign: "center" }}>
        <SpectralLoader label="Loading expirations" />
      </div>
    );
  }

  if (error && expirations.length === 0) {
    return (
      <div style={{ padding: "24px 0", textAlign: "center" }}>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", color: "var(--fault)" }}>
          {error}
        </span>
      </div>
    );
  }

  if (showMobileChain) {
    return (
      <MobileChainLadder
        ticker={ticker}
        expirations={expirations}
        selectedExpiry={selectedExpiry}
        onSelectExpiry={(expiry) => setSelectedExpiry(expiry)}
        visibleStrikes={visibleStrikes}
        atmStrike={atmStrike}
        prices={prices}
        currentPrice={currentPrice}
        loading={loadingStrikes}
        sideFilter={sideFilter}
        onSideFilterChange={setSideFilter}
        strikesPerSide={strikesPerSide}
        onStrikesPerSideChange={setStrikesPerSide}
        orderLegs={orderLegs}
        riskFreeRate={riskFreeRate}
        onAddLeg={handleAddLeg}
        onRemoveLeg={handleRemoveLeg}
        onUpdateLeg={handleUpdateLeg}
        onClearLegs={handleClearLegs}
        portfolio={portfolio ?? null}
      />
    );
  }

  return (
    <div className="chain-tab" style={{ padding: "8px 0" }}>
      {/* Expiry selector */}
      <div className="chain-expiry-bar">
        <label
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "10px",
            textTransform: "uppercase",
            letterSpacing: "0.05em",
            color: "var(--text-secondary)",
          }}
        >
          EXPIRY
        </label>
        <select
          className="chain-expiry-select"
          value={selectedExpiry ?? ""}
          // Browsing expiries must NOT wipe the builder — legs carry their own
          // expiry through to the combo payload (calendars are legitimate).
          // CLEAR is the only thing that empties it.
          onChange={(e) => setSelectedExpiry(e.target.value || null)}
        >
          {expirations.map((exp) => (
            <option key={exp} value={exp}>
              {formatExpiry(exp)} ({daysToExpiry(exp)}d)
            </option>
          ))}
        </select>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--text-secondary)" }}>
          {currentPrice != null
            ? `${priceIsClose ? "Prev Close" : "Underlying"}: ${fmtPrice(currentPrice)}`
            : ""}
        </span>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "8px" }}>
          <div className="chain-side-toggle">
            {(["both", "calls", "puts"] as const).map((val) => (
              <button
                key={val}
                className={`chain-side-toggle-btn ${sideFilter === val ? "active" : ""}`}
                onClick={() => setSideFilter(val)}
              >
                {val === "both" ? "ALL" : val.toUpperCase()}
              </button>
            ))}
          </div>
          <label style={{ fontFamily: "var(--font-mono)", fontSize: "10px", color: "var(--text-secondary)" }}>
            STRIKES
          </label>
          <select
            className="chain-expiry-select"
            value={strikesPerSide}
            onChange={(e) => setStrikesPerSide(Number(e.target.value))}
            style={{ width: "56px" }}
          >
            <option value={10}>±10</option>
            <option value={15}>±15</option>
            <option value={25}>±25</option>
            <option value={50}>±50</option>
            <option value={100}>±100</option>
            <option value={ALL_STRIKES}>All</option>
          </select>
        </div>
      </div>

      {/* Chain grid + docked ticket rail. The ticket sits BESIDE the chain
          rather than under it, so legs, price, risk and CTA stay readable
          without scrolling and the chain keeps its full height. */}
      <div className="chain-rail" data-docked={orderLegs.length > 0 ? "true" : "false"}>
      {/* One grid child per column: the chain and its hint row travel together,
          otherwise the hint becomes a third child and wraps the dock below. */}
      <div className="chain-rail-main">
      {loadingStrikes ? (
        <div style={{ padding: "24px 0", textAlign: "center" }}>
          <SpectralLoader label="Loading chain" />
        </div>
      ) : (
        <div className="chain-grid-wrapper" ref={wrapperRef}>
          <table className="chain-grid" data-sortable-exempt="chain-layout">
            <thead>
              <tr>
                {sideFilter !== "puts" && (
                  <>
                    <th className="chain-header">Δ</th>
                    <th className="chain-header">IV</th>
                    <th className="chain-header" title="Black-Scholes implied (theoretical) per-share price">
                      Implied
                    </th>
                    <th className="chain-header">Vol</th>
                    <th className="chain-header">Bid</th>
                    <th className="chain-header chain-header-mid">Mid</th>
                    <th className="chain-header">Ask</th>
                    <th className="chain-header">Last</th>
                  </>
                )}
                <th className="chain-header chain-header-strike">Strike</th>
                {sideFilter !== "calls" && (
                  <>
                    <th className="chain-header">Last</th>
                    <th className="chain-header">Bid</th>
                    <th className="chain-header chain-header-mid">Mid</th>
                    <th className="chain-header">Ask</th>
                    <th className="chain-header">Vol</th>
                    <th className="chain-header" title="Black-Scholes implied (theoretical) per-share price">
                      Implied
                    </th>
                    <th className="chain-header">IV</th>
                    <th className="chain-header">Δ</th>
                  </>
                )}
              </tr>
              <tr>
                {sideFilter !== "puts" && <th className="chain-side-label" colSpan={8}>CALLS</th>}
                <th className="chain-side-label" />
                {sideFilter !== "calls" && <th className="chain-side-label" colSpan={8}>PUTS</th>}
              </tr>
            </thead>
            <tbody>
              {visibleStrikes.map((row) => {
                const isAtm = row.strike === atmStrike;
                return (
                  <StrikeRow
                    key={row.strike}
                    ticker={ticker}
                    expiry={selectedExpiry!}
                    strike={row.strike}
                    callKey={row.callKey}
                    putKey={row.putKey}
                    prices={prices}
                    isAtm={isAtm}
                    onClickCall={handleCallClick}
                    onClickPut={handlePutClick}
                    atmRef={isAtm ? atmRef : undefined}
                    sideFilter={sideFilter}
                    riskFreeRate={riskFreeRate}
                    staged={stagedSides.get(row.strike)}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {orderLegs.length > 0 && (
        <div className="chain-rail-hint">
          <span>CLICK BID/ASK → ADDS LEG TO TICKET</span>
          <span>SELECTED LEGS HIGHLIGHTED IN CHAIN</span>
        </div>
      )}
      </div>

      {/* Order Builder — the dock */}
      <OrderBuilder
        ticker={ticker}
        legs={orderLegs}
        prices={prices}
        spot={currentPrice}
        riskFreeRate={riskFreeRate}
        portfolio={portfolio}
        builderRef={orderBuilderRef}
        prefillLabel={prefillLabel}
        onRemoveLeg={handleRemoveLeg}
        onUpdateLeg={handleUpdateLeg}
        onClearLegs={handleClearLegs}
      />
      </div>
    </div>
  );
}

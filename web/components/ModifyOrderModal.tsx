"use client";

import { useEffect, useMemo, useState } from "react";
import type { OpenOrder, PortfolioData, PortfolioLeg } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { oldestQuoteTimestamp, optionKey } from "@/lib/pricesProtocol";
import type { ModifyComboLeg, ModifyOrderRequest } from "@/lib/orderModify";
import Modal from "./Modal";
import { getQuoteMetrics } from "@/lib/quoteTelemetry";
import { applyRestingLimitToQuote } from "@/lib/modifyOrderQuote";
import { fmtPrice, legPriceKey, resolveEntryCost } from "@/lib/positionUtils";
import {
  findHeldComboForClose,
  heldComboUnits,
  workingSellComboUnits,
  type ComboStructureLeg,
} from "@/lib/order/positionTrade";
import { computeLegImpliedValue } from "@/lib/impliedValue";
import { useRiskFreeRate } from "@/lib/useRiskFreeRate";
import { OrderQuoteTelemetry } from "./QuoteTelemetry";
import {
  OrderLegPills,
  OrderRiskGate,
  type OrderLeg as UnifiedOrderLeg,
  type OrderRiskInput,
  type ChainOrderLeg,
  useOrderRisk,
} from "@/lib/order";

export type EditableComboLeg = {
  action: "BUY" | "SELL";
  expiry: string;
  strike: string;
  right: "C" | "P";
  ratio: string;
};

type ModifyOrderModalProps = {
  order: OpenOrder | null;
  loading: boolean;
  prices?: Record<string, PriceData>;
  portfolio?: PortfolioData | null;
  /** R-112: the open-orders snapshot, so a close-out can see what is
   *  already working on the same BAG. */
  openOrders?: { open_orders?: unknown[] } | null;
  onConfirm: (request: ModifyOrderRequest) => void;
  onClose: () => void;
};

function normalizeLegAction(action?: string | null): "BUY" | "SELL" {
  return action === "SELL" ? "SELL" : "BUY";
}

function normalizeLegRight(right?: string | null): "C" | "P" {
  return right === "P" || right === "PUT" ? "P" : "C";
}

function normalizeExpiry(expiry?: string | null): string {
  if (!expiry) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(expiry)) return expiry;
  const clean = expiry.replace(/-/g, "");
  if (clean.length === 8) {
    return `${clean.slice(0, 4)}-${clean.slice(4, 6)}-${clean.slice(6, 8)}`;
  }
  return expiry;
}

function parsePositiveInteger(value: string): number | null {
  if (!/^[1-9]\d*$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

function resolveClosingOptionLeg(
  order: OpenOrder,
  quantity: number,
  portfolio?: PortfolioData | null,
): PortfolioLeg | null {
  const contract = order.contract;
  if (!portfolio || contract.secType !== "OPT" || contract.strike == null || !contract.expiry) {
    return null;
  }

  const right = contract.right === "P" || contract.right === "PUT"
    ? "P"
    : contract.right === "C" || contract.right === "CALL"
      ? "C"
      : null;
  if (!right) return null;
  const expiry = contract.expiry.replace(/-/g, "");
  const symbol = contract.symbol.toUpperCase();
  for (const position of portfolio.positions) {
    if (position.ticker.toUpperCase() !== symbol) continue;
    if (position.expiry.replace(/-/g, "") !== expiry) continue;
    for (const leg of position.legs) {
      if (leg.strike !== contract.strike) continue;
      if (leg.type !== (right === "C" ? "Call" : "Put")) continue;
      if (!Number.isFinite(leg.avg_cost) || quantity > Math.abs(leg.contracts)) continue;
      if (order.action === "SELL" && leg.direction === "LONG") return leg;
      if (order.action === "BUY" && leg.direction === "SHORT") return leg;
    }
  }
  return null;
}

function buildEditableComboLegs(order: OpenOrder | null): EditableComboLeg[] {
  if (!order?.contract.comboLegs?.length) return [];
  return order.contract.comboLegs.map((leg) => ({
    action: normalizeLegAction(leg.action),
    expiry: normalizeExpiry(leg.expiry),
    strike: leg.strike != null ? String(leg.strike) : "",
    right: normalizeLegRight(leg.right),
    ratio: String(leg.ratio ?? 1),
  }));
}

function comboUnderlyingSymbol(order: OpenOrder): string {
  const comboSymbol = order.contract.comboLegs?.find((leg) => leg.symbol)?.symbol;
  if (comboSymbol) return comboSymbol.toUpperCase();

  const contractSymbol = order.contract.symbol?.replace(/\s+spread$/i, "").trim();
  if (contractSymbol) return contractSymbol.toUpperCase();

  return order.symbol.replace(/\s+spread$/i, "").trim().toUpperCase();
}

export function normalizeComboLegs(legs: EditableComboLeg[]): ModifyComboLeg[] | null {
  const normalized = legs.map((leg) => {
    const strike = Number.parseFloat(leg.strike);
    const ratio = parsePositiveInteger(leg.ratio);
    const expiry = leg.expiry.replace(/-/g, "");
    if (!Number.isFinite(strike) || strike <= 0 || ratio == null || expiry.length !== 8) {
      return null;
    }
    return {
      action: leg.action,
      expiry,
      strike,
      right: leg.right,
      ratio,
    } satisfies ModifyComboLeg;
  });

  return normalized.every((leg): leg is ModifyComboLeg => leg != null) ? normalized : null;
}

export function effectiveComboLegAction(
  structuralAction: "BUY" | "SELL",
  envelopeAction: "BUY" | "SELL",
): "BUY" | "SELL" {
  if (envelopeAction === "BUY") return structuralAction;
  return structuralAction === "BUY" ? "SELL" : "BUY";
}

export function resolveOrderPriceData(
  order: OpenOrder,
  prices?: Record<string, PriceData>,
  portfolio?: PortfolioData | null,
  editedLegs?: EditableComboLeg[],
): PriceData | null {
  if (!prices) return null;
  const c = order.contract;

  // STK: use ticker symbol key
  if (c.secType === "STK") {
    return prices[c.symbol] ?? null;
  }

  // OPT: build composite key
  if (c.secType === "OPT" && c.strike != null && c.right && c.expiry) {
    const expiryClean = c.expiry.replace(/-/g, "");
    if (expiryClean.length === 8) {
      const key = optionKey({
        symbol: c.symbol.toUpperCase(),
        expiry: expiryClean,
        strike: c.strike,
        right: c.right as "C" | "P",
      });
      return prices[key] ?? null;
    }
  }

  // BAG: compute net bid/ask/mid from combo legs (order data or portfolio fallback)
  // Natural market calculation:
  //   netBid = proceeds if we SELL at market (receive bid on BUY legs, pay ask on SELL legs)
  //   netAsk = cost if we BUY at market (pay ask on BUY legs, receive bid on SELL legs)
  if (c.secType === "BAG") {
    let netBid = 0;
    let netAsk = 0;
    let netLast = 0;
    let resolved = false;
    // Freshness of the BAG net is the freshness of its stalest leg, never the
    // wall clock: the operator confirms a replacement limit against this quote.
    let legQuotes: PriceData[] = [];

    // Primary: use combo legs from the order itself (resolved during sync)
    const quoteLegs = editedLegs?.length
      ? editedLegs.map((leg) => ({ ...leg, symbol: comboUnderlyingSymbol(order), ratio: Number(leg.ratio) }))
      : c.comboLegs;
    if (quoteLegs?.length) {
      let allAvailable = true;
      for (const cl of quoteLegs) {
        if (!cl.symbol || cl.strike == null || !cl.right || !cl.expiry) {
          allAvailable = false;
          break;
        }
        const expiryClean = cl.expiry.replace(/-/g, "");
        if (expiryClean.length !== 8) { allAvailable = false; break; }
        const right = cl.right === "C" || cl.right === "P"
          ? cl.right
          : cl.right === "CALL" ? "C" : cl.right === "PUT" ? "P" : null;
        if (!right) { allAvailable = false; break; }
        const key = optionKey({
          symbol: cl.symbol.toUpperCase(),
          expiry: expiryClean,
          strike: Number(cl.strike),
          right,
        });
        const lp = prices[key];
        if (!lp || lp.bid == null || lp.ask == null) { allAvailable = false; break; }
        legQuotes.push(lp);

        // Natural market: BUY leg = pay ask / receive bid, SELL leg = receive bid / pay ask
        const ratio = Number.isInteger(Number(cl.ratio)) && Number(cl.ratio) > 0 ? Number(cl.ratio) : 0;
        if (ratio === 0) { allAvailable = false; break; }
        if (cl.action === "BUY") {
          netAsk += lp.ask * ratio;  // To BUY combo: pay ask on BUY legs
          netBid += lp.bid * ratio;  // To SELL combo: receive bid on BUY legs
        } else {
          netAsk -= lp.bid * ratio;  // To BUY combo: receive bid on SELL legs
          netBid -= lp.ask * ratio;  // To SELL combo: pay ask on SELL legs
        }
        const sign = cl.action === "BUY" ? 1 : -1;
        netLast += sign * ratio * (lp.last ?? (lp.bid + lp.ask) / 2);
      }
      resolved = allAvailable;
    }

    // Fallback: use portfolio position legs
    if (!resolved && portfolio) {
      const pos = portfolio.positions.find(
        (p) => p.ticker === c.symbol && p.legs.length > 1,
      );
      if (pos) {
        netBid = 0;
        netAsk = 0;
        netLast = 0;
        legQuotes = [];
        let allAvailable = true;
        for (const leg of pos.legs) {
          const key = legPriceKey(pos.ticker, pos.expiry, leg);
          if (!key) { allAvailable = false; break; }
          const lp = prices[key];
          if (!lp || lp.bid == null || lp.ask == null) { allAvailable = false; break; }
          legQuotes.push(lp);

          // Natural market: LONG leg = pay ask / receive bid, SHORT leg = receive bid / pay ask
          if (leg.direction === "LONG") {
            netAsk += lp.ask;  // To BUY combo: pay ask on LONG legs
            netBid += lp.bid;  // To SELL combo: receive bid on LONG legs
          } else {
            netAsk -= lp.bid;  // To BUY combo: receive bid on SHORT legs
            netBid -= lp.ask;  // To SELL combo: pay ask on SHORT legs
          }
          const sign = leg.direction === "LONG" ? 1 : -1;
          netLast += sign * (lp.last ?? (lp.bid + lp.ask) / 2);
        }
        resolved = allAvailable;
      }
    }

    if (!resolved) return null;

    // Preserve signed net prices. Risk reversals and other credit combos can
    // have a negative natural market; taking abs() makes a credit look like a
    // debit and blocks valid negative replacement limits.
    const lo = Math.min(netBid, netAsk);
    const hi = Math.max(netBid, netAsk);

    return {
      symbol: c.symbol,
      last: Math.round(netLast * 100) / 100,
      lastIsCalculated: true,
      bid: Math.round(lo * 100) / 100,
      ask: Math.round(hi * 100) / 100,
      bidSize: null,
      askSize: null,
      volume: null,
      high: null,
      low: null,
      open: null,
      close: null,
      week52High: null,
      week52Low: null,
      avgVolume: null,
      delta: null,
      gamma: null,
      theta: null,
      vega: null,
      impliedVol: null,
      undPrice: null,
      timestamp: oldestQuoteTimestamp(legQuotes) ?? "",
    };
  }

  return null;
}

export default function ModifyOrderModal({ order, loading, prices, portfolio, openOrders = null, onConfirm, onClose }: ModifyOrderModalProps) {
  const [newPrice, setNewPrice] = useState("");
  const [newQuantity, setNewQuantity] = useState("");
  const [outsideRth, setOutsideRth] = useState(false);
  const [editableLegs, setEditableLegs] = useState<EditableComboLeg[]>([]);

  // Reset price only when a different order is selected (by permId), not on every re-render
  const orderPermId = order?.permId ?? null;
  useEffect(() => {
    if (order?.limitPrice != null) {
      setNewPrice(order.limitPrice.toFixed(2));
    }
    if (order?.totalQuantity != null) {
      setNewQuantity(String(order.totalQuantity));
    }
    setOutsideRth(false);
    setEditableLegs(buildEditableComboLegs(order));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderPermId]);

  const marketPriceData = useMemo(
    () => (order ? resolveOrderPriceData(order, prices, portfolio, editableLegs) : null),
    [order, prices, portfolio, editableLegs],
  );

  const priceData = useMemo(
    () => applyRestingLimitToQuote({
      priceData: marketPriceData,
      action: order?.action,
      limitPrice: order?.limitPrice,
    }),
    [marketPriceData, order?.action, order?.limitPrice],
  );

  const riskFreeRate = useRiskFreeRate();

  // ----- riskInput memo (must precede the `if (!order) return null` early
  //       return so React's hook ordering invariant holds across renders).
  //
  // Build the chokepoint input for the POST-MODIFY order shape. Routing
  // through `<OrderRiskGate>` here closes the audit-flagged gap where a
  // user could restructure a defined-risk combo (bull-put-spread) into
  // a naked short put inside this modal with zero risk feedback before
  // resubmit. Single-leg modifies use `order.contract` directly; combos
  // use the editable legs.
  const riskInput: OrderRiskInput | null = useMemo(() => {
    if (!order) return null;
    const parsedNewLocal = parseFloat(newPrice);
    const parsedQtyLocal = parsePositiveInteger(newQuantity);
    if (parsedQtyLocal == null) return null;
    const symbol = order.contract.symbol;
    const action: "BUY" | "SELL" = order.action === "SELL" ? "SELL" : "BUY";
    const isComboLocal = order.contract.secType === "BAG" && editableLegs.length >= 2;
    const validPriceLocal = isComboLocal
      ? Number.isFinite(parsedNewLocal) && parsedNewLocal !== 0
      : parsedNewLocal > 0;
    if (!validPriceLocal) return null;

    if (isComboLocal) {
      const chainLegs: ChainOrderLeg[] = [];
      const structureLegs: ComboStructureLeg[] = [];
      for (const leg of editableLegs) {
        const strikeNum = Number.parseFloat(leg.strike);
        const ratio = parsePositiveInteger(leg.ratio);
        if (!Number.isFinite(strikeNum) || strikeNum <= 0) return null;
        if (ratio == null) return null;
        if (!leg.expiry) return null;
        structureLegs.push({
          action: leg.action,
          right: leg.right,
          strike: strikeNum,
          expiry: leg.expiry,
          ratio,
        });
        chainLegs.push({
          action: effectiveComboLegAction(leg.action, action),
          right: leg.right,
          strike: strikeNum,
          expiry: leg.expiry,
          quantity: ratio * parsedQtyLocal,
        });
      }
      const totalCost = parsedNewLocal * parsedQtyLocal * 100;
      const closingCombo = findHeldComboForClose({
        ticker: comboUnderlyingSymbol(order),
        envelopeAction: action,
        quantity: parsedQtyLocal,
        structureLegs,
        portfolio,
        // R-112: other working SELL combos on this BAG consume held units
        // too. Without this, three full-size SELLs each classified as a pure
        // zero-margin close — and `whatIfKey` returns null whenever
        // `closeOut != null`, so the broker what-if never ran to catch it.
        workingSellUnits: workingSellComboUnits(
          comboUnderlyingSymbol(order),
          openOrders,
          { permId: order.permId, orderId: order.orderId },
        ),
      });
      if (closingCombo) {
        const units = heldComboUnits(closingCombo);
        return {
          ticker: comboUnderlyingSymbol(order),
          chainLegs,
          // R-112: SELL receives the premium, exactly as the opening branch
          // below signs it, and the branch dropped `quote` so net-of-cost
          // risk disappeared on a matched close.
          netPremium: action === "SELL" ? -parsedNewLocal : parsedNewLocal,
          description: `${action} ${parsedQtyLocal}x ${symbol} combo @ ${fmtPrice(parsedNewLocal)}`,
          // `totalCost` stays POSITIVE here: the close-out branch of
          // useOrderRisk reads it as PROCEEDS (`proceeds >= 0` picks the
          // "Close Credit:" label and `pnl = proceeds - entryCost`). That is
          // the opposite convention from the opening branch below, and
          // flipping it turned a credit close into a debit.
          totalCost,
          quote: priceData ? { bid: priceData.bid, ask: priceData.ask } : null,
          closeOut: {
            entryCostDollars: resolveEntryCost(closingCombo) * (parsedQtyLocal / units),
          },
        };
      }
      return {
        ticker: symbol,
        chainLegs,
        netPremium: action === "SELL" ? -parsedNewLocal : parsedNewLocal,
        description: `${action} ${parsedQtyLocal}x ${symbol} combo @ ${fmtPrice(parsedNewLocal)}`,
        totalCost: action === "SELL" ? -totalCost : totalCost,
        // FU7: net combo quote for net-of-cost risk on the post-modify shape.
        quote: priceData ? { bid: priceData.bid, ask: priceData.ask } : null,
      };
    }

    if (order.contract.secType === "STK") {
      const stockLegs = portfolio?.positions
        .filter((position) => position.ticker.toUpperCase() === symbol.toUpperCase())
        .flatMap((position) => position.legs)
        .filter((leg) => leg.type === "Stock") ?? [];
      const heldLong = stockLegs
        .filter((leg) => leg.direction === "LONG")
        .reduce((sum, leg) => sum + Math.abs(leg.contracts), 0);
      const heldShort = stockLegs
        .filter((leg) => leg.direction === "SHORT")
        .reduce((sum, leg) => sum + Math.abs(leg.contracts), 0);
      const basisPerShare = stockLegs[0]?.avg_cost ?? 0;
      const closingLong = action === "SELL" && heldLong >= parsedQtyLocal;
      const closingShort = action === "BUY" && heldShort >= parsedQtyLocal;
      return {
        type: "linear",
        ticker: symbol,
        instrument: "stock",
        action,
        quantity: parsedQtyLocal,
        limitPrice: parsedNewLocal,
        multiplier: 1,
        heldQuantity: heldLong,
        heldShortQuantity: heldShort,
        description: `${action} ${parsedQtyLocal} ${symbol} @ ${fmtPrice(parsedNewLocal)}`,
        closeOut: closingLong
          ? { entryCostDollars: parsedQtyLocal * Math.abs(basisPerShare) }
          : closingShort
            ? { entryCostDollars: -parsedQtyLocal * Math.abs(basisPerShare) }
            : undefined,
      };
    }
    if (order.contract.secType !== "OPT") return null;
    const strikeNum = order.contract.strike;
    const right: "C" | "P" = order.contract.right === "P" || order.contract.right === "PUT" ? "P" : "C";
    const expiry = order.contract.expiry ?? "";
    if (!strikeNum || !expiry) return null;
    const totalCost = parsedNewLocal * parsedQtyLocal * 100;
    const closingLeg = resolveClosingOptionLeg(order, parsedQtyLocal, portfolio);
    if (closingLeg) {
      const basisMagnitude = parsedQtyLocal * Math.abs(closingLeg.avg_cost);
      const closingLong = closingLeg.direction === "LONG";
      return {
        ticker: symbol,
        chainLegs: [{ action, right, strike: strikeNum, expiry, quantity: parsedQtyLocal }],
        netPremium: action === "SELL" ? -parsedNewLocal : parsedNewLocal,
        description: `${action} ${parsedQtyLocal}x ${symbol} ${right} @ ${fmtPrice(parsedNewLocal)}`,
        totalCost: closingLong ? totalCost : -totalCost,
        totalLabel: closingLong ? "Proceeds:" : "Close Debit:",
        closeOut: { entryCostDollars: closingLong ? basisMagnitude : -basisMagnitude },
      };
    }
    return {
      ticker: symbol,
      chainLegs: [
        { action, right, strike: strikeNum, expiry, quantity: parsedQtyLocal },
      ],
      netPremium: action === "SELL" ? -parsedNewLocal : parsedNewLocal,
      description: `${action} ${parsedQtyLocal}x ${symbol} ${right} @ ${fmtPrice(parsedNewLocal)}`,
      totalCost: action === "SELL" ? -totalCost : totalCost,
      // FU7: single-leg quote for net-of-cost risk on the post-modify shape.
      quote: priceData ? { bid: priceData.bid, ask: priceData.ask } : null,
    };
  }, [order, editableLegs, newPrice, newQuantity, priceData, portfolio]);

  const riskState = useOrderRisk(riskInput, portfolio);

  if (!order) return null;

  const currentPrice = order.limitPrice ?? 0;
  const currentQuantity = order.totalQuantity;
  const parsedNew = parseFloat(newPrice);
  const parsedQuantity = parsePositiveInteger(newQuantity);
  const isComboOrder = order.contract.secType === "BAG" && editableLegs.length >= 2;
  const isValidPrice = isComboOrder
    ? Number.isFinite(parsedNew) && parsedNew !== 0
    : Number.isFinite(parsedNew) && parsedNew > 0;
  const isValidQuantity = parsedQuantity != null;
  const normalizedLegs = normalizeComboLegs(editableLegs);
  const originalLegsSnapshot = JSON.stringify(buildEditableComboLegs(order));
  const currentLegsSnapshot = JSON.stringify(editableLegs);
  const priceChanged = isValidPrice && Math.abs(parsedNew - currentPrice) >= 0.005;
  const quantityChanged = isValidQuantity && parsedQuantity !== currentQuantity;
  const legsChanged = isComboOrder && currentLegsSnapshot !== originalLegsSnapshot;
  const canSubmit = !loading && riskState?.okToSubmit === true && (
    isComboOrder
      ? Boolean(isValidPrice && isValidQuantity && normalizedLegs && (priceChanged || quantityChanged || legsChanged))
      : Boolean((priceChanged || quantityChanged || outsideRth) && isValidPrice && isValidQuantity)
  );

  const delta = isValidPrice ? parsedNew - currentPrice : 0;
  const hasPriceData = priceData?.bid != null && priceData?.ask != null;

  const { bid, mid, ask } = getQuoteMetrics(priceData);

  // Black-Scholes implied per-share value at current spot.
  // - Single OPT: from contract.
  // - BAG combo: signed sum across editableLegs at current spot/IV per leg.
  // - STK: not applicable.
  const impliedReference: number | null = (() => {
    if (!order || !prices) return null;
    if (isComboOrder) {
      let net = 0;
      for (const leg of editableLegs) {
        const strikeNum = Number.parseFloat(leg.strike);
        if (!Number.isFinite(strikeNum) || strikeNum <= 0) return null;
        const result = computeLegImpliedValue(
          {
            ticker: order.contract.symbol,
            expiry: leg.expiry,
            strike: strikeNum,
            type: leg.right === "C" ? "Call" : "Put",
            direction: leg.action === "BUY" ? "LONG" : "SHORT",
            contracts: 1,
          },
          prices,
          { riskFreeRate },
        );
        if (result.perContract == null) return null;
        const ratio = Number(leg.ratio);
        if (!Number.isInteger(ratio) || ratio <= 0) return null;
        net += (leg.action === "BUY" ? 1 : -1) * ratio * result.perContract;
      }
      return Math.round(net * 100) / 100;
    }
    const c = order.contract;
    if (c.secType !== "OPT" || c.strike == null || !c.right || !c.expiry) return null;
    const type: "Call" | "Put" | null =
      c.right === "C" || c.right === "CALL" ? "Call" : c.right === "P" || c.right === "PUT" ? "Put" : null;
    if (!type) return null;
    return computeLegImpliedValue(
      {
        ticker: c.symbol,
        expiry: c.expiry,
        strike: c.strike,
        type,
        direction: order.action === "BUY" ? "LONG" : "SHORT",
        contracts: 1,
      },
      prices,
      { riskFreeRate },
    ).perContract;
  })();
  const handleLegChange = (index: number, patch: Partial<EditableComboLeg>) => {
    setEditableLegs((prev) => prev.map((leg, legIndex) => (legIndex === index ? { ...leg, ...patch } : leg)));
    setNewPrice("");
  };

  const submitModify = () => {
    if (!canSubmit || riskState?.okToSubmit !== true) return;

    if (isComboOrder && normalizedLegs) {
      onConfirm({
        replaceOrder: {
          type: "combo",
          symbol: comboUnderlyingSymbol(order),
          action: order.action === "BUY" ? "BUY" : "SELL",
          quantity: parsedQuantity!,
          limitPrice: parsedNew,
          tif: order.tif === "GTC" ? "GTC" : "DAY",
          legs: normalizedLegs,
        },
      });
      return;
    }

    const request: ModifyOrderRequest = {};
    if (priceChanged) request.newPrice = parsedNew;
    if (quantityChanged) request.newQuantity = parsedQuantity!;
    if (outsideRth) request.outsideRth = true;
    onConfirm(request);
  };

  return (
    <Modal
      open={!!order}
      onClose={onClose}
      title="Modify Order"
      className={isComboOrder ? "modify-order-modal modify-order-modal-combo" : "modify-order-modal"}
    >
      <div className={`modify-dialog${isComboOrder ? " modify-dialog-combo" : ""}`}>
        <div className="modify-order-info">
          <strong>{order.symbol}</strong>
          <span className={`pill ${order.action === "BUY" ? "accum" : "distrib"}`}>
            {order.action}
          </span>
          <span>{order.orderType}</span>
          <span>{order.tif}</span>
          <span>{order.totalQuantity}x</span>
        </div>

        <div className={`modify-layout${isComboOrder ? " modify-layout-combo" : ""}`}>
          <div className="modify-primary-panel">
            {/* The MARKET, not the resting-limit-doctored quote. `priceData`
                clamps `ask` down to the operator's own limit on a SELL, so
                giving it nine-field market weight printed a self-referential
                MID and a SPREAD measured against the order itself. A BAG's
                signed net is also captioned as a net rather than as the bare
                underlying symbol. R-255. */}
            <OrderQuoteTelemetry
              priceData={marketPriceData}
              label={isComboOrder ? `${order.symbol} net` : order.symbol}
              density="tight"
            />

            {order.orderType === "STP LMT" && order.auxPrice != null && (
              <div className="modify-stop-row">
                <span className="modify-market-label">STOP PRICE</span>
                <span className="modify-market-value">{fmtPrice(order.auxPrice)}</span>
              </div>
            )}

            <div className="modify-price-section">
              <div className={`modify-field-grid${isComboOrder ? " modify-field-grid-combo" : ""}`}>
                <label className="modify-field" htmlFor="modify-quantity-input">
                  <span className="modify-price-label">New Quantity</span>
                  <div className="modify-price-input-row">
                    <input
                      id="modify-quantity-input"
                      className="modify-price-input"
                      type="number"
                      step="1"
                      min="1"
                      value={newQuantity}
                      onChange={(e) => setNewQuantity(e.target.value)}
                    />
                  </div>
                </label>

                <label className="modify-field" htmlFor="modify-price-input">
                  <span className="modify-price-label">{isComboOrder ? "New Net Price" : "New Limit Price"}</span>
                  <div className="modify-price-input-row">
                    <span className="modify-price-prefix">$</span>
                    <input
                      id="modify-price-input"
                      className="modify-price-input"
                      type="number"
                      step="0.01"
                      min={isComboOrder ? undefined : "0.01"}
                      value={newPrice}
                      onChange={(e) => setNewPrice(e.target.value)}
                      autoFocus
                    />
                  </div>
                </label>
              </div>

              <div className="modify-quick-section">
                <span className="modify-price-label">Reference Price</span>
                <div className="modify-quick-buttons">
                  <button
                    className="btn-quick"
                    disabled={!hasPriceData || bid == null}
                    onClick={() => bid != null && setNewPrice(bid.toFixed(2))}
                  >
                    BID{bid != null ? ` ${bid.toFixed(2)}` : ""}
                  </button>
                  <button
                    className="btn-quick"
                    disabled={!hasPriceData || mid == null}
                    onClick={() => mid != null && setNewPrice(mid.toFixed(2))}
                  >
                    MID{mid != null ? ` ${mid.toFixed(2)}` : ""}
                  </button>
                  <button
                    className="btn-quick"
                    disabled={!hasPriceData || ask == null}
                    onClick={() => ask != null && setNewPrice(ask.toFixed(2))}
                  >
                    ASK{ask != null ? ` ${ask.toFixed(2)}` : ""}
                  </button>
                  <button
                    className="btn-quick"
                    disabled={impliedReference == null}
                    title="Black-Scholes implied value at current spot"
                    onClick={() =>
                      impliedReference != null && setNewPrice(impliedReference.toFixed(2))
                    }
                  >
                    IMPLIED{impliedReference != null ? ` ${impliedReference.toFixed(2)}` : ""}
                  </button>
                </div>
              </div>

              {!isComboOrder && (
                <label className="modify-rth-toggle">
                  <input
                    type="checkbox"
                    checked={outsideRth}
                    onChange={(e) => setOutsideRth(e.target.checked)}
                  />
                  <span className="modify-rth-label">FILL OUTSIDE RTH</span>
                  <span className="modify-rth-hint">Pre-market &amp; after hours</span>
                </label>
              )}

              {isValidPrice && delta !== 0 && (
                <div className={`modify-delta ${delta > 0 ? "positive" : "negative"}`}>
                  {delta > 0 ? "+" : ""}{fmtPrice(Math.abs(delta))} from current {fmtPrice(currentPrice)}
                </div>
              )}
            </div>
          </div>

          {isComboOrder && (
            <div className="modify-secondary-panel">
              {/* Leg pills summary (read-only view) */}
              {(() => {
                const unifiedLegs: UnifiedOrderLeg[] = editableLegs.map((leg, i) => ({
                  id: `leg-${i}`,
                  action: leg.action,
                  direction: leg.action === "BUY" ? "LONG" : "SHORT" as const,
                  strike: Number.parseFloat(leg.strike) || 0,
                  type: leg.right === "C" ? "Call" : "Put" as const,
                  expiry: leg.expiry,
                  quantity: Number.isInteger(Number(leg.ratio)) ? Number(leg.ratio) : 0,
                }));
                return (
                  <div style={{ marginBottom: "12px" }}>
                    <OrderLegPills legs={unifiedLegs} />
                  </div>
                );
              })()}

              <div className="modify-section-heading">
                <span className="modify-price-label">Edit Legs</span>
                <span className="modify-section-hint">Modify each leg before replacing the order</span>
              </div>

              <div className="modify-combo-legs">
                {editableLegs.map((leg, index) => (
                  <section className="modify-combo-leg-card" key={`${order.permId}-leg-${index}`}>
                    <div className="modify-combo-leg-title">Leg {index + 1}</div>
                    <div className="modify-combo-leg-grid">
                      <label className="modify-field" htmlFor={`modify-leg-${index}-action`}>
                        <span className="modify-price-label">Action</span>
                        <div className="modify-price-input-row">
                          <select
                            id={`modify-leg-${index}-action`}
                            className="modify-price-input"
                            value={leg.action}
                            onChange={(e) => handleLegChange(index, { action: normalizeLegAction(e.target.value) })}
                          >
                            <option value="BUY">BUY</option>
                            <option value="SELL">SELL</option>
                          </select>
                        </div>
                      </label>

                      <label className="modify-field" htmlFor={`modify-leg-${index}-right`}>
                        <span className="modify-price-label">Type</span>
                        <div className="modify-price-input-row">
                          <select
                            id={`modify-leg-${index}-right`}
                            className="modify-price-input"
                            value={leg.right}
                            onChange={(e) => handleLegChange(index, { right: normalizeLegRight(e.target.value) })}
                          >
                            <option value="C">CALL</option>
                            <option value="P">PUT</option>
                          </select>
                        </div>
                      </label>

                      <label className="modify-field" htmlFor={`modify-leg-${index}-strike`}>
                        <span className="modify-price-label">Strike</span>
                        <div className="modify-price-input-row">
                          <input
                            id={`modify-leg-${index}-strike`}
                            className="modify-price-input"
                            type="number"
                            step="0.01"
                            min="0.01"
                            value={leg.strike}
                            onChange={(e) => handleLegChange(index, { strike: e.target.value })}
                          />
                        </div>
                      </label>

                      <label className="modify-field" htmlFor={`modify-leg-${index}-expiry`}>
                        <span className="modify-price-label">Expiry</span>
                        <div className="modify-price-input-row">
                          <input
                            id={`modify-leg-${index}-expiry`}
                            className="modify-price-input"
                            type="date"
                            value={leg.expiry}
                            onChange={(e) => handleLegChange(index, { expiry: e.target.value })}
                          />
                        </div>
                      </label>

                      <label className="modify-field" htmlFor={`modify-leg-${index}-ratio`}>
                        <span className="modify-price-label">Ratio</span>
                        <div className="modify-price-input-row">
                          <input
                            id={`modify-leg-${index}-ratio`}
                            className="modify-price-input"
                            type="number"
                            step="1"
                            min="1"
                            value={leg.ratio}
                            onChange={(e) => handleLegChange(index, { ratio: e.target.value })}
                          />
                        </div>
                      </label>
                    </div>
                  </section>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Risk math for the POST-MODIFY shape. Owned by `<OrderRiskGate>`
            — surfaces UNBOUNDED if a leg edit turns a defined-risk combo
            into a naked short. Closes the audit gap (commit ac6c886). */}
        <OrderRiskGate
          input={riskInput}
          portfolio={portfolio}
          surface="modify-order-modal"
          variant="info"
        />

        <div className="modify-actions">
          <button className="btn-secondary" onClick={onClose} disabled={loading}>
            Cancel
          </button>
          <button className="btn-primary" onClick={submitModify} disabled={!canSubmit}>
            {loading ? "Modifying..." : "Modify Order"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

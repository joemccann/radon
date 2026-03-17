"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PriceData, OptionContract } from "@/lib/pricesProtocol";
import { optionKey } from "@/lib/pricesProtocol";
import { fmtPrice } from "@/lib/positionUtils";
import { useTickerDetail } from "@/lib/TickerDetailContext";
import { useChainPrefetch } from "@/lib/useChainPrefetch";
import {
  type OrderLeg,
  formatExpiry,
  daysToExpiry,
  detectStructure,
  computeNetPrice,
  computeNetOptionQuote,
  normalizeComboOrder,
  findAtmStrike,
  getVisibleStrikes,
} from "@/lib/optionsChainUtils";

/* ─── Types ─── */

type OptionsChainTabProps = {
  ticker: string;
  prices: Record<string, PriceData>;
  tickerPriceData: PriceData | null;
};

type ChainStrike = {
  strike: number;
  callKey: string;
  putKey: string;
};

/* ─── Chain Strike Row ─── */

function StrikeRow({
  strike,
  callKey,
  putKey,
  prices,
  isAtm,
  onClickCall,
  onClickPut,
  atmRef,
  sideFilter,
}: {
  strike: number;
  callKey: string;
  putKey: string;
  prices: Record<string, PriceData>;
  isAtm: boolean;
  onClickCall: (strike: number, action: "BUY" | "SELL") => void;
  onClickPut: (strike: number, action: "BUY" | "SELL") => void;
  atmRef?: React.Ref<HTMLTableRowElement>;
  sideFilter: "both" | "calls" | "puts";
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

  const rowClass = `chain-row ${isAtm ? "chain-row-atm" : ""}`;
  const showCalls = sideFilter !== "puts";
  const showPuts = sideFilter !== "calls";

  return (
    <tr className={rowClass} ref={atmRef}>
      {/* Call side */}
      {showCalls && (
        <>
          <td className="chain-cell chain-greek">{callDelta != null ? callDelta.toFixed(2) : ""}</td>
          <td className="chain-cell chain-iv">{callIV != null ? (callIV * 100).toFixed(1) : ""}</td>
          <td className="chain-cell chain-vol">{callVol != null ? callVol.toLocaleString() : ""}</td>
          <td
            className="chain-cell chain-bid chain-clickable"
            onClick={() => onClickCall(strike, "SELL")}
            title="Sell call"
          >
            {callBid != null ? fmtPrice(callBid) : "---"}
          </td>
          <td
            className="chain-cell chain-mid chain-clickable"
            onClick={() => onClickCall(strike, "BUY")}
            title="Buy call"
          >
            {callMid != null ? fmtPrice(callMid) : "---"}
          </td>
          <td
            className="chain-cell chain-ask chain-clickable"
            onClick={() => onClickCall(strike, "BUY")}
            title="Buy call"
          >
            {callAsk != null ? fmtPrice(callAsk) : "---"}
          </td>
          <td className="chain-cell chain-last">{callLast != null ? fmtPrice(callLast) : ""}</td>
        </>
      )}

      {/* Strike */}
      <td className={`chain-cell chain-strike ${isAtm ? "chain-strike-atm" : ""}`}>
        {fmtPrice(strike)}
      </td>

      {/* Put side */}
      {showPuts && (
        <>
          <td className="chain-cell chain-last">{putLast != null ? fmtPrice(putLast) : ""}</td>
          <td
            className="chain-cell chain-bid chain-clickable"
            onClick={() => onClickPut(strike, "SELL")}
            title="Sell put"
          >
            {putBid != null ? fmtPrice(putBid) : "---"}
          </td>
          <td
            className="chain-cell chain-mid chain-clickable"
            onClick={() => onClickPut(strike, "BUY")}
            title="Buy put"
          >
            {putMid != null ? fmtPrice(putMid) : "---"}
          </td>
          <td
            className="chain-cell chain-ask chain-clickable"
            onClick={() => onClickPut(strike, "BUY")}
            title="Buy put"
          >
            {putAsk != null ? fmtPrice(putAsk) : "---"}
          </td>
          <td className="chain-cell chain-vol">{putVol != null ? putVol.toLocaleString() : ""}</td>
          <td className="chain-cell chain-iv">{putIV != null ? (putIV * 100).toFixed(1) : ""}</td>
          <td className="chain-cell chain-greek">{putDelta != null ? putDelta.toFixed(2) : ""}</td>
        </>
      )}
    </tr>
  );
}

/* ─── Order Builder Panel ─── */

function OrderBuilder({
  ticker,
  legs,
  prices,
  onRemoveLeg,
  onUpdateLeg,
  onClearLegs,
}: {
  ticker: string;
  legs: OrderLeg[];
  prices: Record<string, PriceData>;
  onRemoveLeg: (id: string) => void;
  onUpdateLeg: (id: string, updates: Partial<OrderLeg>) => void;
  onClearLegs: () => void;
}) {
  const [tif, setTif] = useState<"DAY" | "GTC">("DAY");
  const [limitPrice, setLimitPrice] = useState("");
  const [priceManuallySet, setPriceManuallySet] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [confirmStep, setConfirmStep] = useState(false);

  const isCombo = legs.length > 1;
  const normalizedOrder = useMemo(() => (isCombo ? normalizeComboOrder(legs) : null), [isCombo, legs]);
  const pricingLegs = normalizedOrder?.legs ?? legs;
  const structure = detectStructure(legs);
  const netPrice = computeNetPrice(pricingLegs, prices);
  const isDebit = netPrice != null && netPrice > 0;
  const totalQty = normalizedOrder?.quantity ?? (legs.length > 0 ? legs[0].quantity : 1);

  // Compute net BID / ASK / MID from leg WS prices
  const netPrices = useMemo(() => {
    return computeNetOptionQuote(pricingLegs, prices, ticker);
  }, [pricingLegs, prices, ticker]);

  // Auto-populate limit price to mid when prices first become available
  useEffect(() => {
    if (!priceManuallySet && netPrices.mid != null) {
      setLimitPrice(netPrices.mid.toFixed(2));
    }
  }, [netPrices.mid, priceManuallySet]);

  const parsedPrice = parseFloat(limitPrice);
  const isValidPrice = !isNaN(parsedPrice) && parsedPrice > 0;

  const handlePlace = useCallback(async () => {
    if (!confirmStep) {
      setConfirmStep(true);
      return;
    }

    setLoading(true);
    setError(null);
    setSuccess(null);

    try {
      const isCombo = legs.length > 1;
      const comboOrder = normalizedOrder ?? normalizeComboOrder(legs);
      const body = isCombo
        ? {
            type: "combo",
            symbol: ticker,
            action: isDebit ? "BUY" : "SELL",
            quantity: totalQty,
            limitPrice: parsedPrice,
            tif,
            legs: comboOrder.legs.map((l) => ({
              symbol: ticker,
              secType: "OPT",
              expiry: l.expiry,
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
            expiry: legs[0].expiry,
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
        setSuccess(`Order placed: ${structure || "Option"} on ${ticker}`);
        setConfirmStep(false);
      }
    } catch {
      setError("Network error placing order");
    } finally {
      setLoading(false);
    }
  }, [confirmStep, ticker, legs, parsedPrice, isDebit, normalizedOrder, totalQty, tif, structure]);

  if (legs.length === 0) return null;

  return (
    <div className="order-builder">
      <div className="order-builder-header">
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "11px",
            textTransform: "uppercase",
            letterSpacing: "0.05em",
            color: "var(--text-secondary)",
          }}
        >
          ORDER BUILDER {structure ? `— ${structure}` : ""}
        </span>
        <button
          className="btn-secondary"
          onClick={() => {
            onClearLegs();
            setConfirmStep(false);
            setLimitPrice("");
            setPriceManuallySet(false);
            setError(null);
            setSuccess(null);
          }}
          style={{ fontSize: "10px", padding: "2px 8px" }}
        >
          Clear
        </button>
      </div>

      {/* Legs list */}
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
            <div key={leg.id} className="order-builder-leg">
              <div style={{ display: "flex", alignItems: "center", gap: "8px", flex: 1 }}>
                <button
                  className={`order-action-btn order-action-active ${leg.action === "BUY" ? "order-action-buy" : "order-action-sell"}`}
                  onClick={() => {
                    onUpdateLeg(leg.id, { action: leg.action === "BUY" ? "SELL" : "BUY" });
                    setConfirmStep(false);
                  }}
                  style={{ fontSize: "9px", padding: "2px 6px", minWidth: "36px" }}
                >
                  {leg.action}
                </button>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px" }}>
                  {leg.quantity}x ${leg.strike} {leg.right === "C" ? "Call" : "Put"}
                </span>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--text-secondary)" }}>
                  {formatExpiry(leg.expiry)}
                </span>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--text-secondary)", marginLeft: "auto" }}>
                  {mid != null ? fmtPrice(mid) : "---"}
                </span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                <input
                  className="order-input"
                  type="number"
                  min="1"
                  step="1"
                  value={leg.quantity}
                  onChange={(e) => {
                    const v = parseInt(e.target.value, 10);
                    if (v > 0) {
                      onUpdateLeg(leg.id, { quantity: v });
                      setConfirmStep(false);
                    }
                  }}
                  style={{ width: "48px", fontSize: "11px", padding: "2px 4px", textAlign: "center" }}
                />
                {isCombo && (
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "2px",
                      flex: "0 0 auto",
                    }}
                  >
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: "9px", color: "var(--text-secondary)" }}>
                      $
                    </span>
                    <input
                      className="order-input"
                      type="number"
                      min="0.01"
                      step="0.01"
                      value={legPrice == null ? "" : legPrice}
                      onChange={(e) => {
                        const v = parseFloat(e.target.value);
                        onUpdateLeg(
                          leg.id,
                          {
                            limitPrice: Number.isFinite(v) ? v : null,
                            priceManuallySet: true,
                          },
                        );
                        setPriceManuallySet(true);
                        setConfirmStep(false);
                      }}
                      style={{ width: "54px", fontSize: "11px", padding: "2px 4px", textAlign: "center" }}
                    />
                  </div>
                )}
                <button
                  onClick={() => {
                    onRemoveLeg(leg.id);
                    setConfirmStep(false);
                  }}
                  style={{
                    background: "none",
                    border: "none",
                    color: "var(--fault)",
                    cursor: "pointer",
                    fontSize: "14px",
                    padding: "0 4px",
                    fontFamily: "var(--font-mono)",
                  }}
                  title="Remove leg"
                >
                  ×
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Limit Price */}
      <div className="order-builder-net">
        <div className="order-field" style={{ margin: 0 }}>
          <label className="order-label">
            Limit Price — NET {isDebit ? "DEBIT" : "CREDIT"}
          </label>
          <div className="modify-price-input-row">
            <span className="modify-price-prefix">$</span>
            <input
              className="modify-price-input"
              type="number"
              step="0.01"
              min="0.01"
              value={limitPrice}
              onChange={(e) => {
                setLimitPrice(e.target.value);
                setPriceManuallySet(true);
                setConfirmStep(false);
              }}
              placeholder="0.00"
            />
          </div>
          <div className="modify-quick-buttons">
            <button
              className="btn-quick"
              disabled={netPrices.bid == null}
              onClick={() => {
                if (netPrices.bid != null) {
                  setLimitPrice(netPrices.bid.toFixed(2));
                  setPriceManuallySet(true);
                  setConfirmStep(false);
                }
              }}
            >
              BID{netPrices.bid != null ? ` ${netPrices.bid.toFixed(2)}` : ""}
            </button>
            <button
              className="btn-quick"
              disabled={netPrices.mid == null}
              onClick={() => {
                if (netPrices.mid != null) {
                  setLimitPrice(netPrices.mid.toFixed(2));
                  setPriceManuallySet(true);
                  setConfirmStep(false);
                }
              }}
            >
              MID{netPrices.mid != null ? ` ${netPrices.mid.toFixed(2)}` : ""}
            </button>
            <button
              className="btn-quick"
              disabled={netPrices.ask == null}
              onClick={() => {
                if (netPrices.ask != null) {
                  setLimitPrice(netPrices.ask.toFixed(2));
                  setPriceManuallySet(true);
                  setConfirmStep(false);
                }
              }}
            >
              ASK{netPrices.ask != null ? ` ${netPrices.ask.toFixed(2)}` : ""}
            </button>
          </div>
          {isValidPrice && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "10px", color: "var(--text-secondary)", marginTop: "4px" }}>
              {fmtPrice(parsedPrice * totalQty * 100)} notional
            </span>
          )}
        </div>
      </div>

      {/* TIF */}
      <div className="order-field" style={{ marginTop: "8px" }}>
        <label className="order-label">Time in Force</label>
        <div className="order-action-buttons">
          <button
            className={`order-action-btn ${tif === "DAY" ? "order-action-active" : ""}`}
            onClick={() => setTif("DAY")}
          >
            DAY
          </button>
          <button
            className={`order-action-btn ${tif === "GTC" ? "order-action-active" : ""}`}
            onClick={() => setTif("GTC")}
          >
            GTC
          </button>
        </div>
      </div>

      {error && <div className="order-error">{error}</div>}
      {success && <div className="order-success">{success}</div>}

      {/* Submit */}
      <div className="order-submit" style={{ marginTop: "8px" }}>
        {confirmStep ? (
          <div className="order-confirm-row">
            <button
              className="btn-secondary"
              onClick={() => setConfirmStep(false)}
              disabled={loading}
            >
              Back
            </button>
            <button
              className={`btn-primary ${!isDebit ? "btn-danger" : ""}`}
              onClick={handlePlace}
              disabled={!isValidPrice || loading}
            >
              {loading ? "Placing..." : `Confirm: ${structure || "Option"} @ ${fmtPrice(parsedPrice)}`}
            </button>
          </div>
        ) : (
          <button
            className="btn-primary"
            onClick={handlePlace}
            disabled={!isValidPrice}
            style={{ width: "100%" }}
          >
            Place {structure || "Order"}
          </button>
        )}
      </div>
    </div>
  );
}

/* ─── Main OptionsChainTab ─── */

export default function OptionsChainTab({
  ticker,
  prices,
  tickerPriceData,
}: OptionsChainTabProps) {
  const [expirations, setExpirations] = useState<string[]>([]);
  const [selectedExpiry, setSelectedExpiry] = useState<string | null>(null);
  const [strikes, setStrikes] = useState<number[]>([]);
  const [loadingExpiries, setLoadingExpiries] = useState(false);
  const [loadingStrikes, setLoadingStrikes] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [orderLegs, setOrderLegs] = useState<OrderLeg[]>([]);
  const [strikesPerSide, setStrikesPerSide] = useState(15);
  const [sideFilter, setSideFilter] = useState<"both" | "calls" | "puts">("both");
  const atmRef = useRef<HTMLTableRowElement>(null);

  // Background prefetch of all expirations for instant switching
  const { cacheStrikes, getCachedStrikes } = useChainPrefetch(
    ticker,
    expirations,
    selectedExpiry,
  );

  // Fetch expirations on mount
  useEffect(() => {
    let cancelled = false;
    setLoadingExpiries(true);
    setError(null);

    fetch(`/api/options/expirations?symbol=${encodeURIComponent(ticker)}`)
      .then((res) => res.json())
      .then((data) => {
        if (cancelled) return;
        if (data.error) {
          setError(data.error);
          setLoadingExpiries(false);
          return;
        }
        const exps: string[] = data.expirations ?? [];
        setExpirations(exps);
        // Default to first expiry that is at least 7 days out
        const defaultExp = exps.find((e) => daysToExpiry(e) >= 7) ?? exps[0] ?? null;
        setSelectedExpiry(defaultExp);
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

    fetch(`/api/options/chain?symbol=${encodeURIComponent(ticker)}&expiry=${selectedExpiry}`)
      .then((res) => res.json())
      .then((data) => {
        if (cancelled) return;
        if (data.error) {
          setError(data.error);
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

  // Filter strikes around ATM
  const visibleStrikes = useMemo<ChainStrike[]>(() => {
    if (!selectedExpiry || strikes.length === 0) return [];
    const visible = getVisibleStrikes(strikes, atmStrike, strikesPerSide);
    return visible.map((strike) => ({
      strike,
      callKey: optionKey({ symbol: ticker, expiry: selectedExpiry, strike, right: "C" }),
      putKey: optionKey({ symbol: ticker, expiry: selectedExpiry, strike, right: "P" }),
    }));
  }, [ticker, selectedExpiry, strikes, atmStrike, strikesPerSide]);

  // Subscribe visible chain contracts for WS price streaming
  const { setChainContracts } = useTickerDetail();
  useEffect(() => {
    if (!selectedExpiry || visibleStrikes.length === 0) {
      setChainContracts([]);
      return;
    }
    const contracts: OptionContract[] = [];
    for (const row of visibleStrikes) {
      contracts.push({ symbol: ticker, expiry: selectedExpiry, strike: row.strike, right: "C" });
      contracts.push({ symbol: ticker, expiry: selectedExpiry, strike: row.strike, right: "P" });
    }
    setChainContracts(contracts);
    return () => setChainContracts([]);
  }, [ticker, selectedExpiry, visibleStrikes, setChainContracts]);

  // Scroll to ATM on load
  useEffect(() => {
    if (atmRef.current) {
      atmRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
    }
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
  }, []);

  // Collect option keys the chain needs subscribed
  // (The parent usePrices hook subscribes based on contracts — we'd need
  //  to lift these up. For now the chain shows WS data if already subscribed.)

  if (loadingExpiries) {
    return (
      <div style={{ padding: "24px 0", textAlign: "center" }}>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", color: "var(--text-secondary)" }}>
          Loading expirations...
        </span>
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
          onChange={(e) => {
            setSelectedExpiry(e.target.value || null);
            setOrderLegs([]);
          }}
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
          </select>
        </div>
      </div>

      {/* Chain grid */}
      {loadingStrikes ? (
        <div style={{ padding: "24px 0", textAlign: "center" }}>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", color: "var(--text-secondary)" }}>
            Loading chain...
          </span>
        </div>
      ) : (
        <div className="chain-grid-wrapper">
          <table className="chain-grid">
            <thead>
              <tr>
                {sideFilter !== "puts" && (
                  <>
                    <th className="chain-header">Δ</th>
                    <th className="chain-header">IV</th>
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
                    <th className="chain-header">IV</th>
                    <th className="chain-header">Δ</th>
                  </>
                )}
              </tr>
              <tr>
                {sideFilter !== "puts" && <th className="chain-side-label" colSpan={7}>CALLS</th>}
                <th className="chain-side-label" />
                {sideFilter !== "calls" && <th className="chain-side-label" colSpan={7}>PUTS</th>}
              </tr>
            </thead>
            <tbody>
              {visibleStrikes.map((row) => {
                const isAtm = row.strike === atmStrike;
                return (
                  <StrikeRow
                    key={row.strike}
                    strike={row.strike}
                    callKey={row.callKey}
                    putKey={row.putKey}
                    prices={prices}
                    isAtm={isAtm}
                    onClickCall={handleCallClick}
                    onClickPut={handlePutClick}
                    atmRef={isAtm ? atmRef : undefined}
                    sideFilter={sideFilter}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Order Builder */}
      <OrderBuilder
        ticker={ticker}
        legs={orderLegs}
        prices={prices}
        onRemoveLeg={handleRemoveLeg}
        onUpdateLeg={handleUpdateLeg}
        onClearLegs={handleClearLegs}
      />
    </div>
  );
}

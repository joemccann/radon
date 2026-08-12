"use client";

import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import type { PriceData, FundamentalsData, DepthBook, Trade, OptionContract } from "@/lib/pricesProtocol";
import type { OrdersData, PortfolioData } from "@/lib/types";

/**
 * Click-to-fill payload: a price (and, when unambiguous, a side) sent from the
 * order book / tape to the order ticket. `nonce` is stamped by the setter and
 * monotonically increases so two clicks on the SAME price+side still re-fire
 * the consuming effect (which keys on nonce, never on object identity).
 * `action` is OMITTED whenever the side is ambiguous — a price-only fill is
 * always safe; the ticket keeps its current action and the user still confirms.
 */
export type OrderPrefill = {
  price: number;
  action?: "BUY" | "SELL";
  quantity?: number;
  source: "montage" | "ladder" | "tape";
  nonce: number;
};

type TickerDetailContextValue = {
  activeTicker: string | null;
  activePositionId: number | null;
  setActiveTicker: (ticker: string | null) => void;
  setActivePositionId: (id: number | null) => void;
  getPrices: () => Record<string, PriceData>;
  getFundamentals: () => Record<string, FundamentalsData>;
  /** Ref snapshot (non-reactive). Prefer `portfolio` for UI that must re-render. */
  getPortfolio: () => PortfolioData | null;
  /** Ref snapshot (non-reactive). Prefer `orders` for UI that must re-render. */
  getOrders: () => OrdersData | null;
  getDepths: () => Record<string, DepthBook>;
  getTape: () => Record<string, Trade[]>;
  /**
   * Reactive portfolio snapshot. High-frequency feeds (prices/depths/tape) stay
   * ref-only; portfolio/orders land rarely and MUST re-render cockpit consumers
   * after client-side navigation (NEW_FINDINGS wave-2: getPortfolio alone never
   * scheduled a render when setPortfolio wrote the ref).
   */
  portfolio: PortfolioData | null;
  orders: OrdersData | null;
  setPrices: (p: Record<string, PriceData>) => void;
  setFundamentals: (f: Record<string, FundamentalsData>) => void;
  setPortfolio: (p: PortfolioData | null) => void;
  setOrders: (o: OrdersData | null) => void;
  setDepths: (d: Record<string, DepthBook>) => void;
  setTape: (t: Record<string, Trade[]>) => void;
  chainContracts: OptionContract[];
  setChainContracts: (c: OptionContract[]) => void;
  /** Book key the detail view wants L2 depth for. Drives `usePrices` upstream. */
  depthSymbol: string | null;
  setDepthSymbol: (key: string | null) => void;
  /** Bounded focused depth set. Two-leg spreads request both option books. */
  depthSymbols: string[];
  setDepthSymbols: (keys: string[]) => void;
  /** For a futures-backed depth subject (e.g. VIX), the order-ticket selected
   *  contract's expiry. The depth KEY stays the index symbol; this expiry tells
   *  the relay WHICH listed future to resolve under that key. Null = front-month. */
  depthFutureExpiry: string | null;
  setDepthFutureExpiry: (expiry: string | null) => void;
  /** User-pinned book subject (e.g. a combo leg's option key) overriding the
   *  default focus, so the Book pane shows that leg's depth. Null = default. */
  focusedBookKey: string | null;
  setFocusedBookKey: (key: string | null) => void;
  /** Click-to-fill: latest price/side published from the book/tape, or null. */
  orderPrefill: OrderPrefill | null;
  /** Publish a click-to-fill; the nonce is stamped here (monotonic). */
  setOrderPrefill: (p: Omit<OrderPrefill, "nonce">) => void;
};

const TickerDetailContext = createContext<TickerDetailContextValue | null>(null);

export function TickerDetailProvider({ children }: { children: ReactNode }) {
  const [activeTicker, setActiveTickerState] = useState<string | null>(null);
  const [activePositionId, setActivePositionIdState] = useState<number | null>(null);
  const [chainContracts, setChainContractsState] = useState<OptionContract[]>([]);
  const [depthSymbol, setDepthSymbolState] = useState<string | null>(null);
  const [depthSymbols, setDepthSymbolsState] = useState<string[]>([]);
  const [depthFutureExpiry, setDepthFutureExpiryState] = useState<string | null>(null);
  const [focusedBookKey, setFocusedBookKeyState] = useState<string | null>(null);
  const [orderPrefill, setOrderPrefillState] = useState<OrderPrefill | null>(null);
  const [portfolio, setPortfolioState] = useState<PortfolioData | null>(null);
  const [orders, setOrdersState] = useState<OrdersData | null>(null);
  const prefillNonceRef = useRef(0);
  const pricesRef = useRef<Record<string, PriceData>>({});
  const fundamentalsRef = useRef<Record<string, FundamentalsData>>({});
  const portfolioRef = useRef<PortfolioData | null>(null);
  const ordersRef = useRef<OrdersData | null>(null);
  const depthsRef = useRef<Record<string, DepthBook>>({});
  const tapeRef = useRef<Record<string, Trade[]>>({});

  const setActiveTicker = useCallback((ticker: string | null) => {
    setActiveTickerState((prev) => {
      const next = ticker ? ticker.toUpperCase() : null;
      // A pinned leg book belongs to one subject — drop it whenever the focused
      // ticker changes so it never leaks across instruments. The selected depth
      // future expiry is likewise per-instrument and must not survive a switch.
      if (next !== prev) {
        setFocusedBookKeyState(null);
        setDepthFutureExpiryState(null);
        setDepthSymbolsState([]);
      }
      return next;
    });
    if (!ticker) {
      setActivePositionIdState(null);
      setChainContractsState([]);
      setDepthSymbolState(null);
      setDepthSymbolsState([]);
      setDepthFutureExpiryState(null);
    }
  }, []);

  const setFocusedBookKey = useCallback((key: string | null) => {
    setFocusedBookKeyState((prev) => (prev === key ? prev : key));
  }, []);

  const setActivePositionId = useCallback((id: number | null) => {
    setActivePositionIdState(id);
  }, []);

  const setChainContracts = useCallback((c: OptionContract[]) => {
    setChainContractsState(c);
  }, []);

  const setDepthSymbol = useCallback((key: string | null) => {
    setDepthSymbolState((prev) => (prev === key ? prev : key));
  }, []);

  const setDepthSymbols = useCallback((keys: string[]) => {
    const next = [...new Set(keys)].sort();
    setDepthSymbolsState((prev) =>
      prev.length === next.length && prev.every((key, index) => key === next[index]) ? prev : next,
    );
  }, []);

  const setDepthFutureExpiry = useCallback((expiry: string | null) => {
    setDepthFutureExpiryState((prev) => (prev === expiry ? prev : expiry));
  }, []);

  const setOrderPrefill = useCallback((p: Omit<OrderPrefill, "nonce">) => {
    prefillNonceRef.current += 1;
    setOrderPrefillState({ ...p, nonce: prefillNonceRef.current });
  }, []);

  const getPrices = useCallback(() => pricesRef.current, []);
  const getFundamentals = useCallback(() => fundamentalsRef.current, []);
  const getPortfolio = useCallback(() => portfolioRef.current, []);
  const getOrders = useCallback(() => ordersRef.current, []);
  const getDepths = useCallback(() => depthsRef.current, []);
  const getTape = useCallback(() => tapeRef.current, []);

  const setPrices = useCallback((p: Record<string, PriceData>) => {
    pricesRef.current = p;
  }, []);

  const setFundamentals = useCallback((f: Record<string, FundamentalsData>) => {
    fundamentalsRef.current = f;
  }, []);

  const setPortfolio = useCallback((p: PortfolioData | null) => {
    portfolioRef.current = p;
    setPortfolioState(p);
  }, []);

  const setOrders = useCallback((o: OrdersData | null) => {
    ordersRef.current = o;
    setOrdersState(o);
  }, []);

  const setDepths = useCallback((d: Record<string, DepthBook>) => {
    depthsRef.current = d;
  }, []);

  const setTape = useCallback((t: Record<string, Trade[]>) => {
    tapeRef.current = t;
  }, []);

  return (
    <TickerDetailContext.Provider
      value={{
        activeTicker,
        activePositionId,
        setActiveTicker,
        setActivePositionId,
        getPrices,
        getFundamentals,
        getPortfolio,
        getOrders,
        getDepths,
        getTape,
        portfolio,
        orders,
        setPrices,
        setFundamentals,
        setPortfolio,
        setOrders,
        setDepths,
        setTape,
        chainContracts,
        setChainContracts,
        depthSymbol,
        setDepthSymbol,
        depthSymbols,
        setDepthSymbols,
        depthFutureExpiry,
        setDepthFutureExpiry,
        focusedBookKey,
        setFocusedBookKey,
        orderPrefill,
        setOrderPrefill,
      }}
    >
      {children}
    </TickerDetailContext.Provider>
  );
}

export function useTickerDetail(): TickerDetailContextValue {
  const ctx = useContext(TickerDetailContext);
  if (!ctx) throw new Error("useTickerDetail must be used within TickerDetailProvider");
  return ctx;
}

/**
 * Non-throwing accessor: returns null when there is no provider. Use this for
 * progressive-enhancement consumers (click-to-fill) that must not crash when
 * the component is rendered outside the ticker-detail tree (modals, tests).
 */
export function useTickerDetailOptional(): TickerDetailContextValue | null {
  return useContext(TickerDetailContext);
}

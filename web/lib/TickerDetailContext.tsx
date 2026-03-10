"use client";

import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import type { PriceData, FundamentalsData } from "@/lib/pricesProtocol";
import type { OrdersData, PortfolioData } from "@/lib/types";

type TickerDetailContextValue = {
  activeTicker: string | null;
  activePositionId: number | null;
  openTicker: (ticker: string, positionId?: number) => void;
  closeTicker: () => void;
  getPrices: () => Record<string, PriceData>;
  getFundamentals: () => Record<string, FundamentalsData>;
  getPortfolio: () => PortfolioData | null;
  getOrders: () => OrdersData | null;
  setPrices: (p: Record<string, PriceData>) => void;
  setFundamentals: (f: Record<string, FundamentalsData>) => void;
  setPortfolio: (p: PortfolioData | null) => void;
  setOrders: (o: OrdersData | null) => void;
};

const TickerDetailContext = createContext<TickerDetailContextValue | null>(null);

export function TickerDetailProvider({ children }: { children: ReactNode }) {
  const [activeTicker, setActiveTicker] = useState<string | null>(null);
  const [activePositionId, setActivePositionId] = useState<number | null>(null);
  const pricesRef = useRef<Record<string, PriceData>>({});
  const fundamentalsRef = useRef<Record<string, FundamentalsData>>({});
  const portfolioRef = useRef<PortfolioData | null>(null);
  const ordersRef = useRef<OrdersData | null>(null);

  const openTicker = useCallback((ticker: string, positionId?: number) => {
    setActiveTicker(ticker.toUpperCase());
    setActivePositionId(positionId ?? null);
  }, []);

  const closeTicker = useCallback(() => {
    setActiveTicker(null);
    setActivePositionId(null);
  }, []);

  const getPrices = useCallback(() => pricesRef.current, []);
  const getFundamentals = useCallback(() => fundamentalsRef.current, []);
  const getPortfolio = useCallback(() => portfolioRef.current, []);
  const getOrders = useCallback(() => ordersRef.current, []);

  const setPrices = useCallback((p: Record<string, PriceData>) => {
    pricesRef.current = p;
  }, []);

  const setFundamentals = useCallback((f: Record<string, FundamentalsData>) => {
    fundamentalsRef.current = f;
  }, []);

  const setPortfolio = useCallback((p: PortfolioData | null) => {
    portfolioRef.current = p;
  }, []);

  const setOrders = useCallback((o: OrdersData | null) => {
    ordersRef.current = o;
  }, []);

  return (
    <TickerDetailContext.Provider
      value={{ activeTicker, activePositionId, openTicker, closeTicker, getPrices, getFundamentals, getPortfolio, getOrders, setPrices, setFundamentals, setPortfolio, setOrders }}
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

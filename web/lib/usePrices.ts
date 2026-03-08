"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  type WSMessage,
  type PriceData,
  type OptionContract,
  type IndexContract,
  normalizeSymbolList,
  symbolKey,
  contractsKey,
} from "./pricesProtocol";

export type PriceUpdate = {
  symbol: string;
  data: PriceData;
  receivedAt: Date;
};

export type UsePricesOptions = {
  /** Symbols to subscribe to (stock tickers) */
  symbols: string[];
  /** Option contracts to subscribe to */
  contracts?: OptionContract[];
  /** Index contracts to subscribe to (e.g. VIX, VVIX) */
  indexes?: IndexContract[];
  /** Enable real-time streaming (default: true) */
  enabled?: boolean;
  /** Callback when a price updates */
  onPriceUpdate?: (update: PriceUpdate) => void;
  /** Callback when connection status changes */
  onConnectionChange?: (connected: boolean) => void;
};

export type UsePricesReturn = {
  /** Current prices keyed by symbol */
  prices: Record<string, PriceData>;
  /** Whether the connection is active */
  connected: boolean;
  /** Whether IB is connected on the server */
  ibConnected: boolean;
  /** Any error message */
  error: string | null;
  /** Manually reconnect */
  reconnect: () => void;
  /** Get a snapshot for symbols (doesn't require streaming connection) */
  getSnapshot: (symbols: string[]) => Promise<Record<string, PriceData>>;
};

/**
 * React hook for real-time price streaming from IB via WebSocket.
 */
export function usePrices(options: UsePricesOptions): UsePricesReturn {
  const {
    symbols,
    contracts = [],
    indexes = [],
    enabled = true,
    onPriceUpdate,
    onConnectionChange,
  } = options;

  const [prices, setPrices] = useState<Record<string, PriceData>>({});
  const [connected, setConnected] = useState(false);
  const [ibConnected, setIbConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  const symbolHash = symbolKey(symbols);
  const contractHash = contractsKey(contracts);
  const indexHash = useMemo(
    () => indexes.map((i) => `${i.symbol}@${i.exchange}`).sort().join(","),
    [indexes],
  );
  const normalizedSymbols = useMemo(
    () => normalizeSymbolList(symbols),
    [symbolHash],
  );
  const normalizedContracts = useMemo(
    () => contracts,
    [contractHash],
  );
  const normalizedIndexes = useMemo(
    () => indexes,
    [indexHash],
  );

  const socketUrl =
    process.env.NEXT_PUBLIC_IB_REALTIME_WS_URL ??
    process.env.IB_REALTIME_WS_URL ??
    "ws://localhost:8765";

  const connect = useCallback(() => {
    if (!enabled || (normalizedSymbols.length === 0 && normalizedContracts.length === 0 && normalizedIndexes.length === 0)) return;

    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    if (wsRef.current) {
      wsRef.current.close();
    }

    const ws = new WebSocket(socketUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) return;
      setConnected(true);
      setError(null);
      onConnectionChange?.(true);

      ws.send(
        JSON.stringify({
          action: "subscribe",
          symbols: normalizedSymbols,
          ...(normalizedContracts.length > 0 ? { contracts: normalizedContracts } : {}),
          ...(normalizedIndexes.length > 0 ? { indexes: normalizedIndexes } : {}),
        }),
      );
    };

    ws.onmessage = (event) => {
      if (!mountedRef.current || ws !== wsRef.current) return;
      try {
        const message = JSON.parse(event.data) as WSMessage;

        switch (message.type) {
          case "price":
          case "snapshot": {
            const { data } = message;
            setPrices((prev) => ({
              ...prev,
              [data.symbol]: data,
            }));
            onPriceUpdate?.({
              symbol: data.symbol,
              data,
              receivedAt: new Date(),
            });
            break;
          }
          case "status":
            setIbConnected(message.ib_connected);
            break;
          case "error":
            setError(message.message);
            break;
          case "pong":
          case "subscribed":
          case "unsubscribed":
            break;
          default:
            break;
        }
      } catch (error_) {
        console.error("Failed to parse price message:", error_);
      }
    };

    ws.onclose = () => {
      // If this WS was replaced by a newer connection, ignore the stale close event.
      // Without this guard, the old onclose fires after mountedRef is reset to true,
      // scheduling a 5s reconnect that creates an infinite reconnection cycle.
      if (!mountedRef.current || ws !== wsRef.current) return;
      setConnected(false);
      onConnectionChange?.(false);

      if (!enabled || (normalizedSymbols.length === 0 && normalizedContracts.length === 0 && normalizedIndexes.length === 0)) return;

      reconnectTimeoutRef.current = setTimeout(() => {
        if (mountedRef.current && enabled && (normalizedSymbols.length > 0 || normalizedContracts.length > 0 || normalizedIndexes.length > 0)) {
          connect();
        }
      }, 5000);
    };

    ws.onerror = () => {
      if (!mountedRef.current || ws !== wsRef.current) return;
      setConnected(false);
      setError("Connection lost");
      onConnectionChange?.(false);
      ws.close();
    };
  }, [enabled, normalizedSymbols, normalizedContracts, normalizedIndexes, onConnectionChange, onPriceUpdate, socketUrl, symbolHash, contractHash, indexHash]);

  const reconnect = useCallback(() => {
    connect();
  }, [connect]);

  const getSnapshot = useCallback(async (snapshotSymbols: string[]): Promise<Record<string, PriceData>> => {
    const symbolsToRequest = normalizeSymbolList(snapshotSymbols);
    if (symbolsToRequest.length === 0) {
      return {};
    }

    try {
      const response = await fetch("/api/prices", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbols: symbolsToRequest }),
      });

      const body = (await response.json()) as {
        prices?: Record<string, PriceData>;
        error?: string;
      };

      if (!response.ok) {
        throw new Error(body.error ?? "Failed to get snapshot");
      }

      return body.prices ?? {};
    } catch (error_) {
      setError(error_ instanceof Error ? error_.message : "Failed to get snapshot");
      console.error("Snapshot error:", error_);
      return {};
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;

    if (enabled && (normalizedSymbols.length > 0 || normalizedContracts.length > 0 || normalizedIndexes.length > 0)) {
      connect();
    } else {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      setConnected(false);
      onConnectionChange?.(false);
    }

    return () => {
      mountedRef.current = false;

      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }

      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
    };
  }, [connect, enabled, onConnectionChange, symbolHash, contractHash, indexHash, normalizedSymbols.length, normalizedContracts.length, normalizedIndexes.length]);

  return {
    prices,
    connected,
    ibConnected,
    error,
    reconnect,
    getSnapshot,
  };
}

/**
 * Format price for display
 */
export function formatPrice(price: number | null | undefined): string {
  if (price == null || Number.isNaN(price)) return "—";
  return price.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/**
 * Format volume for display
 */
export function formatVolume(volume: number | null | undefined): string {
  if (volume == null || Number.isNaN(volume)) return "—";
  if (volume >= 1_000_000) {
    return `${(volume / 1_000_000).toFixed(1)}M`;
  }
  if (volume >= 1_000) {
    return `${(volume / 1_000).toFixed(1)}K`;
  }
  return volume.toLocaleString();
}

/**
 * Calculate price change percentage
 */
export function calcChangePercent(current: number | null, previous: number | null): number | null {
  if (current == null || previous == null || previous === 0) return null;
  return ((current - previous) / previous) * 100;
}

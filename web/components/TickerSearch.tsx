"use client";

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import { createReconnectStrategy, type ReconnectState } from "@/lib/reconnectStrategy";
import { useDismissablePopover } from "@/lib/useDismissablePopover";
import { useWatchlist } from "@/lib/useWatchlist";
import { useRealtimeAuth } from "@/lib/RealtimeAuthContext";
import { buildAuthenticatedWebSocketUrl, resolveRealtimeWebSocketUrl } from "@/lib/realtimeSocketAuth";
import StarToggle from "@/components/StarToggle";

type SearchResult = {
  conId: number;
  symbol: string;
  secType: string;
  primaryExchange: string;
  currency: string;
  derivativeSecTypes?: string[];
};

type TickerSearchProps = {
  onSelect: (symbol: string) => void;
  placeholder?: string;
  className?: string;
  /** Accessible name for the combobox (placeholders are not names). */
  ariaLabel?: string;
  /** Fired when the user attempts a search while IB Gateway is unreachable. */
  onSearchUnavailable?: () => void;
};

const MAX_RESULTS = 10;
const DEBOUNCE_MS = 200;
const ALLOWED_SEC_TYPES = new Set(["STK", "IND", "FUT"]);

const TickerSearch = forwardRef<HTMLInputElement, TickerSearchProps>(
  function TickerSearch(
    { onSelect, placeholder = "Search ticker...", className, ariaLabel = "Search ticker", onSearchUnavailable },
    ref,
  ) {
    const inputRef = useRef<HTMLInputElement>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const dropdownRef = useRef<HTMLDivElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const reconnectStrategyRef = useRef<ReconnectState>(
      createReconnectStrategy({ maxMs: 16000, maxAttempts: 0 }),
    );
    const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
      null,
    );
    const mountedRef = useRef(true);
    const connectingRef = useRef(false);
    const pendingPatternRef = useRef<string | null>(null);
    const activePatternRef = useRef("");
    const getRealtimeToken = useRealtimeAuth();
    const getRealtimeTokenRef = useRef(getRealtimeToken);
    getRealtimeTokenRef.current = getRealtimeToken;

    const [query, setQuery] = useState("");
    const [results, setResults] = useState<SearchResult[]>([]);
    const [activeIndex, setActiveIndex] = useState(-1);
    const [isOpen, setIsOpen] = useState(false);
    const [loading, setLoading] = useState(false);
    const [watchBusy, setWatchBusy] = useState<Set<string>>(new Set());

    const { isWatched, toggleWatch } = useWatchlist();

    const handleToggleWatch = useCallback(
      async (symbol: string) => {
        const key = symbol.toUpperCase();
        setWatchBusy((prev) => new Set(prev).add(key));
        try {
          await toggleWatch(symbol);
        } catch {
          // hook already rolled back the optimistic state
        } finally {
          setWatchBusy((prev) => {
            const next = new Set(prev);
            next.delete(key);
            return next;
          });
        }
      },
      [toggleWatch],
    );

    useImperativeHandle(ref, () => inputRef.current!, []);

    /* ------------------------------------------------------------------ */
    /*  WebSocket lifecycle                                                */
    /* ------------------------------------------------------------------ */
    const connectWs = useCallback(() => {
      if (!mountedRef.current) return;
      if (connectingRef.current) return;
      if (
        wsRef.current &&
        (wsRef.current.readyState === WebSocket.OPEN ||
          wsRef.current.readyState === WebSocket.CONNECTING)
      ) {
        return;
      }

      try {
        connectingRef.current = true;
        void buildAuthenticatedWebSocketUrl(resolveRealtimeWebSocketUrl(), getRealtimeTokenRef.current)
          .then((url) => {
            if (!mountedRef.current) return;
            const ws = new WebSocket(url);
            wsRef.current = ws;

            ws.onopen = () => {
              connectingRef.current = false;
              if (!mountedRef.current) {
                ws.close();
                return;
              }
              reconnectStrategyRef.current.reset();

              // If a search was attempted while WS was down, fire it now
              if (pendingPatternRef.current) {
                const pattern = pendingPatternRef.current;
                pendingPatternRef.current = null;
                ws.send(JSON.stringify({ action: "search", pattern }));
              }
            };

            ws.onmessage = (event) => {
              if (!mountedRef.current) return;
              try {
                const data = JSON.parse(event.data);
                if (data.type === "ping") {
                  if (ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ action: "pong" }));
                  }
                  return;
                }
                if (data.type === "searchResults") {
                  const responsePattern = typeof data.pattern === "string" ? data.pattern.trim().toUpperCase() : "";
                  if (!responsePattern || responsePattern !== activePatternRef.current) return;
                  const filtered: SearchResult[] = (data.results ?? [])
                    .filter((r: SearchResult) => ALLOWED_SEC_TYPES.has(r.secType))
                    .slice(0, MAX_RESULTS);
                  setResults(filtered);
                  setActiveIndex(-1);
                  setLoading(false);
                  if (data.disconnected === true) {
                    onSearchUnavailable?.();
                  }
                }
              } catch {
                // ignore non-JSON or irrelevant messages
              }
            };

            ws.onclose = () => {
              connectingRef.current = false;
              if (!mountedRef.current) return;
              wsRef.current = null;
              // Reconnect with exponential backoff
              const strategy = reconnectStrategyRef.current;
              if (strategy.canRetry()) {
                const delay = strategy.nextDelay();
                reconnectTimerRef.current = setTimeout(connectWs, delay);
              }
            };

            ws.onerror = () => {
              connectingRef.current = false;
              // onclose will fire after onerror — reconnect handled there
              ws.close();
            };
          })
          .catch(() => {
            connectingRef.current = false;
            if (!mountedRef.current) return;
            setLoading(false);
            onSearchUnavailable?.();
            const strategy = reconnectStrategyRef.current;
            if (strategy.canRetry()) {
              const delay = strategy.nextDelay();
              reconnectTimerRef.current = setTimeout(connectWs, delay);
            }
          });
      } catch {
        connectingRef.current = false;
        // setTimeout fallback if constructor throws
        const strategy = reconnectStrategyRef.current;
        if (strategy.canRetry()) {
          const delay = strategy.nextDelay();
          reconnectTimerRef.current = setTimeout(connectWs, delay);
        }
      }
    }, [onSearchUnavailable]);

    useEffect(() => {
      mountedRef.current = true;
      connectWs();
      return () => {
        mountedRef.current = false;
        if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
        if (debounceRef.current) clearTimeout(debounceRef.current);
        connectingRef.current = false;
        if (wsRef.current) {
          wsRef.current.onclose = null; // prevent reconnect on unmount
          if (wsRef.current.readyState === WebSocket.OPEN) {
            wsRef.current.close();
          }
          wsRef.current = null;
        }
      };
    }, [connectWs]);

    /* ------------------------------------------------------------------ */
    /*  Search dispatch (debounced)                                        */
    /* ------------------------------------------------------------------ */
    const dispatchSearch = useCallback(
      (pattern: string) => {
        if (debounceRef.current) clearTimeout(debounceRef.current);

        if (!pattern.trim()) {
          activePatternRef.current = "";
          setResults([]);
          setLoading(false);
          setIsOpen(false);
          pendingPatternRef.current = null;
          return;
        }

        setLoading(true);
        setIsOpen(true);
        activePatternRef.current = pattern.trim().toUpperCase();

        debounceRef.current = setTimeout(() => {
          const ws = wsRef.current;
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: "search", pattern: pattern.trim() }));
            pendingPatternRef.current = null;
          } else {
            // WS not ready — relay (or upstream IB) is unreachable. Surface that.
            pendingPatternRef.current = pattern.trim();
            setResults([]);
            setLoading(false);
            onSearchUnavailable?.();
            connectWs();
          }
        }, DEBOUNCE_MS);
      },
      [connectWs, onSearchUnavailable],
    );

    /* ------------------------------------------------------------------ */
    /*  Selection                                                          */
    /* ------------------------------------------------------------------ */
    const handleSelect = useCallback(
      (symbol: string) => {
        setQuery(symbol);
        setIsOpen(false);
        setResults([]);
        setActiveIndex(-1);
        onSelect(symbol);
      },
      [onSelect],
    );

    /* ------------------------------------------------------------------ */
    /*  Keyboard navigation                                                */
    /* ------------------------------------------------------------------ */
    const handleKeyDown = useCallback(
      (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (!isOpen || results.length === 0) {
          if (e.key === "Escape") {
            setIsOpen(false);
          }
          return;
        }

        switch (e.key) {
          case "ArrowDown":
            e.preventDefault();
            setActiveIndex((prev) =>
              prev < results.length - 1 ? prev + 1 : 0,
            );
            break;
          case "ArrowUp":
            e.preventDefault();
            setActiveIndex((prev) =>
              prev > 0 ? prev - 1 : results.length - 1,
            );
            break;
          case "Enter":
            e.preventDefault();
            if (activeIndex >= 0 && activeIndex < results.length) {
              handleSelect(results[activeIndex].symbol);
            }
            break;
          case "Escape":
            e.preventDefault();
            setIsOpen(false);
            setActiveIndex(-1);
            break;
        }
      },
      [isOpen, results, activeIndex, handleSelect],
    );

    /* ------------------------------------------------------------------ */
    /*  Click outside / Escape                                             */
    /* ------------------------------------------------------------------ */
    const dismissDropdown = useCallback(() => {
      setIsOpen(false);
      setActiveIndex(-1);
    }, []);
    useDismissablePopover(containerRef, dismissDropdown);

    /* ------------------------------------------------------------------ */
    /*  Scroll active item into view                                       */
    /* ------------------------------------------------------------------ */
    useEffect(() => {
      if (activeIndex < 0 || !dropdownRef.current) return;
      const items = dropdownRef.current.querySelectorAll("[data-ticker-item]");
      items[activeIndex]?.scrollIntoView({ block: "nearest" });
    }, [activeIndex]);

    /* ------------------------------------------------------------------ */
    /*  Render                                                             */
    /* ------------------------------------------------------------------ */
    const showDropdown = isOpen && query.trim().length > 0;

    return (
      <div ref={containerRef} style={{ position: "relative" }} className={className}>
        {/* Input */}
        <input
          ref={inputRef}
          type="text"
          value={query}
          placeholder={placeholder}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => {
            const val = e.target.value.toUpperCase();
            setQuery(val);
            dispatchSearch(val);
          }}
          onFocus={() => {
            if (query.trim() && results.length > 0) setIsOpen(true);
          }}
          onKeyDown={handleKeyDown}
          style={{
            width: "100%",
            padding: "8px 12px",
            fontFamily: "'IBM Plex Mono', monospace",
            fontSize: "13px",
            color: "var(--text-primary)",
            backgroundColor: "var(--bg-panel)",
            border: "1px solid var(--border-dim)",
            borderRadius: "4px",
            outline: "none",
            transition: "border-color 150ms",
          }}
          onFocusCapture={(e) => {
            (e.target as HTMLInputElement).style.borderColor =
              "var(--border-focus)";
          }}
          onBlurCapture={(e) => {
            (e.target as HTMLInputElement).style.borderColor =
              "var(--border-dim)";
          }}
          aria-label={ariaLabel}
          aria-expanded={showDropdown}
          aria-haspopup="listbox"
          aria-autocomplete="list"
          aria-controls="ticker-search-listbox"
          role="combobox"
          name="ticker-search"
        />

        {/* Dropdown */}
        {showDropdown && (
          <div
            ref={dropdownRef}
            id="ticker-search-listbox"
            role="listbox"
            style={{
              position: "absolute",
              top: "calc(100% + 4px)",
              left: 0,
              right: 0,
              maxHeight: "320px",
              overflowY: "auto",
              backgroundColor: "var(--bg-panel)",
              border: "1px solid var(--border-dim)",
              borderRadius: "4px",
              zIndex: 100,
            }}
          >
            {loading && results.length === 0 && (
              <div
                className="tm"
                style={{
                  padding: "12px 16px",
                  fontFamily: "Inter, sans-serif",
                  fontSize: "12px",
                }}
              >
                Searching...
              </div>
            )}

            {!loading && results.length === 0 && (
              <div
                className="tm"
                style={{
                  padding: "12px 16px",
                  fontFamily: "Inter, sans-serif",
                  fontSize: "12px",
                }}
              >
                No results
              </div>
            )}

            {results.map((r, i) => (
              <div
                key={r.conId}
                data-ticker-item
                role="option"
                aria-selected={i === activeIndex}
                onMouseDown={(e) => {
                  e.preventDefault(); // prevent input blur
                  handleSelect(r.symbol);
                }}
                onMouseEnter={() => setActiveIndex(i)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "10px",
                  padding: "8px 16px",
                  cursor: "pointer",
                  backgroundColor:
                    i === activeIndex ? "var(--bg-hover)" : "transparent",
                  transition: "background-color 100ms",
                  borderBottom: "1px solid var(--line-grid)",
                }}
              >
                {/* Symbol — prominent */}
                <span
                  style={{
                    fontFamily: "'IBM Plex Mono', monospace",
                    fontSize: "14px",
                    fontWeight: 600,
                    color:
                      i === activeIndex
                        ? "var(--signal-core)"
                        : "var(--text-primary)",
                    minWidth: "72px",
                    letterSpacing: "0.3px",
                  }}
                >
                  {r.symbol}
                </span>

                {/* secType capsule badge */}
                <span
                  style={{
                    fontFamily: "'IBM Plex Mono', monospace",
                    fontSize: "9px",
                    fontWeight: 500,
                    textTransform: "uppercase",
                    letterSpacing: "0.5px",
                    color: "var(--text-dim, var(--text-secondary))",
                    backgroundColor: "var(--bg-panel-raised)",
                    border: "1px solid var(--line-grid)",
                    borderRadius: "999px",
                    padding: "1px 7px",
                    whiteSpace: "nowrap",
                  }}
                >
                  {r.secType}
                </span>

                {/* Exchange — right-aligned, dimmer */}
                <span
                  style={{
                    fontFamily: "Inter, sans-serif",
                    fontSize: "11px",
                    color: "var(--text-dim, var(--text-secondary))",
                    marginLeft: "auto",
                    opacity: 0.6,
                  }}
                >
                  {r.primaryExchange}
                </span>

                {/* Watchlist star — stop propagation so starring doesn't
                    select the row / navigate to the ticker. */}
                <span
                  onMouseDown={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                  }}
                  onClick={(e) => e.stopPropagation()}
                  style={{ display: "inline-flex" }}
                >
                  <StarToggle
                    active={isWatched(r.symbol)}
                    busy={watchBusy.has(r.symbol.toUpperCase())}
                    onToggle={() => handleToggleWatch(r.symbol)}
                    size="sm"
                  />
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  },
);

TickerSearch.displayName = "TickerSearch";

export default TickerSearch;

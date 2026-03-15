"use client";

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";

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
};

const WS_URL =
  process.env.NEXT_PUBLIC_IB_REALTIME_WS_URL ?? "ws://localhost:8765";

const MAX_RESULTS = 10;
const DEBOUNCE_MS = 200;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 16000;

const TickerSearch = forwardRef<HTMLInputElement, TickerSearchProps>(
  function TickerSearch(
    { onSelect, placeholder = "Search ticker...", className },
    ref,
  ) {
    const inputRef = useRef<HTMLInputElement>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const dropdownRef = useRef<HTMLDivElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const reconnectDelayRef = useRef(RECONNECT_BASE_MS);
    const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
      null,
    );
    const mountedRef = useRef(true);
    const pendingPatternRef = useRef<string | null>(null);

    const [query, setQuery] = useState("");
    const [results, setResults] = useState<SearchResult[]>([]);
    const [activeIndex, setActiveIndex] = useState(-1);
    const [isOpen, setIsOpen] = useState(false);
    const [loading, setLoading] = useState(false);
    const [wsReady, setWsReady] = useState(false);

    useImperativeHandle(ref, () => inputRef.current!, []);

    /* ------------------------------------------------------------------ */
    /*  WebSocket lifecycle                                                */
    /* ------------------------------------------------------------------ */
    const connectWs = useCallback(() => {
      if (!mountedRef.current) return;
      if (
        wsRef.current &&
        (wsRef.current.readyState === WebSocket.OPEN ||
          wsRef.current.readyState === WebSocket.CONNECTING)
      ) {
        return;
      }

      try {
        const ws = new WebSocket(WS_URL);
        wsRef.current = ws;

        ws.onopen = () => {
          if (!mountedRef.current) {
            ws.close();
            return;
          }
          setWsReady(true);
          reconnectDelayRef.current = RECONNECT_BASE_MS;

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
            if (data.type === "searchResults") {
              const filtered: SearchResult[] = (data.results ?? [])
                .filter((r: SearchResult) => r.secType === "STK")
                .slice(0, MAX_RESULTS);
              setResults(filtered);
              setActiveIndex(-1);
              setLoading(false);
            }
          } catch {
            // ignore non-JSON or irrelevant messages
          }
        };

        ws.onclose = () => {
          if (!mountedRef.current) return;
          setWsReady(false);
          wsRef.current = null;
          // Reconnect with exponential backoff
          const delay = reconnectDelayRef.current;
          reconnectDelayRef.current = Math.min(
            delay * 2,
            RECONNECT_MAX_MS,
          );
          reconnectTimerRef.current = setTimeout(connectWs, delay);
        };

        ws.onerror = () => {
          // onclose will fire after onerror — reconnect handled there
          ws.close();
        };
      } catch {
        // setTimeout fallback if constructor throws
        const delay = reconnectDelayRef.current;
        reconnectDelayRef.current = Math.min(delay * 2, RECONNECT_MAX_MS);
        reconnectTimerRef.current = setTimeout(connectWs, delay);
      }
    }, []);

    useEffect(() => {
      mountedRef.current = true;
      connectWs();
      return () => {
        mountedRef.current = false;
        if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
        if (debounceRef.current) clearTimeout(debounceRef.current);
        if (wsRef.current) {
          wsRef.current.onclose = null; // prevent reconnect on unmount
          wsRef.current.close();
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
          setResults([]);
          setLoading(false);
          setIsOpen(false);
          pendingPatternRef.current = null;
          return;
        }

        setLoading(true);
        setIsOpen(true);

        debounceRef.current = setTimeout(() => {
          const ws = wsRef.current;
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: "search", pattern: pattern.trim() }));
            pendingPatternRef.current = null;
          } else {
            // WS not ready — stash the pattern for when it reconnects
            pendingPatternRef.current = pattern.trim();
            connectWs();
          }
        }, DEBOUNCE_MS);
      },
      [connectWs],
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
    /*  Click outside                                                      */
    /* ------------------------------------------------------------------ */
    useEffect(() => {
      function handleClickOutside(e: MouseEvent) {
        if (
          containerRef.current &&
          !containerRef.current.contains(e.target as Node)
        ) {
          setIsOpen(false);
          setActiveIndex(-1);
        }
      }
      document.addEventListener("mousedown", handleClickOutside);
      return () =>
        document.removeEventListener("mousedown", handleClickOutside);
    }, []);

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
          aria-expanded={showDropdown}
          aria-haspopup="listbox"
          aria-autocomplete="list"
          aria-controls="ticker-search-listbox"
          role="combobox"
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
                style={{
                  padding: "12px 16px",
                  fontFamily: "Inter, sans-serif",
                  fontSize: "12px",
                  color: "var(--text-muted)",
                }}
              >
                Searching...
              </div>
            )}

            {!loading && results.length === 0 && (
              <div
                style={{
                  padding: "12px 16px",
                  fontFamily: "Inter, sans-serif",
                  fontSize: "12px",
                  color: "var(--text-muted)",
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
                  gap: "12px",
                  padding: "8px 16px",
                  cursor: "pointer",
                  backgroundColor:
                    i === activeIndex ? "var(--bg-hover)" : "transparent",
                  transition: "background-color 100ms",
                }}
              >
                {/* Symbol */}
                <span
                  style={{
                    fontFamily: "'IBM Plex Mono', monospace",
                    fontSize: "13px",
                    fontWeight: 600,
                    color:
                      i === activeIndex
                        ? "var(--signal-core)"
                        : "var(--text-primary)",
                    minWidth: "64px",
                  }}
                >
                  {r.symbol}
                </span>

                {/* secType badge */}
                <span
                  style={{
                    fontFamily: "Inter, sans-serif",
                    fontSize: "10px",
                    fontWeight: 500,
                    color: "var(--text-muted)",
                    textTransform: "uppercase",
                    letterSpacing: "0.5px",
                  }}
                >
                  {r.secType}
                </span>

                {/* Exchange */}
                <span
                  style={{
                    fontFamily: "Inter, sans-serif",
                    fontSize: "11px",
                    color: "var(--text-secondary)",
                    marginLeft: "auto",
                  }}
                >
                  {r.primaryExchange}
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

"use client";

import { useEffect, useMemo, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { navItems, NAV_GROUP_LABEL, PI_COMMANDS, sectionDescription } from "@/lib/data";
import { useWatchlist } from "@/lib/useWatchlist";
import { usePortfolio } from "@/lib/usePortfolio";
import { useMarketHours, MarketState } from "@/lib/useMarketHours";

type PaletteItem = {
  id: string;
  label: string;
  sublabel: string;
  href: string;
  group: string;
  keywords: string;
};

const RECENT_KEY = "radon:recent-tickers";
const MAX_RECENT = 6;

function readRecentTickers(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(RECENT_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((v): v is string => typeof v === "string").slice(0, MAX_RECENT);
  } catch {
    return [];
  }
}

export function pushRecentTicker(symbol: string) {
  if (typeof window === "undefined") return;
  const sym = symbol.toUpperCase().trim();
  if (!sym) return;
  try {
    const current = readRecentTickers();
    const next = [sym, ...current.filter((s) => s !== sym)].slice(0, MAX_RECENT);
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    // ignore
  }
}

function buildPaletteItems(portfolioSymbols: string[], watchlistSymbols: string[], recent: string[]): PaletteItem[] {
  const items: PaletteItem[] = [];

  for (const nav of navItems.filter((n) => !n.hidden)) {
    items.push({
      id: `nav:${nav.route}`,
      label: nav.label,
      sublabel: sectionDescription[nav.route] ?? NAV_GROUP_LABEL[nav.group],
      href: nav.href,
      group: NAV_GROUP_LABEL[nav.group],
      keywords: `${nav.label} ${nav.route} ${nav.group}`.toLowerCase(),
    });
  }

  const seenTickers = new Set<string>();
  for (const sym of [...recent, ...portfolioSymbols, ...watchlistSymbols]) {
    const upper = sym.toUpperCase();
    if (seenTickers.has(upper)) continue;
    seenTickers.add(upper);
    if (items.some((it) => it.id === `ticker:${upper}`)) continue;
    items.push({
      id: `ticker:${upper}`,
      label: upper,
      sublabel: recent.includes(upper) ? "Recent · Instrument" : portfolioSymbols.includes(upper) ? "Portfolio · Instrument" : "Watchlist · Instrument",
      href: `/${upper}`,
      group: "Instruments",
      keywords: upper.toLowerCase(),
    });
  }

  for (const cmd of PI_COMMANDS) {
    const href =
      cmd === "scan" ? "/scanner"
      : cmd === "discover" ? "/scanner"
      : cmd === "portfolio" ? "/portfolio"
      : cmd === "journal" ? "/journal"
      : cmd.startsWith("evaluate") ? "/scanner"
      : "/dashboard";
    items.push({
      id: `cmd:${cmd}`,
      label: `/${cmd}`,
      sublabel: "Command",
      href,
      group: "Commands",
      keywords: cmd.toLowerCase(),
    });
  }

  return items;
}

type CommandPaletteProps = {
  open: boolean;
  onClose: () => void;
};

export default function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const router = useRouter();
  const { watchlist } = useWatchlist();
  const marketState = useMarketHours();
  const isMarketActive = marketState !== MarketState.CLOSED;
  const { data: portfolio } = usePortfolio(isMarketActive);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [recent, setRecent] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const portfolioSymbols = useMemo(() => (portfolio?.positions ?? []).map((p) => p.ticker), [portfolio]);
  const watchlistSymbols = useMemo(() => watchlist.map((w) => w.symbol), [watchlist]);

  useEffect(() => {
    if (open) {
      setRecent(readRecentTickers());
      setQuery("");
      setActiveIndex(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const allItems = useMemo(
    () => buildPaletteItems(portfolioSymbols, watchlistSymbols, recent),
    [portfolioSymbols, watchlistSymbols, recent],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return allItems.slice(0, 12);
    return allItems.filter((it) => it.keywords.includes(q) || it.label.toLowerCase().includes(q) || it.sublabel.toLowerCase().includes(q)).slice(0, 12);
  }, [allItems, query]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  const handleSelect = useCallback(
    (item: PaletteItem) => {
      if (item.id.startsWith("ticker:")) {
        pushRecentTicker(item.label);
      }
      onClose();
      router.push(item.href);
    },
    [onClose, router],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex((i) => Math.min(i + 1, filtered.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        const item = filtered[activeIndex];
        if (item) handleSelect(item);
        else if (query.trim()) {
          const sym = query.trim().toUpperCase();
          if (/^[A-Z]{1,6}(\.[A-Z])?$/.test(sym)) {
            pushRecentTicker(sym);
            onClose();
            router.push(`/${sym}`);
          }
        }
      } else if (e.key === "Escape") {
        onClose();
      }
    },
    [activeIndex, filtered, handleSelect, onClose, query, router],
  );

  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.children[activeIndex] as HTMLElement | undefined;
    el?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, open]);

  if (!open) return null;

  return (
    <div className="command-palette-root" role="dialog" aria-modal="true" aria-label="Command palette" data-testid="command-palette">
      <button type="button" className="command-palette-backdrop" aria-label="Close palette" onClick={onClose} />
      <div className="command-palette-panel">
        <div className="command-palette-input-wrap">
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search instruments, pages, or run /scan…"
            className="command-palette-input"
            aria-label="Search"
            data-testid="command-palette-input"
            autoComplete="off"
            spellCheck={false}
          />
          <span className="command-palette-kbd">ESC</span>
        </div>
        <div className="command-palette-list" ref={listRef} role="listbox">
          {filtered.length === 0 ? (
            <div className="command-palette-empty">
              No matches. Press Enter to open <span className="command-palette-empty-ticker">{query.trim().toUpperCase()}</span> as instrument.
            </div>
          ) : (
            filtered.map((item, idx) => (
              <button
                key={item.id}
                type="button"
                role="option"
                aria-selected={idx === activeIndex}
                className={`command-palette-item${idx === activeIndex ? " command-palette-item--active" : ""}`}
                onMouseEnter={() => setActiveIndex(idx)}
                onClick={() => handleSelect(item)}
                data-testid={`command-palette-item-${item.id}`}
              >
                <span className="command-palette-item-label">{item.label}</span>
                <span className="command-palette-item-sub">{item.sublabel}</span>
                <span className="command-palette-item-group">{item.group}</span>
              </button>
            ))
          )}
        </div>
        <div className="command-palette-footer">
          <span><kbd>↑</kbd><kbd>↓</kbd> navigate</span>
          <span><kbd>↵</kbd> open</span>
          <span><kbd>⌘K</kbd> toggle</span>
        </div>
      </div>
    </div>
  );
}

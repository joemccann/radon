"use client";

import { useState, type FormEvent } from "react";
import { Loader2, Search } from "lucide-react";
import { validateTickerList } from "@/lib/scanTickerList";

type ScannerTickerSearchProps = {
  id: string;
  scanning?: boolean;
  requirePairs?: boolean;
  dedupe?: boolean;
  placeholder?: string;
  onTickerScan: (tickers: string[]) => void;
};

/**
 * Comma-separated ticker search form for scanner tabs (theta-search UX
 * precedent, extended to accept a list: "NVDA" or "NVDA, AMD, TSM").
 */
export default function ScannerTickerSearch({
  id,
  scanning = false,
  requirePairs = false,
  dedupe = true,
  placeholder = "NVDA, AMD",
  onTickerScan,
}: ScannerTickerSearchProps) {
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const normalized = query.trim();

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (scanning) return;
    const parsed = validateTickerList(normalized, { requirePairs, dedupe });
    if (!parsed.ok) {
      setError(parsed.error);
      return;
    }
    setError(null);
    onTickerScan(parsed.tickers);
  };

  return (
    <form className="theta-search" onSubmit={submit}>
      <Search size={13} aria-hidden="true" />
      <input
        id={id}
        className="theta-search__input"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value.toUpperCase());
          setError(null);
        }}
        placeholder={placeholder}
        autoCapitalize="characters"
        autoComplete="off"
        spellCheck={false}
        aria-label="Ticker symbols"
        aria-invalid={error ? "true" : "false"}
        aria-describedby={error ? `${id}-error` : undefined}
      />
      <button
        type="submit"
        className="theta-search__button"
        disabled={scanning || normalized.length === 0}
      >
        {scanning ? <Loader2 size={12} className="spin" /> : null}
        Scan
      </button>
      {error && (
        <span id={`${id}-error`} className="theta-search__error" role="alert">
          {error}
        </span>
      )}
    </form>
  );
}

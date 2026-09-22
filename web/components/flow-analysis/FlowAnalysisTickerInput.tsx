"use client";
import ErrorToast from "@/components/ErrorToast";

import { Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useState, type FormEvent } from "react";

const TICKER_RE = /^[A-Za-z]{1,5}$/;

type Props = {
  initialTicker?: string;
};

export default function FlowAnalysisTickerInput({ initialTicker = "" }: Props) {
  const router = useRouter();
  const [value, setValue] = useState(initialTicker);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = useCallback(
    (event: FormEvent) => {
      event.preventDefault();
      const trimmed = value.trim().toUpperCase();
      if (!TICKER_RE.test(trimmed)) {
        setError("Enter a 1-5 letter ticker symbol");
        return;
      }
      setError(null);
      router.push(`/flow-analysis/${trimmed}`);
    },
    [router, value],
  );

  return (
    <section className="section flow-ticker-input-section">
      <form className="flow-ticker-input" onSubmit={handleSubmit} role="search">
        <label className="flow-ticker-input-label" htmlFor="flow-ticker-input">
          Run Flow Report
        </label>
        <div className="flow-ticker-input-row">
          <Search size={14} className="flow-ticker-input-icon" aria-hidden="true" />
          <input
            id="flow-ticker-input"
            type="text"
            placeholder="Ticker"
            value={value}
            autoComplete="off"
            onChange={(event) => {
              setValue(event.target.value.toUpperCase().replace(/[^A-Z]/g, ""));
              if (error) setError(null);
            }}
            maxLength={5}
            data-testid="flow-ticker-input-field"
            aria-label="Ticker symbol"
          />
          <button
            type="submit"
            className="flow-ticker-input-submit"
            disabled={!value.trim()}
            data-testid="flow-ticker-input-submit"
          >
            Analyze
          </button>
        </div>
        {error && (
          <ErrorToast message={error} />
        )}
      </form>
    </section>
  );
}

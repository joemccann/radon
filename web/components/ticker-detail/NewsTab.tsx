"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ExternalLink, Newspaper } from "lucide-react";
import SectionEmptyState from "@/components/SectionEmptyState";

type NewsItem = {
  headline: string;
  source: string;
  created_at: string;
  tickers?: string[];
  is_major?: boolean;
  url?: string;
};

type NewsTabProps = {
  ticker: string;
  active: boolean;
};

export default function NewsTab({ ticker, active }: NewsTabProps) {
  const [news, setNews] = useState<NewsItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [fetched, setFetched] = useState(false);
  const [resolvedTicker, setResolvedTicker] = useState<string | null>(null);
  const requestGenerationRef = useRef(0);

  const fetchNews = useCallback(async (signal: AbortSignal, generation: number) => {
    setLoading(true);
    setError(null);
    setNews([]);
    setSource(null);
    setFetched(false);
    setResolvedTicker(null);
    try {
      const res = await fetch(`/api/ticker/news?ticker=${encodeURIComponent(ticker)}&limit=20`, { signal });
      const json = await res.json();
      if (signal.aborted || generation !== requestGenerationRef.current) return;
      const items = json.data ?? json ?? [];
      setNews(Array.isArray(items) ? items : []);
      setSource(json.source ?? null);
      if (json.error && (!Array.isArray(items) || items.length === 0)) {
        setError(json.error);
      }
    } catch (err) {
      if (signal.aborted || generation !== requestGenerationRef.current) return;
      setError(err instanceof Error ? err.message : "Failed to fetch news");
    } finally {
      if (signal.aborted || generation !== requestGenerationRef.current) return;
      setLoading(false);
      setFetched(true);
      setResolvedTicker(ticker);
    }
  }, [ticker]);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    const generation = ++requestGenerationRef.current;
    void fetchNews(controller.signal, generation);
    return () => controller.abort();
  }, [active, fetchNews]);

  const isCurrentTicker = resolvedTicker === ticker;

  if (loading || (active && !isCurrentTicker)) {
    return (
      <div className="tab-loading">
        <div className="tab-loading-text">Loading news...</div>
      </div>
    );
  }

  if (isCurrentTicker && error) {
    return <div className="tab-error">{error}</div>;
  }

  if (isCurrentTicker && fetched && news.length === 0) {
    return (
      <div className="tab-empty">
        <SectionEmptyState
          icon={Newspaper}
          headline={`No recent news for ${ticker}`}
          variant="compact"
        />
      </div>
    );
  }

  return (
    <div className="news-tab">
      {(isCurrentTicker ? news : []).map((item, i) => (
        <div key={i} className="news-item">
          <div className="news-meta">
            <span className="news-date">
              {item.created_at ? new Date(item.created_at).toLocaleDateString() : ""}
            </span>
            {item.source && <span className="news-source">{item.source}</span>}
            {item.is_major && <span className="pill defined" style={{ fontSize: "8px", padding: "1px 4px" }}>MAJOR</span>}
          </div>
          <div className="news-headline">
            {item.headline}
            <a
              href={item.url || `https://www.google.com/search?q=${encodeURIComponent(item.headline)}&tbm=nws`}
              target="_blank"
              rel="noopener noreferrer"
              className="news-open-link"
              aria-label="Open article"
            >
              <ExternalLink size={12} />
            </a>
          </div>
        </div>
      ))}
      {source && source !== "unusualwhales" && (
        <div className="news-fallback-notice">via {source === "yahoo" ? "Yahoo Finance" : source}</div>
      )}
    </div>
  );
}

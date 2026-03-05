"use client";

import { useCallback, useEffect, useState } from "react";
import { ExternalLink } from "lucide-react";

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

  const fetchNews = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/ticker/news?ticker=${encodeURIComponent(ticker)}&limit=20`);
      const json = await res.json();
      const items = json.data ?? json ?? [];
      setNews(Array.isArray(items) ? items : []);
      setSource(json.source ?? null);
      if (json.error && (!Array.isArray(items) || items.length === 0)) {
        setError(json.error);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch news");
    } finally {
      setLoading(false);
      setFetched(true);
    }
  }, [ticker]);

  useEffect(() => {
    if (active && !fetched) {
      fetchNews();
    }
  }, [active, fetched, fetchNews]);

  if (loading) {
    return (
      <div className="tab-loading">
        <div className="tab-loading-text">Loading news...</div>
      </div>
    );
  }

  if (error) {
    return <div className="tab-error">{error}</div>;
  }

  if (fetched && news.length === 0) {
    return <div className="tab-empty">No recent news for {ticker}</div>;
  }

  return (
    <div className="news-tab">
      {news.map((item, i) => (
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

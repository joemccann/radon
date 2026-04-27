"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Image from "next/image";
import { Link as LinkIcon, RefreshCw, Radio } from "lucide-react";

const POSTS_ENDPOINT = "/data/posts.json";
const REFRESH_INTERVAL_MS = 2 * 60 * 1000;
const MAX_VISIBLE_POSTS = 18;

export type MarketEarPost = {
  id: string;
  title: string;
  content?: string;
  timestamp: string;
  images?: string[];
  rawImages?: string[];
  createdAt?: string;
  updatedAt?: string;
};

type FetchOptions = {
  signal?: AbortSignal;
  mode?: "initial" | "refresh" | "silent";
};

type NormalisedPost = MarketEarPost & {
  isoTimestamp: string;
  timestampMs: number;
  href: string;
};

function formatAbsolute(timestamp: string) {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatRelative(timestamp: string) {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "";
  const diff = Date.now() - date.getTime();
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diff < minute) return "moments ago";
  if (diff < hour) {
    const mins = Math.max(1, Math.round(diff / minute));
    return `${mins} minute${mins === 1 ? "" : "s"} ago`;
  }
  if (diff < day) {
    const hours = Math.max(1, Math.round(diff / hour));
    return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  }
  const days = Math.max(1, Math.round(diff / day));
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

function formatTime(timestamp: string) {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function buildPostHref(id: string) {
  if (!id) return "https://themarketear.com/newsfeed";
  return `https://themarketear.com/posts/${encodeURIComponent(id)}`;
}

export default function DashboardNewsFeed() {
  const [posts, setPosts] = useState<NormalisedPost[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  const loadPosts = useCallback(async ({ signal, mode = "silent" }: FetchOptions = {}) => {
    if (mode === "initial") {
      setLoading(true);
      setError(null);
    } else if (mode === "refresh") {
      setRefreshing(true);
      setError(null);
    }

    try {
      const response = await fetch(POSTS_ENDPOINT, {
        cache: "no-store",
        signal,
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const data: unknown = await response.json();
      if (!Array.isArray(data)) {
        throw new Error("Unexpected payload shape from posts.json");
      }

      const normalised = data
        .map((item) => {
          const post = item as MarketEarPost;
          const stamp = post.timestamp ?? post.updatedAt ?? post.createdAt ?? "";
          const ts = new Date(stamp);
          const ms = ts.getTime();
          return {
            ...post,
            isoTimestamp: Number.isFinite(ms) ? ts.toISOString() : stamp,
            timestampMs: Number.isFinite(ms) ? ms : 0,
            href: buildPostHref(post.id),
            content: (post.content || "").trim(),
            images: Array.isArray(post.images) ? post.images : [],
          } satisfies NormalisedPost;
        })
        .filter((post) => post.id && post.title && post.isoTimestamp)
        .sort((a, b) => b.timestampMs - a.timestampMs);

      setPosts(normalised);
      setLastUpdated(new Date().toISOString());
      setError(null);
    } catch (err) {
      if (signal?.aborted) return;
      const message = err instanceof Error ? err.message : String(err);
      setError(`Unable to load Market Ear feed: ${message}`);
    } finally {
      if (mode === "initial") {
        setLoading(false);
      } else if (mode === "refresh") {
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadPosts({ signal: controller.signal, mode: "initial" });

    const interval = setInterval(() => {
      void loadPosts();
    }, REFRESH_INTERVAL_MS);

    return () => {
      controller.abort();
      clearInterval(interval);
    };
  }, [loadPosts]);

  const handleRefresh = useCallback(async () => {
    await loadPosts({ mode: "refresh" });
  }, [loadPosts]);

  const items = useMemo(() => posts.slice(0, MAX_VISIBLE_POSTS), [posts]);
  const freshnessLabel = lastUpdated ? `Updated ${formatAbsolute(lastUpdated)}` : "Awaiting first capture";

  return (
    <div className="section dashboard-news">
      <div className="section-header">
        <div className="section-title">
          <Radio size={14} />
          Market Ear Live Feed
        </div>
        <div className="news-feed-actions">
          <span className="report-meta">{freshnessLabel}</span>
          <button
            type="button"
            className="news-feed-refresh"
            onClick={handleRefresh}
            disabled={refreshing}
          >
            <RefreshCw size={12} className={refreshing ? "spin" : ""} />
            {refreshing ? "Refreshing" : "Refresh"}
          </button>
        </div>
      </div>
      <div className="section-body">
        {loading ? (
          <div className="news-feed-empty">Collecting Market Ear posts…</div>
        ) : error ? (
          <div className="news-feed-error">{error}</div>
        ) : items.length === 0 ? (
          <div className="news-feed-empty">No Market Ear posts captured yet. Ensure the scraper is running.</div>
        ) : (
          <ul className="news-feed-list">
            {items.map((post) => {
              const firstImage = post.images?.[0] ?? null;
              const relative = formatRelative(post.isoTimestamp);
              const time = formatTime(post.isoTimestamp);
              const absolute = formatAbsolute(post.isoTimestamp);

              return (
                <li key={post.id} className="news-feed-item">
                  <a className="news-feed-link" href={post.href} target="_blank" rel="noopener noreferrer">
                    <h3 className="news-feed-headline">{post.title}</h3>
                  </a>
                  {post.content ? <p className="news-feed-summary">{post.content}</p> : null}
                  {firstImage ? (
                    <div className="news-feed-image-wrapper">
                      <Image
                        src={firstImage}
                        alt={post.title}
                        width={1200}
                        height={675}
                        sizes="(max-width: 1440px) 100vw, 60vw"
                        className="news-feed-image"
                        priority={false}
                      />
                    </div>
                  ) : null}
                  <div className="news-feed-footer">
                    <a
                      className="news-feed-link-pill"
                      href={post.href}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <LinkIcon size={11} />
                      <span>Link</span>
                    </a>
                    <span className="news-feed-timestamp" title={absolute}>
                      {relative}
                      {time ? ` at ${time}` : ""}
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

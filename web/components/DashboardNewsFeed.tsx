"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Image from "next/image";
import { ChevronLeft, ChevronRight, Link as LinkIcon, RefreshCw, Radio } from "lucide-react";

import { formatAbsolute, formatRelative, formatTime } from "../lib/newsfeedTime";
import { useNewsfeedTagFilter } from "../lib/useNewsfeedTagFilter";
import NewsfeedTagBar from "./NewsfeedTagBar";

const POSTS_ENDPOINT = "/data/posts.json";
const REFRESH_INTERVAL_MS = 2 * 60 * 1000;
const PAGE_SIZE = 18;

export type MarketEarPost = {
  id: string;
  title: string;
  content?: string;
  timestamp: string;
  images?: string[];
  rawImages?: string[];
  tags?: string[];
  tags_text?: string[];
  tags_vision?: string[];
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

function buildPostHref(id: string) {
  if (!id) return "https://themarketear.com/newsfeed";
  return `https://themarketear.com/posts/${encodeURIComponent(id)}`;
}

type PaginationBarProps = {
  currentPage: number;
  totalPages: number;
  rangeStart: number;
  rangeEnd: number;
  totalItems: number;
  onPrev: () => void;
  onNext: () => void;
};

function PaginationBar({
  currentPage,
  totalPages,
  rangeStart,
  rangeEnd,
  totalItems,
  onPrev,
  onNext,
}: PaginationBarProps) {
  return (
    <nav className="news-feed-pagination" aria-label="Newsfeed pagination">
      <button
        type="button"
        className="news-feed-page-button"
        onClick={onPrev}
        disabled={currentPage <= 1}
        aria-label="Previous page"
      >
        <ChevronLeft size={12} />
        <span>Prev</span>
      </button>
      <div className="news-feed-page-meta">
        <span className="news-feed-page-indicator">
          Page {currentPage} of {totalPages}
        </span>
        <span className="news-feed-page-range">
          Showing {rangeStart}–{rangeEnd} of {totalItems}
        </span>
      </div>
      <button
        type="button"
        className="news-feed-page-button"
        onClick={onNext}
        disabled={currentPage >= totalPages}
        aria-label="Next page"
      >
        <span>Next</span>
        <ChevronRight size={12} />
      </button>
    </nav>
  );
}

export default function DashboardNewsFeed() {
  const [posts, setPosts] = useState<NormalisedPost[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(1);

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

  const { selectedTags, toggleTag, clearTags } = useNewsfeedTagFilter();

  const filteredPosts = useMemo(() => {
    if (selectedTags.size === 0) return posts;
    const required = Array.from(selectedTags);
    return posts.filter((post) => {
      const postTags = Array.isArray(post.tags) ? post.tags : [];
      return required.every((t) => postTags.includes(t));
    });
  }, [posts, selectedTags]);

  const totalPages = Math.max(1, Math.ceil(filteredPosts.length / PAGE_SIZE));

  // Reset to page 1 whenever the filter changes (selectedTags identity changes).
  // Also clamp if current page exceeds the new totalPages after data refresh.
  useEffect(() => {
    setCurrentPage(1);
  }, [selectedTags]);

  useEffect(() => {
    if (currentPage > totalPages) {
      setCurrentPage(1);
    }
  }, [currentPage, totalPages]);

  const safePage = Math.min(currentPage, totalPages);
  const items = useMemo(
    () => filteredPosts.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE),
    [filteredPosts, safePage],
  );
  const showPagination = filteredPosts.length > PAGE_SIZE;
  const rangeStart = filteredPosts.length === 0 ? 0 : (safePage - 1) * PAGE_SIZE + 1;
  const rangeEnd = Math.min(safePage * PAGE_SIZE, filteredPosts.length);

  const goPrev = useCallback(() => {
    setCurrentPage((page) => Math.max(1, page - 1));
  }, []);
  const goNext = useCallback(() => {
    setCurrentPage((page) => Math.min(totalPages, page + 1));
  }, [totalPages]);

  const freshnessLabel = lastUpdated ? `Updated ${formatAbsolute(lastUpdated)}` : "Awaiting first capture";

  const paginationBar = showPagination ? (
    <PaginationBar
      currentPage={safePage}
      totalPages={totalPages}
      rangeStart={rangeStart}
      rangeEnd={rangeEnd}
      totalItems={posts.length}
      onPrev={goPrev}
      onNext={goNext}
    />
  ) : null;

  return (
    <div className="section dashboard-news">
      <div className="section-header">
        <div className="section-title">
          <Radio size={14} />
          Live Market Analysis
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
        <NewsfeedTagBar
          selectedTags={selectedTags}
          onRemove={toggleTag}
          onClearAll={clearTags}
        />
        {loading ? (
          <div className="news-feed-empty">Collecting Market Ear posts…</div>
        ) : error ? (
          <div className="news-feed-error">{error}</div>
        ) : posts.length === 0 ? (
          <div className="news-feed-empty">No Market Ear posts captured yet. Ensure the scraper is running.</div>
        ) : items.length === 0 ? (
          <div className="news-feed-empty news-feed-empty-filtered">
            <span>No posts match the selected filter.</span>
            <button type="button" className="news-feed-page-button" onClick={clearTags}>
              Clear filter
            </button>
          </div>
        ) : (
          <>
            <ul className="news-feed-list">
            {items.map((post) => {
              const firstImage = post.images?.[0] ?? null;
              const relative = formatRelative(post.isoTimestamp);
              const time = formatTime(post.isoTimestamp);
              const absolute = formatAbsolute(post.isoTimestamp);
              const postTags = Array.isArray(post.tags) ? post.tags : [];

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
                  {postTags.length > 0 ? (
                    <div className="news-feed-tags">
                      {postTags.map((tag) => {
                        const isActive = selectedTags.has(tag);
                        return (
                          <button
                            key={tag}
                            type="button"
                            className={`news-feed-tag-chip${isActive ? " is-active" : ""}`}
                            onClick={() => toggleTag(tag)}
                            aria-pressed={isActive}
                          >
                            {tag}
                          </button>
                        );
                      })}
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
            {paginationBar}
          </>
        )}
      </div>
    </div>
  );
}

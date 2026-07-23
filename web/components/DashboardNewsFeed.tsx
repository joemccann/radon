"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Image from "next/image";
import { ChevronLeft, ChevronRight, Link as LinkIcon, RefreshCw, Radio } from "lucide-react";

import { formatAbsolute, formatRelative, formatTime } from "../lib/newsfeedTime";
import { readOfflineMeta } from "../lib/offline/offlineStatus";
import {
  reportFetchFailure,
  reportFetchSuccess,
  reportOfflineServed,
} from "../lib/offline/offlineSignals";
import { useNewsfeedTagFilter } from "../lib/useNewsfeedTagFilter";
import { useBookmarks } from "../lib/useBookmarks";
import NewsfeedTagBar from "./NewsfeedTagBar";
import NewsfeedLightbox, { type NewsfeedLightboxFocus } from "./NewsfeedLightbox";
import StarToggle from "./StarToggle";

/** Compact snapshot persisted with a bookmark so the profile list can render
 *  the saved post without the live feed being loaded. */
function buildPostSnapshot(post: NormalisedPost) {
  return {
    title: post.title,
    source: post.href,
    timestamp: post.isoTimestamp,
    image: post.images?.[0] ?? null,
  };
}

const POSTS_ENDPOINT = "/api/newsfeed/posts";
const POSTS_FALLBACK_ENDPOINT = "/data/posts.json";
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
  const [lightboxFocus, setLightboxFocus] = useState<NewsfeedLightboxFocus | null>(null);
  const sectionRef = useRef<HTMLDivElement>(null);
  // Mirrors `posts` for the fetch catch: a failed background refresh must
  // hold the last-good list instead of swapping it for an error panel.
  const postsRef = useRef<NormalisedPost[]>([]);

  const scrollToTop = useCallback(() => {
    const node = sectionRef.current;
    if (!node) return;
    const prefersReducedMotion =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    node.scrollIntoView({
      behavior: prefersReducedMotion ? "auto" : "smooth",
      block: "start",
    });
  }, []);

  const loadPosts = useCallback(async ({ signal, mode = "silent" }: FetchOptions = {}) => {
    if (mode === "initial") {
      setLoading(true);
      setError(null);
    } else if (mode === "refresh") {
      setRefreshing(true);
      setError(null);
    }

    let networkResolved = false;
    try {
      let response = await fetch(POSTS_ENDPOINT, {
        cache: "no-store",
        signal,
      });
      networkResolved = true;

      if (!response.ok) {
        // Phase 1 dual-write: if the DB-backed route is unavailable
        // (cold replica, transient sync failure) fall back to the
        // static JSON file the scraper still writes.
        response = await fetch(POSTS_FALLBACK_ENDPOINT, {
          cache: "no-store",
          signal,
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
      }

      const meta = readOfflineMeta(response.headers);
      if (meta.servedOffline) reportOfflineServed(meta.cachedAt);
      else reportFetchSuccess();
      const servedOffline = meta.servedOffline;

      const data: unknown = await response.json();
      if (!Array.isArray(data)) {
        throw new Error("Unexpected payload shape from posts endpoint");
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
      postsRef.current = normalised;
      // Offline-served payloads are replays of an older fetch; stamping
      // "now" would claim freshness the data does not have.
      if (!servedOffline) setLastUpdated(new Date().toISOString());
      setError(null);
    } catch (err) {
      if (signal?.aborted) return;
      if (!networkResolved) reportFetchFailure();
      if (postsRef.current.length > 0) return;
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

  const { isBookmarked, toggleBookmark } = useBookmarks();
  const [bookmarkBusy, setBookmarkBusy] = useState<Set<string>>(new Set());

  const handleToggleBookmark = useCallback(
    async (post: NormalisedPost) => {
      setBookmarkBusy((prev) => new Set(prev).add(post.id));
      try {
        await toggleBookmark({ id: post.id, snapshot: buildPostSnapshot(post) });
      } catch {
        // hook already rolled back the optimistic state
      } finally {
        setBookmarkBusy((prev) => {
          const next = new Set(prev);
          next.delete(post.id);
          return next;
        });
      }
    },
    [toggleBookmark],
  );

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

  // Lightbox cycle list — every filtered post that has at least one
  // image, ordered to match the rail. Image-less posts (e.g. "The Fed
  // volatility trade") aren't navigable because the lightbox is
  // image-centric; landing on a text-only post would render an empty
  // media pane.
  const navigablePosts = useMemo(
    () => filteredPosts.filter((p) => Array.isArray(p.images) && p.images.length > 0 && p.images[0]),
    [filteredPosts],
  );

  const lightboxIndex = useMemo(() => {
    if (!lightboxFocus) return -1;
    return navigablePosts.findIndex((p) => p.id === lightboxFocus.post.id);
  }, [navigablePosts, lightboxFocus]);

  const canNavigatePrev = lightboxIndex > 0;
  const canNavigateNext = lightboxIndex >= 0 && lightboxIndex < navigablePosts.length - 1;

  const navigateLightbox = useCallback(
    (direction: -1 | 1) => {
      if (lightboxIndex < 0) return;
      const next = lightboxIndex + direction;
      if (next < 0 || next >= navigablePosts.length) return;
      const target = navigablePosts[next];
      const firstImage = target.images?.[0];
      if (!firstImage) return;
      setLightboxFocus({ post: target, imageUrl: firstImage });
      // If the target lives on a different paginated page, follow the
      // cursor so closing the lightbox lands the user where the post
      // they were viewing is visible.
      const targetPage = Math.floor(next / PAGE_SIZE) + 1;
      if (targetPage !== safePage) {
        setCurrentPage(targetPage);
      }
    },
    [lightboxIndex, navigablePosts, safePage],
  );
  const rangeStart = filteredPosts.length === 0 ? 0 : (safePage - 1) * PAGE_SIZE + 1;
  const rangeEnd = Math.min(safePage * PAGE_SIZE, filteredPosts.length);

  const goPrev = useCallback(() => {
    if (safePage <= 1) return;
    setCurrentPage(safePage - 1);
    scrollToTop();
  }, [safePage, scrollToTop]);
  const goNext = useCallback(() => {
    if (safePage >= totalPages) return;
    setCurrentPage(safePage + 1);
    scrollToTop();
  }, [safePage, totalPages, scrollToTop]);

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
    <div className="section dashboard-news" ref={sectionRef}>
      <div className="section-header">
        <div className="section-title">
          <Radio size={14} />
          Live Market Analysis
        </div>
        <div className="news-feed-actions">
          <span className="news-feed-updated">{freshnessLabel}</span>
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
                    <button
                      type="button"
                      className="news-feed-image-wrapper news-feed-image-wrapper--button"
                      onClick={() =>
                        setLightboxFocus({ post, imageUrl: firstImage })
                      }
                      aria-label={`Open lightbox for: ${post.title}`}
                    >
                      <Image
                        src={firstImage}
                        alt={post.title}
                        width={1200}
                        height={675}
                        sizes="(max-width: 1440px) 100vw, 60vw"
                        className="news-feed-image"
                        priority={false}
                      />
                      <span className="news-feed-image-zoom" aria-hidden>
                        ⤢
                      </span>
                    </button>
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
                    <StarToggle
                      active={isBookmarked(post.id)}
                      busy={bookmarkBusy.has(post.id)}
                      onToggle={() => handleToggleBookmark(post)}
                    />
                  </div>
                </li>
              );
            })}
            </ul>
            {paginationBar}
          </>
        )}
      </div>
      <NewsfeedLightbox
        focus={lightboxFocus}
        onDismiss={() => setLightboxFocus(null)}
        onNavigate={navigateLightbox}
        canNavigatePrev={canNavigatePrev}
        canNavigateNext={canNavigateNext}
      />
    </div>
  );
}

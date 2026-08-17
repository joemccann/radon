"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Image from "next/image";
import { ChevronLeft, ChevronRight, Link as LinkIcon, RefreshCw } from "lucide-react";

import { formatAbsolute, formatCompact, formatRelative, formatTime } from "../lib/newsfeedTime";
import {
  useNewsfeedPosts,
  type MarketEarPost,
  type NormalisedPost,
} from "../lib/useNewsfeedPosts";
import { useNewsfeedTagFilter } from "../lib/useNewsfeedTagFilter";
import { useBookmarks } from "../lib/useBookmarks";
import NewsfeedTagBar from "./NewsfeedTagBar";
import NewsfeedLightbox, { type NewsfeedLightboxFocus } from "./NewsfeedLightbox";
import StarToggle from "./StarToggle";
import styles from "./DashboardNewsFeed.module.css";

/** Chips beyond this count collapse behind a `+N` expander on mobile. */
const VISIBLE_TAG_LIMIT = 4;

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

const PAGE_SIZE = 18;

export type { MarketEarPost };

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
  const { posts, loading, refreshing, error, lastUpdated, refresh } = useNewsfeedPosts();
  const [currentPage, setCurrentPage] = useState(1);
  const [lightboxFocus, setLightboxFocus] = useState<NewsfeedLightboxFocus | null>(null);
  const sectionRef = useRef<HTMLElement>(null);

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

  const handleRefresh = useCallback(async () => {
    await refresh();
  }, [refresh]);

  const { selectedTags, toggleTag, clearTags } = useNewsfeedTagFilter();

  const { isBookmarked, toggleBookmark } = useBookmarks();
  const [bookmarkBusy, setBookmarkBusy] = useState<Set<string>>(new Set());
  const [expandedTags, setExpandedTags] = useState<Set<string>>(new Set());
  const [postAwaitingTagFocus, setPostAwaitingTagFocus] = useState<string | null>(null);
  const tagStripsRef = useRef(new Map<string, HTMLDivElement | null>());

  const expandTags = useCallback((postId: string) => {
    setExpandedTags((prev) => new Set(prev).add(postId));
    setPostAwaitingTagFocus(postId);
  }, []);

  // The `+N` control unmounts the moment it is activated, so focus has to be
  // handed somewhere deliberate: the first chip it just revealed. Without this
  // the browser resets the active element to the scroll container and a
  // keyboard user loses their place mid-list.
  useEffect(() => {
    if (!postAwaitingTagFocus) return;
    const strip = tagStripsRef.current.get(postAwaitingTagFocus);
    setPostAwaitingTagFocus(null);
    if (!strip) return;
    const chips = strip.querySelectorAll<HTMLButtonElement>("button.news-feed-tag-chip");
    const firstRevealed = chips[VISIBLE_TAG_LIMIT] ?? chips[chips.length - 1];
    firstRevealed?.focus();
  }, [postAwaitingTagFocus]);

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
      const filteredIndex = filteredPosts.findIndex((post) => post.id === target.id);
      if (filteredIndex < 0) return;
      const targetPage = Math.floor(filteredIndex / PAGE_SIZE) + 1;
      if (targetPage !== safePage) {
        setCurrentPage(targetPage);
      }
    },
    [filteredPosts, lightboxIndex, navigablePosts, safePage],
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

  const lastSample = lastUpdated
    ? new Date(lastUpdated).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })
    : "—";
  const captureBasis = error ? "fault" : loading ? "awaiting" : "scraper";

  const paginationBar = showPagination ? (
    <PaginationBar
      currentPage={safePage}
      totalPages={totalPages}
      rangeStart={rangeStart}
      rangeEnd={rangeEnd}
      totalItems={filteredPosts.length}
      onPrev={goPrev}
      onNext={goNext}
    />
  ) : null;

  return (
    <section className={`dashboard-news snapshot-card ${styles.card}`} ref={sectionRef}>
      <header className={`dashboard-news__header ${styles.header}`}>
        <div className={`dashboard-news__heading ${styles.heading}`}>
          <p className="panel-eyebrow">Feed / 01</p>
          <h3 className="panel-title">Live market analysis</h3>
        </div>
        <div className={`news-feed-actions ${styles.actions}`}>
          <button
            type="button"
            className={`news-feed-refresh news-feed-refresh--rail ${styles.refresh}`}
            onClick={handleRefresh}
            disabled={refreshing}
            aria-label={refreshing ? "Refreshing feed" : "Refresh feed"}
          >
            <RefreshCw size={12} className={refreshing ? "spin" : ""} aria-hidden />
            {refreshing ? "Refreshing" : "Refresh"}
          </button>
        </div>
      </header>
      <div className="dashboard-news__body section-body">
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
            <ul className={`news-feed-list ${styles.list}`}>
            {items.map((post) => {
              const firstImage = post.images?.[0] ?? null;
              const relative = formatRelative(post.isoTimestamp);
              const time = formatTime(post.isoTimestamp);
              const compact = formatCompact(post.isoTimestamp);
              const absolute = formatAbsolute(post.isoTimestamp);
              const postTags = Array.isArray(post.tags) ? post.tags : [];
              const overflowCount = postTags.length - VISIBLE_TAG_LIMIT;
              const tagsExpanded = expandedTags.has(post.id);

              return (
                <li key={post.id} data-testid="news-feed-item" className={`news-feed-item ${styles.item}`}>
                  <a className="news-feed-link" href={post.href} target="_blank" rel="noopener noreferrer">
                    <h3 className={`news-feed-headline ${styles.headline}`}>{post.title}</h3>
                  </a>
                  {post.content ? (
                    <p className={`news-feed-summary ${styles.summary}`}>{post.content}</p>
                  ) : null}
                  {firstImage ? (
                    <button
                      type="button"
                      className={`news-feed-image-wrapper news-feed-image-wrapper--button ${styles.imageWrapper}`}
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
                        className={`news-feed-image ${styles.image}`}
                        priority={false}
                      />
                      <span className="news-feed-image-zoom" aria-hidden>
                        ⤢
                      </span>
                    </button>
                  ) : null}
                  {postTags.length > 0 ? (
                    <div
                      data-testid="news-feed-tags"
                      className={`news-feed-tags ${styles.tags}${tagsExpanded ? ` ${styles.tagsExpanded}` : ""}`}
                      ref={(node) => {
                        tagStripsRef.current.set(post.id, node);
                      }}
                    >
                      {postTags.map((tag, index) => {
                        const isActive = selectedTags.has(tag);
                        const overflow = index >= VISIBLE_TAG_LIMIT;
                        return (
                          <button
                            key={tag}
                            type="button"
                            className={`news-feed-tag-chip${isActive ? " is-active" : ""} ${styles.tagChip}${overflow ? ` ${styles.tagOverflow}` : ""}`}
                            onClick={() => toggleTag(tag)}
                            aria-pressed={isActive}
                          >
                            {tag}
                          </button>
                        );
                      })}
                      {overflowCount > 0 && !tagsExpanded ? (
                        <button
                          type="button"
                          className={`news-feed-tag-chip ${styles.tagChip} ${styles.tagMore}`}
                          onClick={() => expandTags(post.id)}
                          // Always collapsed while mounted: expanding unmounts
                          // the control rather than leaving a hidden one
                          // claiming aria-expanded="true".
                          aria-expanded={false}
                          aria-label={`Show ${overflowCount} more tags`}
                        >
                          +{overflowCount}
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                  <div data-testid="news-feed-footer" className={`news-feed-footer ${styles.footer}`}>
                    <a
                      data-testid="news-feed-link-pill"
                      className={`news-feed-link-pill ${styles.linkPill}`}
                      href={post.href}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <LinkIcon size={11} />
                      <span>Link</span>
                    </a>
                    <span
                      data-testid="news-feed-timestamp"
                      className={`news-feed-timestamp ${styles.timestamp}`}
                      title={absolute}
                    >
                      <span className={styles.tsCompact}>{compact}</span>
                      <span className={styles.tsFull}>
                        {relative}
                        {time ? ` at ${time}` : ""}
                      </span>
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
      <footer className="panel-meta-rail" aria-label="Feed calibration">
        <div className="panel-meta-rail-item">
          <span className="k">source</span>
          <span className="v">Market Ear</span>
        </div>
        <div className="panel-meta-rail-item">
          <span className="k">capture.basis</span>
          <span className="v">{captureBasis}</span>
        </div>
        <div className="panel-meta-rail-item">
          <span className="k">last.sample</span>
          <span className="v">{lastSample}</span>
        </div>
      </footer>
      <NewsfeedLightbox
        focus={lightboxFocus}
        onDismiss={() => setLightboxFocus(null)}
        onNavigate={navigateLightbox}
        canNavigatePrev={canNavigatePrev}
        canNavigateNext={canNavigateNext}
      />
    </section>
  );
}

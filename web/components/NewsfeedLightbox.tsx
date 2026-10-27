"use client";

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronLeft, ChevronRight, X, Link as LinkIcon } from "lucide-react";
import type { MarketEarPost } from "@/components/DashboardNewsFeed";
import { formatAbsolute, formatRelative, formatTime } from "@/lib/newsfeedTime";
import { useDialogChrome } from "@/lib/useDialogChrome";
import { useBookmarks } from "@/lib/useBookmarks";
import NewsfeedGallery from "./NewsfeedGallery";
import NewsfeedShare from "./NewsfeedShare";
import NewsfeedExplain from "./NewsfeedExplain";
import feedStyles from "./DashboardNewsFeed.module.css";
import NewsfeedPostContent from "./NewsfeedPostContent";
import { ResearchCharts } from "./PlannedCharts";
import StarToggle from "@/components/StarToggle";
import PublisherLogo from "./PublisherLogo";
import { researchSourceLabel } from "@/lib/newsfeedSource";

export type NewsfeedLightboxFocus = {
  post: MarketEarPost & { href: string; isoTimestamp: string };
  imageUrl: string;
};

type NewsfeedLightboxProps = {
  focus: NewsfeedLightboxFocus | null;
  onDismiss: () => void;
  /** Called with -1 (previous) or +1 (next) when the user presses an
   *  arrow key or clicks a navigation chevron. Parent decides what's
   *  navigable; the lightbox just forwards the intent. */
  onNavigate?: (direction: -1 | 1) => void;
  /** Disable the previous chevron + ArrowLeft handler when there's no
   *  earlier image-bearing post in the rail. */
  canNavigatePrev?: boolean;
  /** Disable the next chevron + ArrowRight handler when there's no
   *  later image-bearing post in the rail. */
  canNavigateNext?: boolean;
};

/** Centered article reader with independent image and article navigation. */
export default function NewsfeedLightbox({
  focus,
  onDismiss,
  onNavigate,
  canNavigatePrev = false,
  canNavigateNext = false,
}: NewsfeedLightboxProps) {
  const [selectedImage, setSelectedImage] = useState<{ focus: NewsfeedLightboxFocus; url: string } | null>(null);
  const [expandedFocus, setExpandedFocus] = useState<NewsfeedLightboxFocus | null>(null);
  const expanded = focus != null && expandedFocus === focus;
  const { isBookmarked, toggleBookmark } = useBookmarks();
  const [bookmarkBusy, setBookmarkBusy] = useState(false);

  // Scroll-lock, focus trap, and focus restore come from the shared chrome.
  // Escape + the ←/→ arrow nav stay local (the hook opts out via
  // closeOnEscape:false) because they preventDefault and forward navigation
  // intent rather than simply closing.
  const { portalTarget, panelRef } = useDialogChrome<HTMLDivElement>({
    open: focus != null,
    closeOnEscape: false,
  });

  const focusedPostId = focus?.post.id;
  useEffect(() => {
    // On mobile the panel owns scrolling, so a new article starts at its top.
    panelRef.current?.querySelector(".newsfeed-lightbox__panel")?.scrollTo?.(0, 0);
  }, [focusedPostId, portalTarget, panelRef]);

  useEffect(() => {
    if (!focus) return;
    function onKey(event: KeyboardEvent) {
      if (event.defaultPrevented) return;
      if (event.key === "Escape") {
        event.preventDefault();
        if (expanded) {
          panelRef.current?.querySelector<HTMLButtonElement>('[aria-label="Back to article"]')?.focus();
          setExpandedFocus(null);
        }
        else onDismiss();
        return;
      }
      const target = event.target;
      if (expanded || event.altKey || event.ctrlKey || event.metaKey || (target instanceof Element && target.closest('input, textarea, select, [contenteditable="true"], [aria-label="Article images"]'))) return;
      if (event.key === "ArrowLeft" && canNavigatePrev && onNavigate) {
        event.preventDefault();
        onNavigate(-1);
        return;
      }
      if (event.key === "ArrowRight" && canNavigateNext && onNavigate) {
        event.preventDefault();
        onNavigate(1);
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
    };
  }, [focus, expanded, onDismiss, onNavigate, canNavigatePrev, canNavigateNext, panelRef]);

  const handleToggleBookmark = useCallback(async () => {
    if (!focus) return;
    const { post } = focus;
    setBookmarkBusy(true);
    try {
      await toggleBookmark({
        id: post.id,
        snapshot: {
          title: post.title,
          source: post.href,
          timestamp: post.isoTimestamp,
          image: post.images?.[0] ?? focus.imageUrl,
          thumbnail: post.images?.[0] ?? focus.imageUrl,
        },
      });
    } finally {
      setBookmarkBusy(false);
    }
  }, [focus, toggleBookmark]);

  if (!focus || !portalTarget) return null;

  const { post } = focus;
  const imageUrl = selectedImage?.focus === focus ? selectedImage.url : focus.imageUrl;
  const tags = Array.isArray(post.tags) ? post.tags : [];
  const relative = formatRelative(post.isoTimestamp);
  const time = formatTime(post.isoTimestamp);
  const absolute = formatAbsolute(post.isoTimestamp);

  // Portal to body so the lightbox escapes the right-rail's stacking context
  // cleanly. Without this the dashboard's grid layers + scroll containers
  // can clip the scrim and let chrome bleed through at the edges.
  return createPortal(
    <div
      className="newsfeed-lightbox"
      ref={panelRef}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-label={post.title}
    >
      <div
        aria-hidden="true"
        className="newsfeed-lightbox__scrim"
        onClick={onDismiss}
        data-testid="newsfeed-lightbox-scrim"
      />
      <div className={`newsfeed-lightbox__frame${expanded ? " newsfeed-lightbox__frame--expanded" : ""}`}>
        <header className="newsfeed-lightbox__toolbar">
          <nav className="newsfeed-lightbox__controls" aria-label="Article navigation">
            {onNavigate && canNavigatePrev ? <button type="button" className="newsfeed-lightbox__control" onClick={() => onNavigate(-1)} aria-label="Previous article" data-testid="newsfeed-lightbox-prev"><ChevronLeft size={18} /><span>Previous article</span></button> : null}
            {onNavigate && canNavigateNext ? <button type="button" className="newsfeed-lightbox__control" onClick={() => onNavigate(1)} aria-label="Next article" data-testid="newsfeed-lightbox-next"><span>Next article</span><ChevronRight size={18} /></button> : null}
          </nav>
          <button type="button" className="newsfeed-lightbox__control newsfeed-lightbox__close" onClick={onDismiss} aria-label="Close article"><X size={20} /></button>
        </header>
        <div className="newsfeed-lightbox__panel">
          <NewsfeedGallery key={`${post.id}:${focus.imageUrl}`} post={post} imageUrl={imageUrl}
            onSelect={url => setSelectedImage({ focus, url })} expanded={expanded}
            onToggleExpanded={() => setExpandedFocus(expanded ? null : focus)} />

          <article key={post.id} className="newsfeed-lightbox__copy" hidden={expanded}>
            <header className="newsfeed-lightbox__head">
              {post.source ? (
                <div style={{ marginBottom: "8px" }} data-testid="lightbox-publisher-badge">
                  <PublisherLogo publisher={post.source.publisher} showLabel showType size={16} />
                </div>
              ) : null}
              <p className="newsfeed-lightbox__kicker" title={absolute}>
                {relative}
                {time ? ` at ${time}` : ""}
              </p>
              <h2 className="newsfeed-lightbox__title">{post.title}</h2>
            </header>

            <NewsfeedPostContent post={post} className="newsfeed-lightbox__body" />
            <ResearchCharts post={post} />

            {tags.length > 0 ? (
              <div className="newsfeed-lightbox__tags">
                {tags.map((tag) => (
                  <span key={tag} className="newsfeed-lightbox__tag">
                    {tag}
                  </span>
                ))}
              </div>
            ) : null}

            <footer className="newsfeed-lightbox__footer">
              <a
                className="newsfeed-lightbox__source"
                href={post.href}
                target="_blank"
                rel="noopener noreferrer"
              >
                {post.source ? (
                  <PublisherLogo publisher={post.source.publisher} size={14} aria-hidden />
                ) : (
                  <LinkIcon size={12} />
                )}
                <span>{post.source ? researchSourceLabel(post.source) : "Open original"}</span>
              </a>
              <StarToggle
                active={isBookmarked(post.id)}
                busy={bookmarkBusy}
                onToggle={handleToggleBookmark}
                label="Save"
              />
            </footer>
            <div className={`${feedStyles.postActions} newsfeed-lightbox__actions`}>
              <NewsfeedExplain key={`explain:${post.id}`}
                post={{ ...post, content: post.content ?? "" }}
                imageUrls={imageUrl ? [imageUrl] : (post.images ?? [])}
              />
              <NewsfeedShare key={`share:${post.id}`} post={post} imageUrl={imageUrl} />
            </div>
          </article>
        </div>
      </div>
    </div>,
    portalTarget,
  );
}

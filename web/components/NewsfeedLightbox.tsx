"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Image from "next/image";
import { ChevronLeft, ChevronRight, Link as LinkIcon } from "lucide-react";
import type { MarketEarPost } from "@/components/DashboardNewsFeed";
import { formatAbsolute, formatRelative, formatTime } from "@/lib/newsfeedTime";
import { useDialogChrome } from "@/lib/useDialogChrome";
import { useBookmarks } from "@/lib/useBookmarks";
import StarToggle from "@/components/StarToggle";

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

/**
 * NewsfeedLightbox — opens when a user clicks an image in the dashboard
 * news rail. Renders the image at full size on the left and the article
 * copy (title, body, tags, timestamp, source link) on the right so the
 * user can read the context without leaving the workspace.
 *
 * Keyboard:
 *   Esc       dismiss
 *   ←  / →    cycle to the previous / next post with an image
 *
 * Mouse: scrim click dismisses (there's no close X — tapping outside the
 * panel closes it); on-screen chevrons trigger the same prev/next as the
 * arrow keys.
 */
export default function NewsfeedLightbox({
  focus,
  onDismiss,
  onNavigate,
  canNavigatePrev = false,
  canNavigateNext = false,
}: NewsfeedLightboxProps) {
  const touchStartX = useRef<number | null>(null);
  const touchStartY = useRef<number | null>(null);
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

  useEffect(() => {
    if (!focus) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onDismiss();
        return;
      }
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
  }, [focus, onDismiss, onNavigate, canNavigatePrev, canNavigateNext]);

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
        },
      });
    } catch {
      // hook already rolled back the optimistic state
    } finally {
      setBookmarkBusy(false);
    }
  }, [focus, toggleBookmark]);

  if (!focus || !portalTarget) return null;

  const { post, imageUrl } = focus;
  const tags = Array.isArray(post.tags) ? post.tags : [];
  const relative = formatRelative(post.isoTimestamp);
  const time = formatTime(post.isoTimestamp);
  const absolute = formatAbsolute(post.isoTimestamp);

  function handleTouchStart(event: React.TouchEvent<HTMLDivElement>) {
    const touch = event.touches[0];
    touchStartX.current = touch.clientX;
    touchStartY.current = touch.clientY;
  }

  function handleTouchEnd(event: React.TouchEvent<HTMLDivElement>) {
    if (!onNavigate || touchStartX.current == null || touchStartY.current == null) return;
    const touch = event.changedTouches[0];
    const dx = touch.clientX - touchStartX.current;
    const dy = touch.clientY - touchStartY.current;
    touchStartX.current = null;
    touchStartY.current = null;
    if (Math.abs(dx) < 48 || Math.abs(dx) < Math.abs(dy) * 1.25) return;
    if (dx > 0 && canNavigatePrev) {
      onNavigate(-1);
    } else if (dx < 0 && canNavigateNext) {
      onNavigate(1);
    }
  }

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
      <button
        type="button"
        className="newsfeed-lightbox__scrim"
        onClick={onDismiss}
        aria-label="Dismiss lightbox"
        data-testid="newsfeed-lightbox-scrim"
      />
      {onNavigate && canNavigatePrev ? (
        <button
          type="button"
          className="newsfeed-lightbox__nav newsfeed-lightbox__nav--prev"
          onClick={() => onNavigate(-1)}
          aria-label="Previous post"
          data-testid="newsfeed-lightbox-prev"
        >
          <ChevronLeft size={20} />
        </button>
      ) : null}
      {onNavigate && canNavigateNext ? (
        <button
          type="button"
          className="newsfeed-lightbox__nav newsfeed-lightbox__nav--next"
          onClick={() => onNavigate(1)}
          aria-label="Next post"
          data-testid="newsfeed-lightbox-next"
        >
          <ChevronRight size={20} />
        </button>
      ) : null}
      <div className="newsfeed-lightbox__frame">
        <div
          className="newsfeed-lightbox__panel"
          onTouchStart={handleTouchStart}
          onTouchEnd={handleTouchEnd}
        >
          <div className="newsfeed-lightbox__media">
            <Image
              src={imageUrl}
              alt={post.title}
              width={1600}
              height={900}
              sizes="(max-width: 900px) 100vw, 60vw"
              className="newsfeed-lightbox__image"
              priority
            />
          </div>

          <article className="newsfeed-lightbox__copy">
            <header className="newsfeed-lightbox__head">
              <p className="newsfeed-lightbox__kicker">
                Live Market Analysis · {relative}
                {time ? ` at ${time}` : ""}
              </p>
              <h2 className="newsfeed-lightbox__title">{post.title}</h2>
            </header>

            {post.content ? (
              <p className="newsfeed-lightbox__body">{post.content}</p>
            ) : null}

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
                <LinkIcon size={12} />
                <span>Open original</span>
              </a>
              <StarToggle
                active={isBookmarked(post.id)}
                busy={bookmarkBusy}
                onToggle={handleToggleBookmark}
                label="Save"
              />
              <span className="newsfeed-lightbox__timestamp" title={absolute}>
                {absolute}
              </span>
            </footer>
            {onNavigate && (canNavigatePrev || canNavigateNext) ? (
              <p className="newsfeed-lightbox__hint" aria-hidden>
                ← / → to cycle posts · Esc to close
              </p>
            ) : null}
          </article>
        </div>
      </div>
    </div>,
    portalTarget,
  );
}

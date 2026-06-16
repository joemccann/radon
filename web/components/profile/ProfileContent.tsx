"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { useUser } from "@clerk/nextjs";
import { useProfile } from "@/lib/useProfile";
import { useBookmarks, type Bookmark } from "@/lib/useBookmarks";
import { useWatchlist, type WatchlistEntry } from "@/lib/useWatchlist";
import { useViewport } from "@/lib/useViewport";
import { useTickerNav } from "@/lib/useTickerNav";
import StarToggle from "@/components/StarToggle";
import { resizeImageToSquareDataUrl } from "@/lib/profile/resizeImage";
import { formatRelative } from "@/lib/newsfeedTime";
import type { PriceData } from "@/lib/pricesProtocol";

const USERNAME_PATTERN = /^[A-Za-z0-9_\- ]{1,32}$/;

type ProfileTab = "bookmarks" | "watchlist";

/** Defensive read of a bookmark snapshot. The snapshot is `unknown` per the
 *  Phase 1 contract; news posts shaped like MarketEarPost are the common case. */
type BookmarkSnapshotView = {
  headline: string;
  source: string | null;
  thumbnail: string | null;
  timestamp: string | null;
};

function readSnapshot(bookmark: Bookmark): BookmarkSnapshotView {
  const snap = (bookmark.snapshot ?? {}) as Record<string, unknown>;
  const asString = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);
  const firstImage = Array.isArray(snap.images)
    ? (snap.images.find((i) => typeof i === "string") as string | undefined) ?? null
    : null;
  return {
    headline:
      asString(snap.title) ?? asString(snap.headline) ?? asString(snap.content) ?? bookmark.post_id,
    source: asString(snap.source) ?? asString(snap.url),
    thumbnail: asString(snap.thumbnail) ?? firstImage,
    timestamp: asString(snap.timestamp) ?? asString(snap.createdAt) ?? bookmark.saved_at,
  };
}

function initialsFor(name: string | null, email: string | null): string {
  const source = (name ?? email ?? "").trim();
  if (!source) return "·";
  const parts = source.replace(/@.*$/, "").split(/[\s._-]+/).filter(Boolean);
  if (parts.length === 0) return source.slice(0, 2).toUpperCase();
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

function Avatar({
  url,
  initials,
  size,
}: {
  url: string | null;
  initials: string;
  size: number;
}) {
  if (url) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img className="profile-avatar__img" src={url} alt="" width={size} height={size} />;
  }
  return (
    <span className="profile-avatar__monogram" style={{ fontSize: Math.round(size * 0.36) }}>
      {initials}
    </span>
  );
}

/* ─── Source tag pill for bookmarks (mobile) ─────────── */

function sourceDomain(source: string | null): string {
  if (!source) return "SOURCE";
  try {
    const u = new URL(source.startsWith("http") ? source : `https://${source}`);
    return u.hostname.replace(/^www\./, "").toUpperCase();
  } catch {
    return source.slice(0, 16).toUpperCase();
  }
}

export default function ProfileContent({ prices }: { prices?: Record<string, PriceData> }) {
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;
  const [tab, setTab] = useState<ProfileTab>("bookmarks");

  const { profile, saveProfile } = useProfile();
  const { user } = useUser();
  const { bookmarks, isLoading: bookmarksLoading, toggleBookmark } = useBookmarks();
  const { watchlist, isLoading: watchlistLoading, toggleWatch } = useWatchlist();

  const email = user?.primaryEmailAddress?.emailAddress ?? null;
  const clerkImage = user?.imageUrl ?? null;
  const avatarUrl = profile?.avatar_url ?? clerkImage ?? null;
  const initials = initialsFor(profile?.username ?? null, email);

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [photoBusy, setPhotoBusy] = useState(false);
  const [photoError, setPhotoError] = useState<string | null>(null);

  const [usernameDraft, setUsernameDraft] = useState<string | null>(null);
  const usernameValue = usernameDraft ?? profile?.username ?? "";
  const [usernameError, setUsernameError] = useState<string | null>(null);
  const [usernameSaving, setUsernameSaving] = useState(false);

  const onPickPhoto = useCallback(() => {
    setPhotoError(null);
    fileInputRef.current?.click();
  }, []);

  const onPhotoSelected = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      event.target.value = "";
      if (!file) return;
      setPhotoBusy(true);
      setPhotoError(null);
      try {
        const dataUrl = await resizeImageToSquareDataUrl(file, 256);
        await saveProfile({ avatar_url: dataUrl });
      } catch {
        setPhotoError("Could not process that image. Try a smaller JPEG or PNG.");
      } finally {
        setPhotoBusy(false);
      }
    },
    [saveProfile],
  );

  const validateUsername = useCallback((raw: string): string | null => {
    const trimmed = raw.trim();
    if (trimmed === "") return null; // empty clears the username
    if (!USERNAME_PATTERN.test(trimmed)) {
      return "1 to 32 chars: letters, numbers, space, hyphen or underscore.";
    }
    return null;
  }, []);

  const commitUsername = useCallback(async () => {
    if (usernameDraft === null) return;
    const trimmed = usernameDraft.trim();
    if (trimmed === (profile?.username ?? "")) {
      setUsernameDraft(null);
      setUsernameError(null);
      return;
    }
    const error = validateUsername(trimmed);
    if (error) {
      setUsernameError(error);
      return;
    }
    setUsernameSaving(true);
    setUsernameError(null);
    try {
      await saveProfile({ username: trimmed });
      setUsernameDraft(null);
    } catch {
      setUsernameError("Could not save. Try again.");
    } finally {
      setUsernameSaving(false);
    }
  }, [usernameDraft, profile?.username, validateUsername, saveProfile]);

  if (compact) {
    return (
      <div className="profile-surface profile-surface--mobile">
        {/* ── Mobile header: avatar LEFT of username ── */}
        <section className="profile-header panel">
          <div className="profile-m-identity">
            <div className="profile-avatar profile-m-avatar" data-busy={photoBusy ? "true" : undefined}>
              <Avatar url={avatarUrl} initials={initials} size={48} />
              {photoBusy ? <span className="profile-avatar__spinner" aria-hidden /> : null}
            </div>
            <div className="profile-m-identity__fields">
              <label className="profile-field">
                <span className="profile-field__label">Username</span>
                <input
                  className={`profile-field__input${usernameError ? " profile-field__input--error" : ""}`}
                  value={usernameValue}
                  placeholder="Set a display name"
                  spellCheck={false}
                  disabled={usernameSaving}
                  onChange={(e) => {
                    setUsernameDraft(e.target.value);
                    setUsernameError(validateUsername(e.target.value));
                  }}
                  onBlur={commitUsername}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") e.currentTarget.blur();
                  }}
                />
                {usernameError ? (
                  <span className="profile-field__error">{usernameError}</span>
                ) : (
                  <span className="profile-field__hint">Saves on blur or Enter.</span>
                )}
              </label>
              <div className="profile-field">
                <span className="profile-field__label">Email</span>
                <span className="profile-field__readonly">{email ?? "Not available"}</span>
              </div>
            </div>
          </div>
          {photoError ? <span className="profile-field__error" style={{ display: "block", marginTop: "8px" }}>{photoError}</span> : null}
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="profile-file-input"
            onChange={onPhotoSelected}
          />
        </section>

        {/* ── Mobile tab row: full-width segment control ── */}
        <div className="m-segment" role="tablist" aria-label="Profile sections">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "bookmarks"}
            className={`m-segment__item${tab === "bookmarks" ? " m-segment__item--active" : ""}`}
            onClick={() => setTab("bookmarks")}
          >
            Bookmarks
            <span className="profile-tab__count">{bookmarks.length}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "watchlist"}
            className={`m-segment__item${tab === "watchlist" ? " m-segment__item--active" : ""}`}
            onClick={() => setTab("watchlist")}
          >
            Watchlist
            <span className="profile-tab__count">{watchlist.length}</span>
          </button>
        </div>

        {tab === "bookmarks" ? (
          <BookmarksPanelMobile
            bookmarks={bookmarks}
            isLoading={bookmarksLoading}
            onUnstar={(b) => toggleBookmark({ id: b.post_id, snapshot: b.snapshot })}
          />
        ) : (
          <WatchlistPanelMobile
            watchlist={watchlist}
            isLoading={watchlistLoading}
            prices={prices}
            onRemove={(symbol) => toggleWatch(symbol)}
          />
        )}

        {/* ── Sticky Save Profile button ── */}
        <div className="m-sticky-cta">
          <button
            type="button"
            className="profile-m-save-btn"
            onClick={onPickPhoto}
            disabled={photoBusy}
          >
            {photoBusy ? "Processing..." : "Change photo"}
          </button>
        </div>
      </div>
    );
  }

  /* ── Desktop layout ── */
  return (
    <div className="profile-surface">
      <section className="profile-header panel">
        <div className="profile-header__identity">
          <div className="profile-avatar" data-busy={photoBusy ? "true" : undefined}>
            <Avatar url={avatarUrl} initials={initials} size={96} />
            {photoBusy ? <span className="profile-avatar__spinner" aria-hidden /> : null}
          </div>
          <div className="profile-header__fields">
            <label className="profile-field">
              <span className="profile-field__label">Username</span>
              <input
                className={`profile-field__input${usernameError ? " profile-field__input--error" : ""}`}
                value={usernameValue}
                placeholder="Set a display name"
                spellCheck={false}
                disabled={usernameSaving}
                onChange={(e) => {
                  setUsernameDraft(e.target.value);
                  setUsernameError(validateUsername(e.target.value));
                }}
                onBlur={commitUsername}
                onKeyDown={(e) => {
                  if (e.key === "Enter") e.currentTarget.blur();
                }}
              />
              {usernameError ? (
                <span className="profile-field__error">{usernameError}</span>
              ) : (
                <span className="profile-field__hint">Saves on blur or Enter.</span>
              )}
            </label>
            <div className="profile-field">
              <span className="profile-field__label">Email</span>
              <span className="profile-field__readonly">{email ?? "Not available"}</span>
            </div>
            <div className="profile-header__actions">
              <button
                type="button"
                className="profile-photo-btn"
                onClick={onPickPhoto}
                disabled={photoBusy}
              >
                {photoBusy ? "Processing..." : "Change photo"}
              </button>
              {photoError ? <span className="profile-field__error">{photoError}</span> : null}
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              className="profile-file-input"
              onChange={onPhotoSelected}
            />
          </div>
        </div>
      </section>

      <div className="profile-tabs" role="tablist" aria-label="Profile sections">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "bookmarks"}
          className={`profile-tab${tab === "bookmarks" ? " profile-tab--active" : ""}`}
          onClick={() => setTab("bookmarks")}
        >
          Bookmarks
          <span className="profile-tab__count">{bookmarks.length}</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "watchlist"}
          className={`profile-tab${tab === "watchlist" ? " profile-tab--active" : ""}`}
          onClick={() => setTab("watchlist")}
        >
          Watchlist
          <span className="profile-tab__count">{watchlist.length}</span>
        </button>
      </div>

      {tab === "bookmarks" ? (
        <BookmarksPanel
          bookmarks={bookmarks}
          isLoading={bookmarksLoading}
          onUnstar={(b) => toggleBookmark({ id: b.post_id, snapshot: b.snapshot })}
        />
      ) : (
        <WatchlistPanel
          watchlist={watchlist}
          isLoading={watchlistLoading}
          prices={prices}
          onRemove={(symbol) => toggleWatch(symbol)}
        />
      )}
    </div>
  );
}

/* ─── Desktop BookmarksPanel ─────────────────────────── */

function BookmarksPanel({
  bookmarks,
  isLoading,
  onUnstar,
}: {
  bookmarks: Bookmark[];
  isLoading: boolean;
  onUnstar: (bookmark: Bookmark) => void | Promise<void>;
}) {
  if (isLoading && bookmarks.length === 0) {
    return <div className="profile-empty">Loading bookmarks...</div>;
  }
  if (bookmarks.length === 0) {
    return (
      <div className="profile-empty">
        No bookmarks yet. Star an article in the feed to save it here.
      </div>
    );
  }
  return (
    <ul className="profile-list" aria-label="Saved articles">
      {bookmarks.map((bookmark) => {
        const view = readSnapshot(bookmark);
        return (
          <li className="profile-bookmark panel" key={bookmark.id}>
            {view.thumbnail ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img className="profile-bookmark__thumb" src={view.thumbnail} alt="" />
            ) : (
              <span className="profile-bookmark__thumb profile-bookmark__thumb--empty" aria-hidden />
            )}
            <div className="profile-bookmark__body">
              <span className="profile-bookmark__headline">{view.headline}</span>
              <div className="profile-bookmark__meta">
                {view.source ? <span className="profile-bookmark__source">{view.source}</span> : null}
                {view.timestamp ? (
                  <span className="profile-bookmark__time">{formatRelative(view.timestamp)}</span>
                ) : null}
              </div>
            </div>
            <StarToggle active onToggle={() => onUnstar(bookmark)} label="SAVED" />
          </li>
        );
      })}
    </ul>
  );
}

/* ─── Mobile BookmarksPanel ──────────────────────────── */

function BookmarksPanelMobile({
  bookmarks,
  isLoading,
  onUnstar,
}: {
  bookmarks: Bookmark[];
  isLoading: boolean;
  onUnstar: (bookmark: Bookmark) => void | Promise<void>;
}) {
  if (isLoading && bookmarks.length === 0) {
    return <div className="profile-empty">Loading bookmarks...</div>;
  }
  if (bookmarks.length === 0) {
    return (
      <div className="profile-empty">
        No bookmarks yet. Star an article in the feed to save it here.
      </div>
    );
  }
  return (
    <ul className="profile-list" aria-label="Saved articles">
      {bookmarks.map((bookmark) => {
        const view = readSnapshot(bookmark);
        const domain = sourceDomain(view.source);
        return (
          <li className="profile-bookmark panel profile-bookmark--mobile" key={bookmark.id}>
            <div className="profile-bookmark__body">
              <div className="profile-bookmark__mobile-meta">
                <span className="m-pill profile-bookmark__source-pill">{domain}</span>
                {view.timestamp ? (
                  <span className="profile-bookmark__time">{formatRelative(view.timestamp)}</span>
                ) : null}
              </div>
              <span className="profile-bookmark__headline profile-bookmark__headline--mobile">
                {view.headline}
              </span>
            </div>
            <StarToggle active onToggle={() => onUnstar(bookmark)} label="SAVED" />
          </li>
        );
      })}
    </ul>
  );
}

/* ─── Desktop WatchlistPanel ─────────────────────────── */

function WatchlistPanel({
  watchlist,
  isLoading,
  prices,
  onRemove,
}: {
  watchlist: WatchlistEntry[];
  isLoading: boolean;
  prices?: Record<string, PriceData>;
  onRemove: (symbol: string) => void | Promise<void>;
}) {
  const { navigateToTicker } = useTickerNav();

  if (isLoading && watchlist.length === 0) {
    return <div className="profile-empty">Loading watchlist...</div>;
  }
  if (watchlist.length === 0) {
    return (
      <div className="profile-empty">
        No symbols watched yet. Star a ticker to track it here.
      </div>
    );
  }
  return (
    <ul className="profile-list" aria-label="Watched symbols">
      {watchlist.map((entry) => (
        <WatchRow
          key={entry.id}
          entry={entry}
          price={prices?.[entry.symbol]}
          onOpen={() => navigateToTicker(entry.symbol)}
          onRemove={() => onRemove(entry.symbol)}
        />
      ))}
    </ul>
  );
}

/* ─── Mobile WatchlistPanel ──────────────────────────── */

function WatchlistPanelMobile({
  watchlist,
  isLoading,
  prices,
  onRemove,
}: {
  watchlist: WatchlistEntry[];
  isLoading: boolean;
  prices?: Record<string, PriceData>;
  onRemove: (symbol: string) => void | Promise<void>;
}) {
  const { navigateToTicker } = useTickerNav();

  if (isLoading && watchlist.length === 0) {
    return <div className="profile-empty">Loading watchlist...</div>;
  }
  if (watchlist.length === 0) {
    return (
      <div className="profile-empty">
        No symbols watched yet. Star a ticker to track it here.
      </div>
    );
  }
  return (
    <ul className="profile-list" aria-label="Watched symbols">
      {watchlist.map((entry) => (
        <WatchRowMobile
          key={entry.id}
          entry={entry}
          price={prices?.[entry.symbol]}
          onOpen={() => navigateToTicker(entry.symbol)}
          onRemove={() => onRemove(entry.symbol)}
        />
      ))}
    </ul>
  );
}

/* ─── Desktop WatchRow ───────────────────────────────── */

function WatchRow({
  entry,
  price,
  onOpen,
  onRemove,
}: {
  entry: WatchlistEntry;
  price?: PriceData;
  onOpen: () => void;
  onRemove: () => void | Promise<void>;
}) {
  const change = useMemo(() => {
    if (!price || price.last == null || price.close == null || price.close === 0) return null;
    const abs = price.last - price.close;
    const pct = (abs / Math.abs(price.close)) * 100;
    return { abs, pct, tone: abs >= 0 ? "positive" : "negative" as const };
  }, [price]);

  return (
    <li className="profile-watch panel">
      <button type="button" className="profile-watch__symbol" onClick={onOpen}>
        {entry.symbol}
      </button>
      <span className="profile-watch__sector">{entry.sector ?? "---"}</span>
      <span className="profile-watch__price">
        {price?.last != null ? (
          <>
            <span className="profile-watch__last">{price.last.toFixed(2)}</span>
            {change ? (
              <span className={`profile-watch__chg profile-watch__chg--${change.tone}`}>
                {change.abs >= 0 ? "+" : ""}
                {change.pct.toFixed(2)}%
              </span>
            ) : null}
          </>
        ) : (
          <span className="profile-watch__last profile-watch__last--muted">---</span>
        )}
      </span>
      <StarToggle active onToggle={onRemove} size="sm" />
    </li>
  );
}

/* ─── Mobile WatchRow ────────────────────────────────── */

function WatchRowMobile({
  entry,
  price,
  onOpen,
  onRemove,
}: {
  entry: WatchlistEntry;
  price?: PriceData;
  onOpen: () => void;
  onRemove: () => void | Promise<void>;
}) {
  const change = useMemo(() => {
    if (!price || price.last == null || price.close == null || price.close === 0) return null;
    const abs = price.last - price.close;
    const pct = (abs / Math.abs(price.close)) * 100;
    return { abs, pct, tone: abs >= 0 ? "positive" : "negative" as const };
  }, [price]);

  return (
    <li className="profile-watch panel profile-watch--mobile m-card-press">
      {/* Full-row press target navigates to ticker */}
      <button
        type="button"
        className="profile-watch-m__press"
        onClick={onOpen}
        aria-label={`Open ${entry.symbol}`}
      >
        <div className="profile-watch-m__primary">
          <span className="profile-watch__symbol" style={{ pointerEvents: "none" }}>{entry.symbol}</span>
          {entry.sector ? (
            <span className="profile-watch-m__sector">{entry.sector}</span>
          ) : null}
        </div>
        <span className="profile-watch__price">
          {price?.last != null ? (
            <>
              <span className="profile-watch__last">{price.last.toFixed(2)}</span>
              {change ? (
                <span className={`profile-watch__chg profile-watch__chg--${change.tone}`}>
                  {change.abs >= 0 ? "+" : ""}
                  {change.pct.toFixed(2)}%
                </span>
              ) : null}
            </>
          ) : (
            <span className="profile-watch__last profile-watch__last--muted">---</span>
          )}
        </span>
      </button>
      <StarToggle active onToggle={onRemove} size="sm" />
    </li>
  );
}

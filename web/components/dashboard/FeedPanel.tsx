"use client";

import { useMemo, useState } from "react";
import Image from "next/image";

import { formatRelative, formatTime } from "@/lib/newsfeedTime";
import { useNewsfeedPosts, NEWSFEED_ORIGIN_HREF } from "@/lib/useNewsfeedPosts";
import NewsfeedLightbox, { type NewsfeedLightboxFocus } from "@/components/NewsfeedLightbox";

const HEADLINE_COUNT = 5;

/**
 * FeedPanel — desktop feed: one featured story rendered in full (title, body,
 * image, tags) plus a compact headline list that swaps the featured story on
 * click. The mobile shell keeps the paginated DashboardNewsFeed list instead.
 */
export default function FeedPanel() {
  const { posts, loading, error, lastUpdated } = useNewsfeedPosts();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [lightboxFocus, setLightboxFocus] = useState<NewsfeedLightboxFocus | null>(null);

  const headlines = useMemo(() => posts.slice(0, HEADLINE_COUNT), [posts]);
  const featured = useMemo(
    () => headlines.find((p) => p.id === selectedId) ?? headlines[0] ?? null,
    [headlines, selectedId],
  );

  const featuredImage = featured?.images?.[0] ?? null;
  const lastSample = lastUpdated
    ? new Date(lastUpdated).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })
    : "—";

  return (
    <section className="feed-panel snapshot-card">
      <span className="panel-edge-trace" aria-hidden />
      <header className="feed-panel__header">
        <div>
          <p className="panel-eyebrow">Feed / 01</p>
          <h3 className="panel-title">Live market analysis</h3>
        </div>
        <span className="feed-panel__header-meta">
          {posts.length > 0 ? `${posts.length} STORIES` : null}
        </span>
      </header>

      {loading ? (
        <div className="news-feed-empty">Collecting Market Ear posts…</div>
      ) : error ? (
        <div className="news-feed-error">{error}</div>
      ) : !featured ? (
        <div className="news-feed-empty">No Market Ear posts captured yet. Ensure the scraper is running.</div>
      ) : (
        <>
          <article className="feed-panel__featured" data-testid="feed-featured">
            <a href={featured.href} target="_blank" rel="noopener noreferrer" className="feed-panel__featured-title-link">
              <h4 className="feed-panel__featured-title">{featured.title}</h4>
            </a>
            {featured.content ? (
              <p className="feed-panel__featured-body">{featured.content}</p>
            ) : null}
            {featuredImage ? (
              <button
                type="button"
                className="feed-panel__media"
                onClick={() => setLightboxFocus({ post: featured, imageUrl: featuredImage })}
                aria-label={`Open lightbox for: ${featured.title}`}
              >
                <Image
                  src={featuredImage}
                  alt={featured.title}
                  width={1200}
                  height={675}
                  sizes="(max-width: 1280px) 100vw, 46vw"
                  className="feed-panel__media-image"
                  priority={false}
                />
              </button>
            ) : null}
            <div className="feed-panel__featured-foot">
              {(featured.tags ?? []).map((tag) => (
                <span key={tag} className="feed-panel__tag">{tag}</span>
              ))}
              <span className="feed-panel__featured-time">
                {formatRelative(featured.isoTimestamp)}
                {formatTime(featured.isoTimestamp) ? ` · ${formatTime(featured.isoTimestamp)}` : ""}
              </span>
            </div>
          </article>

          <div className="feed-panel__divider">
            <span className="feed-panel__divider-label">Headlines</span>
          </div>
          <ul className="feed-panel__headlines">
            {headlines.map((post) => {
              const active = post.id === featured.id;
              return (
                <li key={post.id}>
                  <button
                    type="button"
                    className={`feed-panel__headline${active ? " is-active" : ""}`}
                    onClick={() => setSelectedId(post.id)}
                    aria-current={active || undefined}
                  >
                    <span className="feed-panel__headline-title">{post.title}</span>
                    <span className="feed-panel__headline-meta">
                      {post.tags?.[0] ? <span>{post.tags[0]}</span> : null}
                      <span>{formatRelative(post.isoTimestamp)}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}

      <footer className="panel-meta-rail" aria-label="Feed calibration">
        <div className="panel-meta-rail-item">
          <span className="k">source</span>
          <span className="v">Market Ear</span>
        </div>
        <div className="panel-meta-rail-item">
          <span className="k">last.sample</span>
          <span className="v">{lastSample}</span>
        </div>
        <a
          className="panel-meta-rail-item feed-panel__full-feed"
          href={NEWSFEED_ORIGIN_HREF}
          target="_blank"
          rel="noopener noreferrer"
        >
          FULL FEED →
        </a>
      </footer>
      <NewsfeedLightbox
        focus={lightboxFocus}
        onDismiss={() => setLightboxFocus(null)}
        onNavigate={() => {}}
        canNavigatePrev={false}
        canNavigateNext={false}
      />
    </section>
  );
}

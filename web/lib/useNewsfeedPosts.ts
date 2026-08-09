"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { readOfflineMeta } from "./offline/offlineStatus";
import {
  reportFetchFailure,
  reportFetchSuccess,
  reportOfflineServed,
} from "./offline/offlineSignals";

const POSTS_ENDPOINT = "/api/newsfeed/posts";
const POSTS_FALLBACK_ENDPOINT = "/data/posts.json";
const REFRESH_INTERVAL_MS = 2 * 60 * 1000;

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

export type NormalisedPost = MarketEarPost & {
  isoTimestamp: string;
  timestampMs: number;
  href: string;
};

type FetchOptions = {
  signal?: AbortSignal;
  mode?: "initial" | "refresh" | "silent";
};

export function buildPostHref(id: string) {
  if (!id) return "https://themarketear.com/newsfeed";
  return `https://themarketear.com/posts/${encodeURIComponent(id)}`;
}

export type NewsfeedPosts = {
  posts: NormalisedPost[];
  loading: boolean;
  refreshing: boolean;
  error: string | null;
  lastUpdated: string | null;
  refresh: () => Promise<void>;
};

/** Shared Market Ear feed loader: DB-backed endpoint with static-JSON
 *  fallback, offline signal reporting, 2-minute background refresh, and
 *  last-good hold on failed refreshes. */
export function useNewsfeedPosts(): NewsfeedPosts {
  const [posts, setPosts] = useState<NormalisedPost[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  // Mirrors `posts` for the fetch catch: a failed background refresh must
  // hold the last-good list instead of swapping it for an error panel.
  const postsRef = useRef<NormalisedPost[]>([]);

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

  const refresh = useCallback(async () => {
    await loadPosts({ mode: "refresh" });
  }, [loadPosts]);

  return { posts, loading, refreshing, error, lastUpdated, refresh };
}

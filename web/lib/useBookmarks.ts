"use client";

import { useCallback, useEffect, useState } from "react";

export type Bookmark = {
  id: string;
  post_id: string;
  snapshot: unknown;
  saved_at: string;
};

type UseBookmarksReturn = {
  bookmarks: Bookmark[];
  isLoading: boolean;
  error: string | null;
  retry: () => Promise<void>;
  isBookmarked: (postId: string) => boolean;
  toggleBookmark: (post: { id: string; snapshot?: unknown }) => Promise<void>;
};

// Module-level shared store: one fetch hydrates every consumer.
let cache: Bookmark[] = [];
let loaded = false;
let loadError: string | null = null;
let loadGeneration = 0;
let inFlight: { generation: number; controller: AbortController; promise: Promise<void> } | null = null;
let mutationQueue: Promise<void> = Promise.resolve();
const subscribers = new Set<() => void>();

function notify(): void {
  for (const fn of subscribers) fn();
}

function setCache(next: Bookmark[]): void {
  cache = next;
  notify();
}

async function loadBookmarks(force = false): Promise<void> {
  if (inFlight && !force) return inFlight.promise;
  if (force) inFlight?.controller.abort();
  const generation = ++loadGeneration;
  const controller = new AbortController();
  const promise = (async () => {
    try {
      const res = await fetch("/api/bookmarks", { cache: "no-store", signal: controller.signal });
      if (!res.ok) throw new Error("Failed to fetch bookmarks");
      const json = (await res.json()) as { bookmarks: Bookmark[] };
      if (generation !== loadGeneration) return;
      cache = Array.isArray(json.bookmarks) ? json.bookmarks : [];
      loaded = true;
      loadError = null;
    } catch (err) {
      if (controller.signal.aborted || generation !== loadGeneration) return;
      loaded = false;
      loadError = err instanceof Error ? err.message : "Failed to fetch bookmarks";
    } finally {
      if (generation === loadGeneration) {
        inFlight = null;
        notify();
      }
    }
  })();
  inFlight = { generation, controller, promise };
  notify();
  return promise;
}

function invalidateReads(): void {
  loadGeneration += 1;
  inFlight?.controller.abort();
  inFlight = null;
}

function enqueueMutation(operation: () => Promise<void>): Promise<void> {
  const result = mutationQueue.then(operation, operation);
  mutationQueue = result.catch(() => undefined);
  return result;
}

export function useBookmarks(): UseBookmarksReturn {
  const [, forceRender] = useState(0);
  const [isLoading, setIsLoading] = useState(!loaded);

  useEffect(() => {
    const rerender = () => {
      forceRender((n) => n + 1);
      setIsLoading(Boolean(inFlight));
    };
    subscribers.add(rerender);
    if (!loaded && !inFlight) void loadBookmarks();
    else setIsLoading(Boolean(inFlight));
    return () => {
      subscribers.delete(rerender);
    };
  }, []);

  const isBookmarked = useCallback((postId: string) => {
    return cache.some((b) => b.post_id === postId);
  }, []);

  const toggleBookmark = useCallback((post: { id: string; snapshot?: unknown }) => enqueueMutation(async () => {
    invalidateReads();
    const existing = cache.find((bookmark) => bookmark.post_id === post.id);

    if (existing) {
      setCache(cache.filter((b) => b.post_id !== post.id));
      try {
        const res = await fetch(`/api/bookmarks/${encodeURIComponent(post.id)}`, {
          method: "DELETE",
          cache: "no-store",
        });
        if (!res.ok) throw new Error("Failed to remove bookmark");
        await loadBookmarks(true);
      } catch (err) {
        if (!cache.some((bookmark) => bookmark.post_id === post.id)) {
          setCache([existing, ...cache]);
        }
        throw err;
      }
      return;
    }

    const optimistic: Bookmark = {
      id: `optimistic-${post.id}`,
      post_id: post.id,
      snapshot: post.snapshot ?? null,
      saved_at: new Date().toISOString(),
    };
    setCache([optimistic, ...cache]);
    try {
      const res = await fetch("/api/bookmarks", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ post_id: post.id, snapshot: post.snapshot }),
      });
      if (!res.ok) throw new Error("Failed to save bookmark");
      await loadBookmarks(true);
    } catch (err) {
      setCache(cache.filter((bookmark) => bookmark.id !== optimistic.id));
      throw err;
    }
  }), []);

  const retry = useCallback(() => loadBookmarks(true), []);

  return { bookmarks: cache, isLoading, error: loadError, retry, isBookmarked, toggleBookmark };
}

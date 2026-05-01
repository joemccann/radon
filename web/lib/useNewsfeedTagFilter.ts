"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

const TAGS_PARAM = "tags";

export type NewsfeedTagFilter = {
  selectedTags: ReadonlySet<string>;
  toggleTag: (tag: string) => void;
  clearTags: () => void;
};

function parseTags(value: string | null): string[] {
  if (!value) return [];
  return value
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
}

function setsEqual(a: Set<string>, b: Set<string>): boolean {
  if (a.size !== b.size) return false;
  for (const v of a) if (!b.has(v)) return false;
  return true;
}

function serializeTags(tags: Iterable<string>): string {
  return Array.from(tags).join(",");
}

export function useNewsfeedTagFilter(): NewsfeedTagFilter {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const tagsParamRaw = searchParams?.get(TAGS_PARAM) ?? "";

  const [selectedTags, setSelectedTags] = useState<Set<string>>(
    () => new Set(parseTags(tagsParamRaw || null)),
  );

  // External URL changes (back/forward, deep-link, server nav) update local state.
  // Depend on the raw string so re-renders that don't actually change the URL
  // don't clobber an optimistic in-progress toggle.
  useEffect(() => {
    const fromUrl = new Set(parseTags(tagsParamRaw || null));
    setSelectedTags((prev) => (setsEqual(prev, fromUrl) ? prev : fromUrl));
  }, [tagsParamRaw]);

  // Sync local state → URL after commit. Calling router.replace inside a
  // setState updater (or anywhere during render) trips React's
  // "Cannot update a component while rendering a different component"
  // because router.replace dispatches into the Router component.
  // Effect runs post-commit, so it's safe.
  const lastWrittenRef = useRef<string | null>(null);
  useEffect(() => {
    const desired = serializeTags(selectedTags);
    const current = tagsParamRaw;
    if (desired === current) {
      lastWrittenRef.current = desired;
      return;
    }
    if (lastWrittenRef.current === desired) return; // already wrote this, waiting for URL to settle
    lastWrittenRef.current = desired;

    const params = new URLSearchParams(searchParams?.toString() ?? "");
    if (desired.length === 0) params.delete(TAGS_PARAM);
    else params.set(TAGS_PARAM, desired);
    const query = params.toString();
    const url = query ? `${pathname}?${query}` : pathname ?? "/dashboard";
    router.replace(url, { scroll: false });
  }, [selectedTags, tagsParamRaw, searchParams, pathname, router]);

  const toggleTag = useCallback((tag: string) => {
    setSelectedTags((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  }, []);

  const clearTags = useCallback(() => {
    setSelectedTags((prev) => (prev.size === 0 ? prev : new Set()));
  }, []);

  return { selectedTags, toggleTag, clearTags };
}

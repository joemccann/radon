"use client";

import { useCallback, useEffect, useState } from "react";
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

  const writeUrl = useCallback(
    (next: Set<string>) => {
      const params = new URLSearchParams(searchParams?.toString() ?? "");
      if (next.size === 0) {
        params.delete(TAGS_PARAM);
      } else {
        params.set(TAGS_PARAM, serializeTags(next));
      }
      const query = params.toString();
      const url = query ? `${pathname}?${query}` : pathname ?? "/dashboard";
      router.replace(url, { scroll: false });
    },
    [router, pathname, searchParams],
  );

  const toggleTag = useCallback(
    (tag: string) => {
      setSelectedTags((prev) => {
        const next = new Set(prev);
        if (next.has(tag)) next.delete(tag);
        else next.add(tag);
        writeUrl(next);
        return next;
      });
    },
    [writeUrl],
  );

  const clearTags = useCallback(() => {
    setSelectedTags((prev) => {
      if (prev.size === 0) return prev;
      const next = new Set<string>();
      writeUrl(next);
      return next;
    });
  }, [writeUrl]);

  return { selectedTags, toggleTag, clearTags };
}

"use client";

/**
 * Purges legacy authenticated caches on initial identity resolution and every
 * account/sign-out transition. The current worker never stores protected data;
 * this clears entries left by older deployed workers, plus the in-memory
 * return cache and bookmarks store on an account change.
 *
 * Mounts inside ClerkThemeBridge (ClerkProvider context required). The
 * postMessage is a no-op when no SW controls the page (dev, first visit).
 */
import { useEffect, useRef } from "react";
import { useAuth } from "@clerk/nextjs";
import { purgeReturnCaches } from "@/lib/returnCache";
import { resetBookmarksCache } from "@/lib/useBookmarks";

export default function SignOutCachePurge() {
  const { isLoaded, isSignedIn, userId } = useAuth();
  const previousIdentity = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    if (!isLoaded) return;
    const identity = isSignedIn && userId ? userId : null;
    if (previousIdentity.current === identity) return;
    // In-memory per-user stores: clear on a transition only. At initial
    // resolution they hold nothing but the current user's own data.
    if (previousIdentity.current !== undefined) {
      purgeReturnCaches();
      resetBookmarksCache();
    }
    previousIdentity.current = identity;
    const message = { type: "radon-clear-caches", identity };
    try {
      navigator.serviceWorker?.controller?.postMessage(message);
      void navigator.serviceWorker?.getRegistration?.().then((registration) => {
        (registration?.active ?? registration?.waiting ?? registration?.installing)?.postMessage(message);
      }).catch(() => {});
      if (typeof caches !== "undefined") {
        void caches.keys().then((keys) => Promise.all(
          keys.filter((key) => key.startsWith("radon-pages-") || key.startsWith("radon-api-"))
            .map((key) => caches.delete(key)),
        )).catch(() => {});
      }
    } catch {
      // No SW / blocked storage: the current worker does not cache protected data.
    }
  }, [isLoaded, isSignedIn, userId]);

  return null;
}

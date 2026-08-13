"use client";

/**
 * Purges legacy authenticated caches on initial identity resolution and every
 * account/sign-out transition. The current worker never stores protected data;
 * this clears entries left by older deployed workers.
 *
 * Mounts inside ClerkThemeBridge (ClerkProvider context required). The
 * postMessage is a no-op when no SW controls the page (dev, first visit).
 */
import { useEffect, useRef } from "react";
import { useAuth } from "@clerk/nextjs";

export default function SignOutCachePurge() {
  const { isLoaded, isSignedIn, userId } = useAuth();
  const previousIdentity = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    if (!isLoaded) return;
    const identity = isSignedIn && userId ? userId : null;
    if (previousIdentity.current === identity) return;
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

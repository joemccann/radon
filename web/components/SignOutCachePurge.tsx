"use client";

/**
 * Posts the SW's "radon-clear-caches" purge (drops cached pages + API
 * payloads, keeps the static shell) when the Clerk session ends. Without
 * this the offline caches would keep serving the signed-out user's
 * portfolio/orders payloads from Cache Storage.
 *
 * Mounts inside ClerkThemeBridge (ClerkProvider context required). The
 * postMessage is a no-op when no SW controls the page (dev, first visit).
 */
import { useEffect, useRef } from "react";
import { useAuth } from "@clerk/nextjs";

export default function SignOutCachePurge() {
  const { isLoaded, isSignedIn } = useAuth();
  const wasSignedInRef = useRef(false);

  useEffect(() => {
    if (!isLoaded) return;
    if (isSignedIn) {
      wasSignedInRef.current = true;
      return;
    }
    if (!wasSignedInRef.current) return;
    wasSignedInRef.current = false;
    try {
      navigator.serviceWorker?.controller?.postMessage({ type: "radon-clear-caches" });
    } catch {
      // No SW / blocked storage — nothing cached to purge.
    }
  }, [isLoaded, isSignedIn]);

  return null;
}

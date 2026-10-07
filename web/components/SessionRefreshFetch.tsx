"use client";

/**
 * Installs the session-refresh fetch interceptor (lib/sessionRefreshFetch.ts)
 * over window.fetch for the life of the tab. Mounts inside ClerkThemeBridge
 * (ClerkProvider context required), BEFORE the data providers so their first
 * effects already go through it.
 */
import { useEffect, useRef } from "react";
import { useAuth } from "@clerk/nextjs";
import { installSessionRefreshFetch } from "@/lib/sessionRefreshFetch";

export default function SessionRefreshFetch() {
  const { getToken } = useAuth();
  const getTokenRef = useRef(getToken);
  getTokenRef.current = getToken;

  useEffect(
    // skipCache: the cached token is the expired one; a fresh mint also
    // rewrites the __session cookie before the replay goes out.
    () => installSessionRefreshFetch(window, () => getTokenRef.current({ skipCache: true })),
    [],
  );

  return null;
}

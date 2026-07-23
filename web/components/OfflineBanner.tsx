"use client";

/**
 * OfflineBanner — renders only while the device is offline (state-driven,
 * self-clearing; feedback_banner_only_actionable). Enter: 200ms rise added via
 * rAF class so the CSS transition stays interruptible. Exit: opacity-only
 * fade, unmount on transitionend with a timeout fallback.
 */
import { useEffect, useRef, useState } from "react";
import { useOfflineStatus } from "@/lib/offline/OfflineStatusContext";
import { formatAsOf } from "@/lib/offline/offlineStatus";

const EXIT_FALLBACK_MS = 240;

export default function OfflineBanner() {
  const { offline, cachedAt } = useOfflineStatus();
  const [rendered, setRendered] = useState(false);
  const [visible, setVisible] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const renderedRef = useRef(false);
  const exitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const clearExitTimer = () => {
      if (exitTimerRef.current) {
        clearTimeout(exitTimerRef.current);
        exitTimerRef.current = null;
      }
    };
    if (offline) {
      clearExitTimer();
      renderedRef.current = true;
      setLeaving(false);
      setRendered(true);
      const raf = requestAnimationFrame(() => setVisible(true));
      return () => cancelAnimationFrame(raf);
    }
    if (!renderedRef.current) return;
    setVisible(false);
    setLeaving(true);
    exitTimerRef.current = setTimeout(() => {
      exitTimerRef.current = null;
      renderedRef.current = false;
      setRendered(false);
      setLeaving(false);
    }, EXIT_FALLBACK_MS);
    return clearExitTimer;
  }, [offline]);

  if (!rendered) return null;

  const handleTransitionEnd = () => {
    if (!leaving) return;
    if (exitTimerRef.current) {
      clearTimeout(exitTimerRef.current);
      exitTimerRef.current = null;
    }
    renderedRef.current = false;
    setRendered(false);
    setLeaving(false);
  };

  const className = `offline-banner${visible ? " is-visible" : ""}${leaving ? " is-leaving" : ""}`;

  return (
    <div
      className={className}
      data-testid="offline-banner"
      role="status"
      aria-live="polite"
      onTransitionEnd={handleTransitionEnd}
    >
      <strong>OFFLINE</strong>
      <span>
        showing last known data
        {cachedAt != null ? ` · as of ${formatAsOf(cachedAt)}` : ""}
      </span>
    </div>
  );
}

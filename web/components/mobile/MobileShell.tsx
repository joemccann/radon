"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import MobileAppBar from "./MobileAppBar";
import MobileTabBar from "./MobileTabBar";
import MobileMoreDrawer from "./MobileMoreDrawer";
import MobileTickerSearch from "./MobileTickerSearch";
import styles from "../ClearShell.module.css";

type MobileShellProps = {
  title: string;
  isPageHeading?: boolean;
  ibConnected?: boolean;
  lastSync?: string | null;
};

/**
 * Mobile chrome overlay — renders top app bar, bottom tab bar, and overflow drawer
 * when the app is in a mobile viewport. Sets `body[data-mobile="true"]` so global CSS
 * hides the desktop sidebar/header and re-pads the main content area for the new chrome.
 *
 * Render alongside the existing desktop layout in `WorkspaceShell` — does not wrap children.
 */
export default function MobileShell({ title, isPageHeading, ibConnected, lastSync }: MobileShellProps) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const closeDrawer = useCallback(() => setDrawerOpen(false), []);
  const closeSearch = useCallback(() => setSearchOpen(false), []);
  const openSearch = useCallback(() => {
    // Commit the input before this click returns so mobile Safari can focus it
    // and present the keyboard within the user's opening gesture.
    flushSync(() => setSearchOpen(true));
    searchInputRef.current?.focus({ preventScroll: true });
  }, []);

  useEffect(() => {
    if (typeof document === "undefined") return;
    document.body.dataset.mobile = "true";
    return () => {
      delete document.body.dataset.mobile;
    };
  }, []);

  return (
    <div className={styles.mobileChrome}>
      <MobileAppBar
        title={title}
        isPageHeading={isPageHeading}
        ibConnected={ibConnected}
        onOpenSearch={openSearch}
        onOpenMore={() => setDrawerOpen(true)}
      />
      <MobileTabBar onOpenMore={() => setDrawerOpen(true)} />
      <MobileMoreDrawer
        open={drawerOpen}
        onClose={closeDrawer}
        ibConnected={ibConnected}
        lastSync={lastSync}
      />
      <MobileTickerSearch
        ref={searchInputRef}
        open={searchOpen}
        onClose={closeSearch}
      />
    </div>
  );
}

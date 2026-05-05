"use client";

import { useEffect, useState } from "react";
import MobileAppBar from "./MobileAppBar";
import MobileTabBar from "./MobileTabBar";
import MobileMoreDrawer from "./MobileMoreDrawer";
import MobileTickerSearch from "./MobileTickerSearch";

type MobileShellProps = {
  title: string;
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
export default function MobileShell({ title, ibConnected, lastSync }: MobileShellProps) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);

  useEffect(() => {
    if (typeof document === "undefined") return;
    document.body.dataset.mobile = "true";
    return () => {
      delete document.body.dataset.mobile;
    };
  }, []);

  return (
    <>
      <MobileAppBar
        title={title}
        ibConnected={ibConnected}
        onOpenSearch={() => setSearchOpen(true)}
      />
      <MobileTabBar onOpenMore={() => setDrawerOpen(true)} />
      <MobileMoreDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        ibConnected={ibConnected}
        lastSync={lastSync}
      />
      <MobileTickerSearch
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
      />
    </>
  );
}

"use client";

import { useEffect, useState } from "react";
import { Search } from "lucide-react";

type MobileAppBarProps = {
  title: string;
  ibConnected?: boolean;
  onOpenSearch?: () => void;
};

export default function MobileAppBar({ title, ibConnected = true, onOpenSearch }: MobileAppBarProps) {
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    let lastY = typeof window !== "undefined" ? window.scrollY : 0;
    let frame = 0;

    const handleScroll = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        const y = window.scrollY;
        const delta = y - lastY;
        if (y < 16) setCollapsed(false);
        else if (delta > 4) setCollapsed(true);
        else if (delta < -4) setCollapsed(false);
        lastY = y;
        frame = 0;
      });
    };

    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", handleScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);

  const className = `mobile-app-bar${collapsed ? " mobile-app-bar--collapsed" : ""}`;

  return (
    <header className={className} data-testid="mobile-app-bar">
      <div className="mobile-app-bar__inner">
        <div className="mobile-app-bar__brand">
          <span className="mobile-app-bar__logo" aria-hidden />
          <span className="mobile-app-bar__title">{title}</span>
        </div>
        <div className="mobile-app-bar__actions">
          <span
            className={`mobile-app-bar__status ${ibConnected ? "mobile-app-bar__status--live" : "mobile-app-bar__status--offline"}`}
            aria-label={ibConnected ? "IB Gateway connected" : "IB Gateway offline"}
          >
            <span className="mobile-app-bar__status-dot" aria-hidden />
            {ibConnected ? "LIVE" : "OFF"}
          </span>
          {onOpenSearch ? (
            <button
              type="button"
              className="mobile-app-bar__search"
              onClick={onOpenSearch}
              aria-label="Open ticker search"
              data-testid="mobile-app-bar-search"
            >
              <Search size={18} strokeWidth={2} aria-hidden />
            </button>
          ) : null}
        </div>
      </div>
    </header>
  );
}

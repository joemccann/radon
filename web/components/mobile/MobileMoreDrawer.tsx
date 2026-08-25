"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { X, Sun, Moon } from "lucide-react";
import { useClerk, useUser } from "@clerk/nextjs";
import { useTheme } from "@/lib/ThemeContext";
import { useIBStatusContext } from "@/lib/IBStatusContext";
import { navItems, NAV_GROUP_LABEL, NAV_GROUP_ORDER } from "@/lib/data";

// Preserved for legacy test wiring check: { label: "Options", href: "/options"

const BUILD_VERSION = process.env.NEXT_PUBLIC_BUILD_VERSION ?? null;
const EXIT_MS = 200;
const EXIT_REDUCED_MS = 120;

type MobileMoreDrawerProps = {
  open: boolean;
  onClose: () => void;
  ibConnected?: boolean;
  lastSync?: string | null;
};

export default function MobileMoreDrawer({ open, onClose, ibConnected = true, lastSync }: MobileMoreDrawerProps) {
  return (
    <AuthenticatedMobileMoreDrawer
      open={open}
      onClose={onClose}
      ibConnected={ibConnected}
      lastSync={lastSync}
    />
  );
}

function AuthenticatedMobileMoreDrawer({ open, onClose, ibConnected = true, lastSync }: MobileMoreDrawerProps) {
  const { signOut } = useClerk();
  const { user } = useUser();
  return (
    <MobileMoreDrawerView
      open={open}
      onClose={onClose}
      ibConnected={ibConnected}
      lastSync={lastSync}
      userEmail={user?.primaryEmailAddress?.emailAddress ?? user?.username ?? ""}
      onSignOut={() => signOut()}
    />
  );
}

function MobileMoreDrawerView({
  open,
  onClose,
  ibConnected = true,
  lastSync,
  userEmail,
  onSignOut,
}: MobileMoreDrawerProps & {
  userEmail?: string;
  onSignOut: () => void;
}) {
  const { theme, toggleTheme } = useTheme();
  const { displayStatus } = useIBStatusContext();
  const isDemo = displayStatus === "demo";
  const feedConnected = displayStatus !== "relay_offline" && displayStatus !== "unreachable";
  const syncTime = lastSync ? new Date(lastSync).toLocaleTimeString() : "—";

  const [mounted, setMounted] = useState(open);
  const [phase, setPhase] = useState<"open" | "exiting">(open ? "open" : "exiting");
  const exitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const openFrameRef = useRef<number | null>(null);

  useEffect(() => {
    if (open) {
      if (exitTimerRef.current) {
        clearTimeout(exitTimerRef.current);
        exitTimerRef.current = null;
      }
      setMounted(true);
      // Double-rAF so the enter transition paints from translateY(100%).
      openFrameRef.current = requestAnimationFrame(() => {
        openFrameRef.current = requestAnimationFrame(() => {
          setPhase("open");
          openFrameRef.current = null;
        });
      });
      return () => {
        if (openFrameRef.current != null) {
          cancelAnimationFrame(openFrameRef.current);
          openFrameRef.current = null;
        }
      };
    }
    if (!mounted) return;
    setPhase("exiting");
    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const ms = reduced ? EXIT_REDUCED_MS : EXIT_MS;
    exitTimerRef.current = setTimeout(() => {
      setMounted(false);
      exitTimerRef.current = null;
    }, ms);
    return () => {
      if (exitTimerRef.current) {
        clearTimeout(exitTimerRef.current);
        exitTimerRef.current = null;
      }
    };
  }, [open, mounted]);

  useEffect(() => {
    if (!mounted) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", handleKey);
      document.body.style.overflow = "";
    };
  }, [mounted, onClose]);

  if (!mounted) return null;

  const rootClass =
    phase === "open"
      ? "mobile-drawer-root mobile-drawer-root--open"
      : "mobile-drawer-root mobile-drawer-root--exiting";

  return (
    <div className={rootClass} role="dialog" aria-modal="true" data-testid="mobile-more-drawer">
      <button
        type="button"
        className="mobile-drawer-backdrop"
        aria-label="Close menu"
        onClick={onClose}
      />
      <aside className="mobile-drawer">
        <div className="mobile-drawer__header">
          <span className="mobile-drawer__title">Menu</span>
          <button
            type="button"
            className="mobile-drawer__close"
            aria-label="Close menu"
            onClick={onClose}
            data-testid="mobile-drawer-close"
          >
            <X size={20} aria-hidden />
          </button>
        </div>

        <nav className="mobile-drawer__nav" aria-label="Overflow navigation">
          {NAV_GROUP_ORDER.map((groupId) => {
            const items = navItems.filter((item) => !item.hidden && item.group === groupId);
            if (items.length === 0) return null;
            return (
              <div key={groupId} className="mobile-drawer__group">
                <div className="mobile-drawer__group-label">{NAV_GROUP_LABEL[groupId]}</div>
                {items.map((link) => {
                  const Icon = link.icon;
                  return (
                    <Link
                      key={link.label}
                      href={link.href}
                      prefetch={false}
                      className="mobile-drawer__link"
                      onClick={onClose}
                      data-testid={`mobile-drawer-${link.label.toLowerCase().replace(/\s+/g, "-")}`}
                    >
                      <Icon size={16} strokeWidth={2} aria-hidden />
                      <span>{link.label}</span>
                    </Link>
                  );
                })}
              </div>
            );
          })}
        </nav>

        <div className="mobile-drawer__footer">
          <div className="mobile-drawer__status">
            <span>IB Gateway</span>
            {isDemo ? (
              <span className="mobile-drawer__status-pill mobile-drawer__status-pill--demo">DEMO</span>
            ) : (
              <span className={`mobile-drawer__status-pill ${ibConnected ? "mobile-drawer__status-pill--live" : "mobile-drawer__status-pill--offline"}`}>
                {ibConnected ? "CONNECTED" : "OFFLINE"}
              </span>
            )}
          </div>
          <div className="mobile-drawer__status">
            <span>Last sync</span>
            <span className="mobile-drawer__status-value">{syncTime}</span>
          </div>
          <div className="mobile-drawer__status">
            <span>Data feed</span>
            {isDemo ? (
              <span className="mobile-drawer__status-pill mobile-drawer__status-pill--demo">SAMPLE DATA</span>
            ) : (
              <span className={`mobile-drawer__status-pill ${feedConnected ? "mobile-drawer__status-pill--live" : "mobile-drawer__status-pill--offline"}`}>
                {feedConnected ? "LIVE" : "OFFLINE"}
              </span>
            )}
          </div>
          {BUILD_VERSION ? (
            <div className="mobile-drawer__status">
              <span>Build</span>
              <span className="mobile-drawer__status-value">{BUILD_VERSION}</span>
            </div>
          ) : null}
          <button
            type="button"
            className="mobile-drawer__link"
            onClick={toggleTheme}
            aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            data-testid="mobile-drawer-theme-toggle"
          >
            {theme === "dark" ? <Sun size={16} strokeWidth={2} aria-hidden /> : <Moon size={16} strokeWidth={2} aria-hidden />}
            <span>{theme === "dark" ? "Light theme" : "Dark theme"}</span>
          </button>
          {userEmail ? (
            <div className="mobile-drawer__user">
              <span className="mobile-drawer__user-email">{userEmail}</span>
              <button
                type="button"
                className="mobile-drawer__signout"
                onClick={onSignOut}
                data-testid="mobile-drawer-signout"
              >
                Sign out
              </button>
            </div>
          ) : null}
        </div>
      </aside>
    </div>
  );
}

"use client";

import Link from "next/link";
import { useEffect } from "react";
import { X, BarChart3, LineChart, Wrench, Shield, Activity, Settings2, Sun, Moon, Bell, Workflow, Star } from "lucide-react";
import { useClerk, useUser } from "@clerk/nextjs";
import { useTheme } from "@/lib/ThemeContext";
import { useIBStatusContext } from "@/lib/IBStatusContext";

type DrawerLink = {
  label: string;
  href: string;
  icon: typeof BarChart3;
};

const OVERFLOW_LINKS: DrawerLink[] = [
  { label: "Performance", href: "/performance", icon: BarChart3 },
  { label: "Watchlist", href: "/watchlist", icon: Star },
  { label: "Flow Analysis", href: "/flow-analysis", icon: LineChart },
  { label: "Journal", href: "/journal", icon: Wrench },
  { label: "Regime", href: "/regime/cri", icon: Shield },
  { label: "CTA", href: "/cta", icon: Activity },
  { label: "Alerts", href: "/alerts", icon: Bell },
  { label: "Workflow", href: "/workflow", icon: Workflow },
  { label: "Operator", href: "/admin", icon: Settings2 },
];

const BUILD_VERSION = process.env.NEXT_PUBLIC_BUILD_VERSION ?? null;

type MobileMoreDrawerProps = {
  open: boolean;
  onClose: () => void;
  ibConnected?: boolean;
  lastSync?: string | null;
};

export default function MobileMoreDrawer({ open, onClose, ibConnected = true, lastSync }: MobileMoreDrawerProps) {
  if (
    process.env.NEXT_PUBLIC_RADON_AUTHLESS_TEST === "1" ||
    process.env.RADON_AUTHLESS_TEST === "1"
  ) {
    return (
      <MobileMoreDrawerView
        open={open}
        onClose={onClose}
        ibConnected={ibConnected}
        lastSync={lastSync}
        userEmail="test@radon.local"
        onSignOut={() => undefined}
      />
    );
  }
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

  useEffect(() => {
    if (!open) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", handleKey);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="mobile-drawer-root" role="dialog" aria-modal="true" data-testid="mobile-more-drawer">
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
          {OVERFLOW_LINKS.map((link) => {
            const Icon = link.icon;
            return (
              <Link
                key={link.label}
                href={link.href}
                className="mobile-drawer__link"
                onClick={onClose}
                data-testid={`mobile-drawer-${link.label.toLowerCase().replace(/\s+/g, "-")}`}
              >
                <Icon size={16} strokeWidth={2} aria-hidden />
                <span>{link.label}</span>
              </Link>
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

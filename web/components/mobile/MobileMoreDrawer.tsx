"use client";

import Link from "next/link";
import { useEffect } from "react";
import { X, BarChart3, Search, LineChart, Wrench, Shield, Activity } from "lucide-react";
import { useClerk, useUser } from "@clerk/nextjs";

type DrawerLink = {
  label: string;
  href: string;
  icon: typeof BarChart3;
};

const OVERFLOW_LINKS: DrawerLink[] = [
  { label: "Performance", href: "/performance", icon: BarChart3 },
  { label: "Discover", href: "/discover", icon: Search },
  { label: "Flow Analysis", href: "/flow-analysis", icon: LineChart },
  { label: "Journal", href: "/journal", icon: Wrench },
  { label: "Regime", href: "/regime/cri", icon: Shield },
  { label: "CTA", href: "/cta", icon: Activity },
];

type MobileMoreDrawerProps = {
  open: boolean;
  onClose: () => void;
  ibConnected?: boolean;
  lastSync?: string | null;
};

export default function MobileMoreDrawer({ open, onClose, ibConnected = true, lastSync }: MobileMoreDrawerProps) {
  const { signOut } = useClerk();
  const { user } = useUser();
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
            <span className={`mobile-drawer__status-pill ${ibConnected ? "mobile-drawer__status-pill--live" : "mobile-drawer__status-pill--offline"}`}>
              {ibConnected ? "CONNECTED" : "OFFLINE"}
            </span>
          </div>
          <div className="mobile-drawer__status">
            <span>Last sync</span>
            <span className="mobile-drawer__status-value">{syncTime}</span>
          </div>
          {user ? (
            <div className="mobile-drawer__user">
              <span className="mobile-drawer__user-email">{user.primaryEmailAddress?.emailAddress ?? user.username ?? ""}</span>
              <button
                type="button"
                className="mobile-drawer__signout"
                onClick={() => signOut()}
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

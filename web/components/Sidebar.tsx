"use client";

import Link from "next/link";
import type { WorkspaceSection } from "@/lib/types";
import { navItems } from "@/lib/data";
import { useProfile } from "@/lib/useProfile";
import { useIBStatusContext, type IBDisplayStatus } from "@/lib/IBStatusContext";

type SidebarProps = {
  activeSection: WorkspaceSection;
  actionTone: string;
  /** @deprecated kept for callers that haven't migrated. The authoritative
   *  status now comes from useIBStatusContext().displayStatus inside this
   *  component — see the IB Gateway 2FA contradictions fix. */
  ibConnected?: boolean;
  lastSync?: string | null;
};

/** Footer label per derived display status. Keep tight — overflowing the
 *  sidebar reflows the status row. */
function statusLabel(
  status: IBDisplayStatus,
): { text: string; cls: "live" | "warn" | "dead" | "demo" } {
  switch (status) {
    case "connected":
      return { text: "NOMINAL", cls: "live" };
    case "awaiting_2fa":
      return { text: "AWAITING 2FA", cls: "warn" };
    case "unhealthy":
      return { text: "DEGRADED", cls: "warn" };
    case "unreachable":
      return { text: "UNREACHABLE", cls: "dead" };
    case "ib_offline":
      return { text: "OFFLINE", cls: "dead" };
    case "relay_offline":
      return { text: "RELAY OFFLINE", cls: "dead" };
    case "demo":
      return { text: "DEMO", cls: "demo" };
  }
}

function monogramFor(name: string | null, email: string | null): string {
  const source = (name ?? email ?? "").trim();
  if (!source) return "·";
  const parts = source.replace(/@.*$/, "").split(/[\s._-]+/).filter(Boolean);
  if (parts.length === 0) return source.slice(0, 2).toUpperCase();
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

export default function Sidebar({ activeSection, actionTone, lastSync }: SidebarProps) {
  const { displayStatus } = useIBStatusContext();
  const { text, cls } = statusLabel(displayStatus);
  const dotClass =
    cls === "live"
      ? "status-dot-live"
      : cls === "warn"
        ? "status-dot-warn"
        : cls === "demo"
          ? "status-dot-demo"
          : "status-dot-dead";
  const syncTime = lastSync ? new Date(lastSync).toLocaleTimeString() : "—";

  // Sidebar identity comes from our own profile store (fetch-based, no Clerk
  // context needed here). The account email + Clerk avatar fallback live on
  // the Profile page, which always renders within ClerkProvider.
  const { profile } = useProfile();
  const displayName = profile?.username ?? "Profile";
  const avatarUrl = profile?.avatar_url ?? null;
  const monogram = monogramFor(profile?.username ?? null, null);
  const profileActive = activeSection === "profile";

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <img
          src="/brand/radon-monogram.svg"
          alt=""
          width={22}
          height={22}
          className="logo-mark"
          aria-hidden
        />
        <span className="logo-text">
          Radon
          <span className="logo-text-sub">terminal</span>
        </span>
      </div>

      <nav className="sidebar-nav">
        {navItems.filter((item) => !item.hidden).map((item) => {
          const Icon = item.icon;
          return (
            <Link
              key={item.label}
              href={item.href}
              className={item.route === activeSection ? "nav-item active" : "nav-item"}
            >
              <span className="nav-icon">
                <Icon size={14} color={actionTone} strokeWidth={2} />
              </span>
              {item.label}
            </Link>
          );
        })}
      </nav>

      <Link
        href="/profile"
        className={`sidebar-user-card${profileActive ? " sidebar-user-card--active" : ""}`}
        aria-current={profileActive ? "page" : undefined}
      >
        <span className="sidebar-user-card__avatar">
          {avatarUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={avatarUrl} alt="" width={28} height={28} />
          ) : (
            <span className="sidebar-user-card__monogram">{monogram}</span>
          )}
        </span>
        <span className="sidebar-user-card__text">
          <span className="sidebar-user-card__name">{displayName}</span>
          <span className="sidebar-user-card__email">View profile</span>
        </span>
      </Link>

      <div className="sidebar-footer">
        <div className="status-row">
          <span>Uplink</span>
          <span className="status-dot-wrap">
            <span className={`status-dot ${dotClass}`} />
            {text}
          </span>
        </div>
        <div className="status-row">
          <span>Last Sample</span>
          <span>{syncTime}</span>
        </div>
        <div className="status-row">
          <span>Source</span>
          <span>IB · UW</span>
        </div>
      </div>
    </aside>
  );
}

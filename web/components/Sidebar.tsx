"use client";

import Link from "next/link";
import { useState, useCallback } from "react";
import type { WorkspaceSection, NavGroupId } from "@/lib/types";
import { navItems, NAV_GROUP_LABEL, NAV_GROUP_ORDER } from "@/lib/data";
import { useProfile } from "@/lib/useProfile";

type SidebarProps = {
  activeSection: WorkspaceSection;
  actionTone: string;
  /** @deprecated kept for callers that haven't migrated. */
  ibConnected?: boolean;
  lastSync?: string | null;
};

function monogramFor(name: string | null, email: string | null): string {
  const source = (name ?? email ?? "").trim();
  if (!source) return "·";
  const parts = source.replace(/@.*$/, "").split(/[\s._-]+/).filter(Boolean);
  if (parts.length === 0) return source.slice(0, 2).toUpperCase();
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

export default function Sidebar({ activeSection, actionTone }: SidebarProps) {
  const [collapsedGroups, setCollapsedGroups] = useState<Set<NavGroupId>>(() => new Set<NavGroupId>(["operations"]));
  const toggleGroup = useCallback((group: NavGroupId) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });
  }, []);

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

      <nav className="sidebar-nav" aria-label="Primary navigation">
        {NAV_GROUP_ORDER.map((groupId) => {
          const items = navItems.filter((item) => !item.hidden && item.group === groupId);
          if (items.length === 0) return null;
          const collapsed = collapsedGroups.has(groupId);
          const isActiveGroup = items.some((it) => it.route === activeSection);
          return (
            <div key={groupId} className={`nav-group${collapsed ? " nav-group--collapsed" : ""}${isActiveGroup ? " nav-group--active" : ""}`}>
              <button
                type="button"
                className="nav-group-label"
                onClick={() => toggleGroup(groupId)}
                aria-expanded={!collapsed}
                aria-label={`${NAV_GROUP_LABEL[groupId]} group`}
              >
                <span className="nav-group-label-text">{NAV_GROUP_LABEL[groupId]}</span>
                <span className={`nav-group-chevron${collapsed ? "" : " nav-group-chevron--open"}`} aria-hidden>
                  <svg width="8" height="8" viewBox="0 0 8 8" fill="none">
                    <path d="M1.5 2.5L4 5L6.5 2.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="square" strokeLinejoin="miter" />
                  </svg>
                </span>
              </button>
              {!collapsed && (
                <div className="nav-group-items">
                  {items.map((item) => {
                    const Icon = item.icon;
                    return (
                      <Link
                        key={item.label}
                        href={item.href}
                        className={item.route === activeSection ? "nav-item active" : "nav-item"}
                        aria-current={item.route === activeSection ? "page" : undefined}
                      >
                        <span className="nav-icon">
                          <Icon size={14} color={actionTone} strokeWidth={2} />
                        </span>
                        {item.label}
                      </Link>
                    );
                  })}
                </div>
              )}
            </div>
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
    </aside>
  );
}

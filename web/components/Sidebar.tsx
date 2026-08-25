"use client";

import Link from "next/link";
import { useState, useCallback, useEffect } from "react";
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

export const SIDEBAR_COLLAPSED_STORAGE_KEY = "radon:sidebar-collapsed";

function monogramFor(name: string | null, email: string | null): string {
  const source = (name ?? email ?? "").trim();
  if (!source) return "·";
  const parts = source.replace(/@.*$/, "").split(/[\s._-]+/).filter(Boolean);
  if (parts.length === 0) return source.slice(0, 2).toUpperCase();
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

function readSavedCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function saveCollapsed(collapsed: boolean) {
  try {
    window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, collapsed ? "1" : "0");
  } catch {}
}

// SSR always renders expanded; the saved preference is applied post-mount
// (same hydration contract as ThemeContext). `settled` gates the width
// transition so restoring a collapsed rail on load snaps instead of sliding.
function useCollapsedPreference() {
  const [collapsed, setCollapsed] = useState(false);
  const [settled, setSettled] = useState(false);
  useEffect(() => {
    setCollapsed(readSavedCollapsed());
    const frame = window.requestAnimationFrame(() => setSettled(true));
    return () => window.cancelAnimationFrame(frame);
  }, []);
  const toggle = useCallback(() => {
    setCollapsed((prev) => {
      saveCollapsed(!prev);
      return !prev;
    });
  }, []);
  return { collapsed, settled, toggle };
}

function CollapseGlyph({ collapsed }: { collapsed: boolean }) {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden className={`sidebar-collapse-glyph${collapsed ? " sidebar-collapse-glyph--collapsed" : ""}`}>
      <rect x="1.5" y="2.5" width="11" height="9" stroke="currentColor" strokeWidth="1.2" />
      <path d="M5 2.5V11.5" stroke="currentColor" strokeWidth="1.2" />
      <path className="sidebar-collapse-glyph__arrow" d="M10.5 5.5L9 7L10.5 8.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="square" />
    </svg>
  );
}

export default function Sidebar({ activeSection, actionTone }: SidebarProps) {
  const { collapsed, settled, toggle: toggleCollapsed } = useCollapsedPreference();
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

  const sidebarClass = `sidebar${collapsed ? " sidebar--collapsed" : ""}${settled ? " sidebar--settled" : ""}`;

  return (
    <aside className={sidebarClass} data-collapsed={collapsed ? "true" : "false"}>
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
          // The rail has no group headers to fold, so every group shows its items.
          const groupFolded = !collapsed && collapsedGroups.has(groupId);
          const isActiveGroup = items.some((it) => it.route === activeSection);
          return (
            <div key={groupId} className={`nav-group${groupFolded ? " nav-group--collapsed" : ""}${isActiveGroup ? " nav-group--active" : ""}`}>
              <button
                type="button"
                className="nav-group-label"
                onClick={() => toggleGroup(groupId)}
                aria-expanded={!groupFolded}
                aria-label={`${NAV_GROUP_LABEL[groupId]} group`}
                tabIndex={collapsed ? -1 : undefined}
                aria-hidden={collapsed || undefined}
              >
                <span className="nav-group-label-text">{NAV_GROUP_LABEL[groupId]}</span>
                <span className={`nav-group-chevron${groupFolded ? "" : " nav-group-chevron--open"}`} aria-hidden>
                  <svg width="8" height="8" viewBox="0 0 8 8" fill="none">
                    <path d="M1.5 2.5L4 5L6.5 2.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="square" strokeLinejoin="miter" />
                  </svg>
                </span>
              </button>
              {!groupFolded && (
                <div className="nav-group-items">
                  {items.map((item) => {
                    const Icon = item.icon;
                    return (
                      <Link
                        key={item.label}
                        href={item.href}
                        prefetch={false}
                        className={item.route === activeSection ? "nav-item active" : "nav-item"}
                        aria-current={item.route === activeSection ? "page" : undefined}
                        aria-label={collapsed ? item.label : undefined}
                        data-label={item.label}
                      >
                        <span className="nav-icon">
                          <Icon size={14} color={actionTone} strokeWidth={2} />
                        </span>
                        <span className="nav-item-label">{item.label}</span>
                      </Link>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </nav>

      <button
        type="button"
        className="sidebar-collapse-toggle"
        onClick={toggleCollapsed}
        aria-expanded={!collapsed}
        aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
        data-label={collapsed ? "Expand" : "Collapse"}
      >
        <span className="nav-icon">
          <CollapseGlyph collapsed={collapsed} />
        </span>
        <span className="nav-item-label">Collapse</span>
      </button>

      <Link
        href="/profile"
        prefetch={false}
        className={`sidebar-user-card${profileActive ? " sidebar-user-card--active" : ""}`}
        aria-current={profileActive ? "page" : undefined}
        aria-label={collapsed ? "Profile" : undefined}
        data-label={displayName}
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

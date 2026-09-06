"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import type { WorkspaceSection } from "@/lib/types";
import { clearPrimaryNavigation, navItems, NAV_GROUP_LABEL, NAV_GROUP_ORDER } from "@/lib/data";
import { useProfile } from "@/lib/useProfile";
import styles from "./ClearShell.module.css";
import ClearBrandMark from "./ClearBrandMark";

type SidebarProps = {
  activeSection: WorkspaceSection;
  /** Kept for existing shell callers; icons inherit semantic text color. */
  actionTone: string;
  ibConnected?: boolean;
  lastSync?: string | null;
};

function monogramFor(name: string | null): string {
  const parts = (name ?? "").trim().split(/[\s._-]+/).filter(Boolean);
  if (!parts.length) return "R";
  return parts.length === 1 ? parts[0].slice(0, 2).toUpperCase() : `${parts[0][0]}${parts[1][0]}`.toUpperCase();
}

/** Clear's horizontal navigation replaces the old permanent sidebar rail. */
export default function Sidebar({ activeSection }: SidebarProps) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const { profile } = useProfile();
  const displayName = profile?.username ?? "Profile";

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (event.target instanceof Node && !menuRef.current?.contains(event.target)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return (
    <div className={styles.navigation} data-testid="clear-navigation">
      <Link href="/dashboard" prefetch={false} className={styles.brand} aria-label="Radon home">
        <ClearBrandMark />
        <span>radon</span>
      </Link>
      <nav className={styles.primary} aria-label="Primary navigation">
        {clearPrimaryNavigation.map((item) => (
          <Link key={item.label} href={item.href} prefetch={false} className={styles.primaryLink}
            aria-current={item.sections.includes(activeSection) ? "page" : undefined}>
            {item.label}
          </Link>
        ))}
      </nav>
      <div ref={menuRef} className={styles.more}>
        <button ref={triggerRef} type="button" className={styles.moreTrigger}
          onClick={() => setOpen((previous) => !previous)} aria-expanded={open}
          aria-controls="clear-workspace-navigation" aria-label="Open all workspaces">
          More <ChevronDown size={14} aria-hidden />
        </button>
        {open ? (
          <nav id="clear-workspace-navigation" className={styles.menu} aria-label="All workspaces">
            {NAV_GROUP_ORDER.map((groupId) => (
              <div key={groupId} className={styles.menuGroup}>
                <span className={styles.menuHeading}>{NAV_GROUP_LABEL[groupId]}</span>
                {navItems.filter((item) => item.group === groupId).map((item) => {
                  const Icon = item.icon;
                  return (
                    <Link key={item.route} href={item.href} prefetch={false} className={styles.menuLink}
                      aria-current={item.route === activeSection ? "page" : undefined} onClick={() => setOpen(false)}>
                      <Icon size={17} aria-hidden />{item.label}
                    </Link>
                  );
                })}
              </div>
            ))}
          </nav>
        ) : null}
      </div>
      <Link href="/profile" prefetch={false} className={styles.avatar} aria-label="Profile"
        title={displayName} aria-current={activeSection === "profile" ? "page" : undefined}>
        {profile?.avatar_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={profile.avatar_url} alt="" width={32} height={32} />
        ) : monogramFor(profile?.username ?? null)}
      </Link>
    </div>
  );
}

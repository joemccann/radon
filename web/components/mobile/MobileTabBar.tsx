"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, Circle, ClipboardList, Sparkles, Menu } from "lucide-react";
import type { ComponentType, SVGProps } from "react";

type MobileTab = {
  label: string;
  href?: string;
  icon: ComponentType<SVGProps<SVGSVGElement> & { size?: number; strokeWidth?: number }>;
  matchPaths?: string[];
  action?: "openMore";
};

const TABS: MobileTab[] = [
  { label: "Dashboard", href: "/dashboard", icon: LayoutDashboard, matchPaths: ["/dashboard", "/"] },
  { label: "Positions", href: "/portfolio", icon: Circle, matchPaths: ["/portfolio", "/performance"] },
  { label: "Orders", href: "/orders", icon: ClipboardList, matchPaths: ["/orders"] },
  { label: "Scanner", href: "/scanner", icon: Sparkles, matchPaths: ["/scanner", "/discover", "/flow-analysis"] },
  { label: "More", icon: Menu, action: "openMore" },
];

type MobileTabBarProps = {
  onOpenMore: () => void;
};

function isActive(pathname: string, paths: string[] | undefined): boolean {
  if (!paths) return false;
  return paths.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

export default function MobileTabBar({ onOpenMore }: MobileTabBarProps) {
  const pathname = usePathname() ?? "";

  return (
    <nav className="mobile-tab-bar" aria-label="Primary mobile navigation" data-testid="mobile-tab-bar">
      {TABS.map((tab) => {
        const Icon = tab.icon;
        const active = isActive(pathname, tab.matchPaths);
        const className = `mobile-tab-bar__item${active ? " mobile-tab-bar__item--active" : ""}`;

        if (tab.action === "openMore") {
          return (
            <button
              key={tab.label}
              type="button"
              className={className}
              onClick={onOpenMore}
              aria-label="Open more navigation"
              data-testid="mobile-tab-more"
            >
              <Icon size={20} strokeWidth={2} aria-hidden />
              <span className="mobile-tab-bar__label">{tab.label}</span>
            </button>
          );
        }

        return (
          <Link
            key={tab.label}
            href={tab.href ?? "#"}
            className={className}
            data-testid={`mobile-tab-${tab.label.toLowerCase()}`}
          >
            <Icon size={20} strokeWidth={2} aria-hidden />
            <span className="mobile-tab-bar__label">{tab.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}

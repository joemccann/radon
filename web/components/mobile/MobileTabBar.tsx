"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChartNoAxesCombined, Circle, ScanSearch, Shield } from "lucide-react";
import type { ComponentType, SVGProps } from "react";

type MobileTab = {
  label: string;
  href?: string;
  icon: ComponentType<SVGProps<SVGSVGElement> & { size?: number; strokeWidth?: number }>;
  matchPaths?: string[];
  action?: "openMore";
};

const TABS: MobileTab[] = [
  { label: "Portfolio", href: "/dashboard", icon: ChartNoAxesCombined, matchPaths: ["/dashboard", "/performance", "/"] },
  { label: "Research", href: "/scanner", icon: ScanSearch, matchPaths: ["/scanner", "/discover", "/flow-analysis", "/options", "/watchlist"] },
  { label: "Risk", href: "/regime/cri", icon: Shield, matchPaths: ["/regime", "/cta"] },
  { label: "Positions", href: "/portfolio", icon: Circle, matchPaths: ["/portfolio", "/orders"] },
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
            prefetch={false}
            className={className}
            aria-current={active ? "page" : undefined}
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

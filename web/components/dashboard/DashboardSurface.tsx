"use client";

import { type ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useViewport } from "@/lib/useViewport";
import { useDashboardSectionVisibility } from "@/lib/useDashboardSectionVisibility";
import DashboardNewsFeed from "@/components/DashboardNewsFeed";
import { PortfolioSnapshotCard } from "./PortfolioSnapshotCard";
import { OrdersSnapshotCard } from "./OrdersSnapshotCard";
import { OpportunitiesCard } from "./OpportunitiesCard";
import { FlowSurpriseCard } from "./FlowSurpriseCard";
import { CatalystCard } from "./CatalystCard";
import type { OrdersData, PortfolioData } from "@/lib/types";

type DashboardSurfaceProps = {
  portfolio: PortfolioData | null;
  orders: OrdersData | null;
  realizedPnl?: number;
};

function DashboardSection({
  id,
  label,
  count,
  open,
  onToggle,
  children,
}: {
  id: string;
  label: string;
  count?: string;
  open: boolean;
  onToggle: (id: string) => void;
  children: ReactNode;
}) {
  const { isMobile, hasMounted } = useViewport();
  const mobile = isMobile && hasMounted;
  // Single mount label: when open the card owns eyebrow/title; outer is
  // collapse-only. When collapsed (or mobile), keep a short device label so
  // slots do not reduce to orphaned counts (T2 / T5).
  const showDeviceLabel = !open || mobile;
  const collapseOnly = open && !mobile;

  return (
    <section className={`dashboard-section dashboard-section--${id}`} data-testid={`dashboard-section-${id}`}>
      <h2 className="dashboard-section__heading" aria-label={label}>
        <button
          type="button"
          className={`dashboard-section__toggle${mobile ? " tap-target" : ""}${collapseOnly ? " dashboard-section__toggle--collapse-only" : ""}`}
          aria-expanded={open}
          aria-controls={`dashboard-section-body-${id}`}
          aria-label={label}
          onClick={() => onToggle(id)}
        >
          {showDeviceLabel ? (
            <span className={mobile ? "dashboard-section__label" : "dashboard-section__title"}>
              {label}
            </span>
          ) : null}
          <span className="dashboard-section__meta">
            {count ? <span>{count}</span> : null}
            {open ? <ChevronDown size={16} aria-hidden /> : <ChevronRight size={16} aria-hidden />}
          </span>
        </button>
      </h2>
      <div
        id={`dashboard-section-body-${id}`}
        className="dashboard-section__body"
        hidden={!open}
      >
        {children}
      </div>
    </section>
  );
}

/**
 * DashboardSurface — actionable trading dashboard. Two equal columns:
 *
 *   LEFT (50%) — what the trader needs to act on right now:
 *     - Portfolio snapshot (Net Liq / Today P&L / Open Risk / Cash)
 *     - Open orders + today's fills (compressed; click-through to /orders)
 *     - Trading opportunities (scanner + discover tabs; click to ticker)
 *
 *   RIGHT (50%) — live market intel (the surface the trader actually reads):
 *     - DashboardNewsFeed (full width, with tag filter + image lightbox)
 *
 * Regime-focused panels (CRI / VCG / Markov state lattice / Spectral
 * decomposition / Flow projection) live on /regime sub-tabs, not here.
 * The MarkovStateGraph / FlowProjectionTrace / SpectralBars primitives
 * remain available via `components/instruments/` for re-use there.
 */
export default function DashboardSurface({
  portfolio,
  orders,
  realizedPnl = 0,
}: DashboardSurfaceProps) {
  const { isHidden, toggle } = useDashboardSectionVisibility();

  return (
    <div className="dashboard-surface">
      <div className="dashboard-surface__main">
        <DashboardSection id="portfolio" label="Portfolio" count="01" open={!isHidden("portfolio")} onToggle={toggle}>
          <PortfolioSnapshotCard portfolio={portfolio} realizedPnl={realizedPnl} />
        </DashboardSection>
        <DashboardSection id="orders" label="Working & Filled" count="03" open={!isHidden("orders")} onToggle={toggle}>
          <OrdersSnapshotCard orders={orders} />
        </DashboardSection>
        <DashboardSection id="opportunities" label="Trading Candidates" count="04" open={!isHidden("opportunities")} onToggle={toggle}>
          <OpportunitiesCard />
        </DashboardSection>
        <DashboardSection id="flow-surprise" label="Flow Surprise" count="05" open={!isHidden("flow-surprise")} onToggle={toggle}>
          <FlowSurpriseCard />
        </DashboardSection>
        <DashboardSection id="catalysts" label="Upcoming Catalysts" count="06" open={!isHidden("catalysts")} onToggle={toggle}>
          <CatalystCard />
        </DashboardSection>
      </div>
      <aside className="dashboard-surface__rail" aria-label="Newsfeed">
        <DashboardSection id="news" label="Live Market Feed" count="02" open={!isHidden("news")} onToggle={toggle}>
          <DashboardNewsFeed />
        </DashboardSection>
      </aside>
    </div>
  );
}

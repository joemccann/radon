"use client";

import { useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useViewport } from "@/lib/useViewport";
import DashboardNewsFeed from "@/components/DashboardNewsFeed";
import { PortfolioSnapshotCard } from "./PortfolioSnapshotCard";
import { OrdersSnapshotCard } from "./OrdersSnapshotCard";
import { OpportunitiesCard } from "./OpportunitiesCard";
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
  children,
}: {
  id: string;
  label: string;
  count?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(true);
  const { isMobile, hasMounted } = useViewport();
  const mobile = isMobile && hasMounted;

  return (
    <section className={`dashboard-section dashboard-section--${id}`} data-testid={`dashboard-section-${id}`}>
      <button
        type="button"
        // On mobile: give the toggle a full 44px tap target; suppress the label
        // overhead so only the chevron + count remain as a compact collapse affordance.
        className={`dashboard-section__toggle${mobile ? " tap-target" : ""}`}
        aria-expanded={open}
        aria-controls={`dashboard-section-body-${id}`}
        aria-label={mobile ? label : undefined}
        onClick={() => setOpen((prev) => !prev)}
      >
        {!mobile && <span className="dashboard-section__title">{label}</span>}
        <span className="dashboard-section__meta">
          {count ? <span>{count}</span> : null}
          {open ? <ChevronDown size={16} aria-hidden /> : <ChevronRight size={16} aria-hidden />}
        </span>
      </button>
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
  return (
    <div className="dashboard-surface">
      <div className="dashboard-surface__main">
        <DashboardSection id="portfolio" label="Portfolio" count="01">
        <PortfolioSnapshotCard portfolio={portfolio} realizedPnl={realizedPnl} />
        </DashboardSection>
        <DashboardSection id="orders" label="Working & Filled" count="03">
          <OrdersSnapshotCard orders={orders} />
        </DashboardSection>
        <DashboardSection id="opportunities" label="Trading Candidates" count="04">
          <OpportunitiesCard />
        </DashboardSection>
      </div>
      <aside className="dashboard-surface__rail" aria-label="Newsfeed">
        <DashboardSection id="news" label="Live Market Feed" count="02">
          <DashboardNewsFeed />
        </DashboardSection>
      </aside>
    </div>
  );
}

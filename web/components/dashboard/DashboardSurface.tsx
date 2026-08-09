"use client";

import { useMemo, type ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useViewport } from "@/lib/useViewport";
import { useDashboardSectionVisibility } from "@/lib/useDashboardSectionVisibility";
import DashboardNewsFeed from "@/components/DashboardNewsFeed";
import { KpiStrip } from "./KpiStrip";
import ScannerHero from "./ScannerHero";
import CatalystsQuadrant from "./CatalystsQuadrant";
import EngineStatePanel from "./EngineStatePanel";
import type { PortfolioData } from "@/lib/types";
import type { MarketState } from "@/lib/useMarketHours";

type DashboardSurfaceProps = {
  portfolio: PortfolioData | null;
  realizedPnl?: number;
  marketState: MarketState | null;
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
 * DashboardSurface — terminal dashboard. A full-width KPI telemetry strip,
 * then a two-column grid:
 *
 *   LEFT — Feed / 01: the paginated DashboardNewsFeed rail (tag bar,
 *     lightbox, bookmarks) inside the height-clamped scrollable
 *     .dashboard-surface__feed container.
 *
 *   RIGHT — Signals / 02: theta-harvester / 7-step-strength ranked tables,
 *     then the quadrant row: Catalysts / 03 grouped TODAY / THIS WEEK /
 *     POSITIONS, and Structure / 04 engine state (CRI · MARKOV · VCG · GEX).
 *
 * Orders live on /orders; flow surprise and the wider scanner matrix live on
 * their own surfaces.
 */
export default function DashboardSurface({
  portfolio,
  realizedPnl = 0,
  marketState,
}: DashboardSurfaceProps) {
  const { isHidden, toggle } = useDashboardSectionVisibility();

  const positionTickers = useMemo(
    () => new Set((portfolio?.positions ?? []).map((p) => p.ticker).filter(Boolean)),
    [portfolio],
  );

  return (
    <div className="dashboard-surface">
      <KpiStrip portfolio={portfolio} realizedPnl={realizedPnl} />
      <div className="dashboard-surface__grid">
        <div className="dashboard-surface__feed">
          <DashboardSection id="feed" label="Live Market Feed" count="01" open={!isHidden("feed")} onToggle={toggle}>
            <DashboardNewsFeed />
          </DashboardSection>
        </div>
        <div className="dashboard-surface__right">
          <DashboardSection id="signals" label="Top Candidates" count="02" open={!isHidden("signals")} onToggle={toggle}>
            <ScannerHero />
          </DashboardSection>
          <div className="dashboard-quadrants">
            <DashboardSection id="catalysts" label="Upcoming Catalysts" count="03" open={!isHidden("catalysts")} onToggle={toggle}>
              <CatalystsQuadrant positionTickers={positionTickers} />
            </DashboardSection>
            <DashboardSection id="engine" label="Engine State" count="04" open={!isHidden("engine")} onToggle={toggle}>
              <EngineStatePanel marketState={marketState} />
            </DashboardSection>
          </div>
        </div>
      </div>
    </div>
  );
}

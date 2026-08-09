"use client";

import { useMemo } from "react";

import { useCatalysts, type CatalystRow } from "@/lib/useCatalysts";
import { catalystKindLabel, catalystWhenLabel, groupCatalysts } from "@/lib/catalystGroups";

type Props = {
  positionTickers: ReadonlySet<string>;
};

function formatUpdated(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function GroupRows({ rows }: { rows: CatalystRow[] }) {
  return (
    <>
      {rows.map((row) => (
        <div key={`${row.type}:${row.title}:${row.date}`} className="catalyst-group__row">
          <span className="catalyst-group__kind">{catalystKindLabel(row.type)}</span>
          <span className="catalyst-group__name" title={row.title}>
            {row.ticker ? `${row.ticker} · ` : ""}
            {row.title}
          </span>
          <span className="catalyst-group__when">{catalystWhenLabel(row)}</span>
        </div>
      ))}
    </>
  );
}

/**
 * CatalystsQuadrant — upcoming catalysts grouped into TODAY / THIS WEEK /
 * POSITIONS (rows whose ticker is currently held). Rows and distances are
 * recomputed at render time; the stored days_until is advisory only.
 */
export default function CatalystsQuadrant({ positionTickers }: Props) {
  const { data, isLoading, error } = useCatalysts(true);

  const groups = useMemo(
    () => groupCatalysts(data?.catalysts ?? [], positionTickers),
    [data, positionTickers],
  );

  const upcomingCount = groups.today.length + groups.thisWeek.length + groups.positions.length;

  const sections: Array<{ label: string; tone: "warn" | "neutral"; rows: CatalystRow[] }> = [
    { label: "TODAY", tone: "warn", rows: groups.today },
    { label: "THIS WEEK", tone: "neutral", rows: groups.thisWeek },
    { label: "POSITIONS", tone: "neutral", rows: groups.positions },
  ];

  return (
    <section className="catalyst-quadrant snapshot-card">
      <span className="panel-edge-trace" aria-hidden />
      <header className="catalyst-quadrant__header">
        <div>
          <p className="panel-eyebrow">Catalysts / 03</p>
          <h3 className="panel-title">Upcoming</h3>
        </div>
      </header>

      {isLoading ? (
        <div className="news-feed-empty">Loading catalysts…</div>
      ) : error ? (
        <div className="news-feed-error">{error}</div>
      ) : upcomingCount === 0 ? (
        <div className="news-feed-empty">No upcoming catalysts in the current snapshot.</div>
      ) : (
        sections.map((section) =>
          section.rows.length === 0 ? null : (
            <div key={section.label} className="catalyst-group">
              <div className="catalyst-group__head">
                <span className={`catalyst-group__label catalyst-group__label--${section.tone}`}>
                  {section.label}
                </span>
                <span className="catalyst-group__rule" aria-hidden />
                <span className="catalyst-group__count">{section.rows.length}</span>
              </div>
              <GroupRows rows={section.rows} />
            </div>
          ),
        )
      )}

      <footer className="panel-meta-rail" aria-label="Catalyst calibration">
        <div className="panel-meta-rail-item">
          <span className="k">updated</span>
          <span className="v">{formatUpdated(data?.scan_time)}</span>
        </div>
        <div className="panel-meta-rail-item">
          <span className="k">events</span>
          <span className="v">{upcomingCount}</span>
        </div>
      </footer>
    </section>
  );
}

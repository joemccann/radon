"use client";

import { useCallback, useMemo, useState } from "react";

import { useCatalysts } from "@/lib/useCatalysts";
import {
  catalystWhenLabel,
  groupCatalystsByCategory,
  type CatalystCategory,
  type CategorizedCatalystRow,
} from "@/lib/catalystGroups";

type Props = {
  positionTickers: ReadonlySet<string>;
};

/** Rows shown before a category has to be expanded in full. */
const PREVIEW_ROWS = 6;

function formatUpdated(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function rowKey(row: CategorizedCatalystRow): string {
  return `${row.type}:${row.ticker ?? ""}:${row.title}:${row.date}`;
}

function CatalystRowLine({
  row,
  showFlag,
}: {
  row: CategorizedCatalystRow;
  showFlag: boolean;
}) {
  return (
    <div className="catalyst-group__row">
      {showFlag ? (
        <span className={`catalyst-group__flag${row.isHeld ? " catalyst-group__flag--held" : ""}`}>
          {row.isHeld ? "HELD" : ""}
        </span>
      ) : null}
      <span className="catalyst-group__name" title={row.title}>
        {row.ticker ? `${row.ticker} · ` : ""}
        {row.title}
      </span>
      <span className="catalyst-group__when">{catalystWhenLabel(row)}</span>
    </div>
  );
}

function CategorySection({
  category,
  isOpen,
  isExpanded,
  onToggleOpen,
  onToggleExpanded,
}: {
  category: CatalystCategory;
  isOpen: boolean;
  isExpanded: boolean;
  onToggleOpen: () => void;
  onToggleExpanded: () => void;
}) {
  const total = category.rows.length;
  const visible = isExpanded ? category.rows : category.rows.slice(0, PREVIEW_ROWS);

  return (
    <div className="catalyst-group">
      <button
        type="button"
        className="catalyst-group__head"
        aria-expanded={isOpen}
        onClick={onToggleOpen}
      >
        <span className="catalyst-group__caret" aria-hidden>
          {isOpen ? "▾" : "▸"}
        </span>
        <span className="catalyst-group__label">{category.label}</span>
        {category.heldCount > 0 ? (
          <span className="catalyst-group__held-count">{category.heldCount} held</span>
        ) : null}
        <span className="catalyst-group__rule" aria-hidden />
        <span className="catalyst-group__count">{total}</span>
      </button>

      {isOpen ? (
        <>
          <div className={`catalyst-group__rows${isExpanded ? " catalyst-group__rows--scroll" : ""}`}>
            {visible.map((row) => (
              <CatalystRowLine key={rowKey(row)} row={row} showFlag={category.heldCount > 0} />
            ))}
          </div>
          {total > PREVIEW_ROWS ? (
            <button type="button" className="catalyst-group__more" onClick={onToggleExpanded}>
              {isExpanded ? "SHOW LESS" : `SHOW ALL ${total}`}
            </button>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

/**
 * CatalystsQuadrant — upcoming catalysts grouped by category (economic data,
 * earnings, FDA, then the remainder) with progressive disclosure: the leading
 * category opens, the rest stay collapsed, and long categories preview a few
 * rows until expanded. Distances are recomputed at render time; the stored
 * days_until is advisory only.
 */
export default function CatalystsQuadrant({ positionTickers }: Props) {
  const { data, isLoading, error } = useCatalysts(true);
  const [openOverrides, setOpenOverrides] = useState<Record<string, boolean>>({});
  const [expandedCategories, setExpandedCategories] = useState<Record<string, boolean>>({});

  const categories = useMemo(
    () => groupCatalystsByCategory(data?.catalysts ?? [], positionTickers),
    [data, positionTickers],
  );

  const upcomingCount = categories.reduce((total, category) => total + category.rows.length, 0);

  const toggleOpen = useCallback((key: string, fallbackOpen: boolean) => {
    setOpenOverrides((prev) => ({ ...prev, [key]: !(prev[key] ?? fallbackOpen) }));
  }, []);

  const toggleExpanded = useCallback((key: string) => {
    setExpandedCategories((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  return (
    <section className="catalyst-quadrant snapshot-card">
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
        categories.map((category, index) => {
          const defaultOpen = index === 0;
          const isOpen = openOverrides[category.key] ?? defaultOpen;
          return (
            <CategorySection
              key={category.key}
              category={category}
              isOpen={isOpen}
              isExpanded={Boolean(expandedCategories[category.key])}
              onToggleOpen={() => toggleOpen(category.key, defaultOpen)}
              onToggleExpanded={() => toggleExpanded(category.key)}
            />
          );
        })
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

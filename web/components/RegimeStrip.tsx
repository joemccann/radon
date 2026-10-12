import type { ReactNode } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";

export type BadgeVariant = "live" | "daily";

export function LiveBadge({ live, variant }: { live: boolean; variant?: BadgeVariant }) {
  const resolved: BadgeVariant = variant ?? (live ? "live" : "daily");
  const bg =
    resolved === "live" ? "color-mix(in srgb, var(--positive) 15%, transparent)"
    : "color-mix(in srgb, var(--text-primary) 6%, transparent)";
  const color =
    resolved === "live" ? "var(--positive)"
    : "var(--text-muted)";
  const label =
    resolved === "live" ? "LIVE"
    : "DAILY";
  return (
    <span className="regime-badge" data-variant={resolved} style={{ background: bg, color }}>
      {label}
    </span>
  );
}

export function DayChange({ last, close, prefix }: { last: number | null; close: number | null; prefix?: string }) {
  if (last == null || close == null || close <= 0) return null;
  const change = last - close;
  const pct = (change / close) * 100;
  const isUp = change >= 0;
  const color = isUp ? "var(--positive)" : "var(--negative)";
  const Arrow = isUp ? ArrowUp : ArrowDown;
  return (
    <div className="regime-strip-day-chg" style={{ color }} data-testid="regime-day-chg">
      <span>{prefix}{isUp ? "+" : ""}{change.toFixed(2)} ({isUp ? "+" : ""}{pct.toFixed(2)}%)</span>
      <Arrow size={10} />
    </div>
  );
}

export function PointChange({ change, suffix, label }: { change: number | null; suffix?: string; label?: string }) {
  if (change == null || Math.abs(change) < 0.005) return null;
  const isUp = change >= 0;
  const color = isUp ? "var(--positive)" : "var(--negative)";
  const Arrow = isUp ? ArrowUp : ArrowDown;
  return (
    <div className="regime-strip-day-chg" style={{ color }} data-testid="regime-day-chg">
      <span>{isUp ? "+" : ""}{change.toFixed(2)}{suffix}{label ? ` ${label}` : ""}</span>
      <Arrow size={10} />
    </div>
  );
}

type RegimeStripCellProps = {
  testId: string;
  label: ReactNode;
  value: ReactNode;
  change?: ReactNode;
  sub?: ReactNode;
  timestamp?: ReactNode;
};

export function RegimeStripCell({ testId, label, value, change, sub, timestamp }: RegimeStripCellProps) {
  return (
    <div className="regime-strip-cell" data-testid={testId}>
      <div className="regime-strip-primary">
        <div className="regime-strip-label">{label}</div>
        <div className="regime-strip-value">{value}</div>
      </div>
      <div className="regime-strip-meta-row">
        {change}
        {sub ? <div className="regime-strip-sub">{sub}</div> : null}
        {timestamp ? <div className="regime-strip-ts">{timestamp}</div> : null}
      </div>
    </div>
  );
}

/**
 * `cadence="daily"` means the panel states the cadence once elsewhere, so per-tile DAILY pills hide.
 * `columns={4}` opts a four-cell strip out of the five-column grid so no dead tile pads the row.
 */
export function RegimeStrip({ children, cadence, columns }: { children: ReactNode; cadence?: "daily"; columns?: 4 }) {
  return (
    <div className={columns ? `regime-strip regime-strip--cols-${columns}` : "regime-strip"} data-cadence={cadence}>
      {children}
    </div>
  );
}

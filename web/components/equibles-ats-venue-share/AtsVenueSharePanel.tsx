"use client";

import { Layers } from "lucide-react";
import InfoTooltip from "../InfoTooltip";
import SectionEmptyState from "../SectionEmptyState";
import SpectralLoader from "../SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "../RegimeStrip";
import {
  classificationColor,
  classificationLabel,
  countByClassification,
  formatGap,
  formatPrintSize,
  formatSharePct,
  formatZ,
  venueDivergence,
  weekLabel,
  type AtsVenueShareRow,
  type AtsVenueShareThresholds,
} from "./atsVenueShare";
import { useAtsVenueShare } from "./useAtsVenueShare";
import SortTh from "../SortTh";
import { useSort } from "@/lib/useSort";

type AtsSortKey = "ticker" | "share" | "shareZ" | "print" | "printZ" | "shortVol" | "divergence" | "signal";

function atsExtract(row: AtsVenueShareRow, key: AtsSortKey): string | number | null {
  switch (key) {
    case "ticker": return row.ticker ?? null;
    case "share": return row.ats_share_pct;
    case "shareZ": return row.ats_share_z;
    case "print": return row.avg_ats_print_size;
    case "printZ": return row.avg_ats_print_size_z;
    case "shortVol": return row.short_volume_pct;
    case "divergence": return venueDivergence(row);
    case "signal": return row.classification;
    default: return null;
  }
}

function AtsVenueShareTable({ rows }: { rows: AtsVenueShareRow[] }) {
  const { sorted, sort, toggle } = useSort(rows, atsExtract);
  return (
    <table className="data-table" data-testid="ats-venue-share-table">
      <thead>
        <tr>
          <SortTh<AtsSortKey> label="Ticker" sortKey="ticker" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<AtsSortKey> label="ATS Share" sortKey="share" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} helpText="Dark-pool (ATS) slice of the week's FINRA off-exchange volume. Off-exchange volume is the only denominator the source publishes." helpAriaLabel="ATS share details" />
          <SortTh<AtsSortKey> label="Share Z" sortKey="shareZ" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} helpText="This week's ATS share against its trailing weekly window. Blank until enough history exists to score." helpAriaLabel="Share z-score details" />
          <SortTh<AtsSortKey> label="Avg ATS Print" sortKey="print" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} helpText="Average shares per ATS trade. Larger prints mean size is being worked in the dark rather than internalized in odd lots." helpAriaLabel="Average ATS print details" />
          <SortTh<AtsSortKey> label="Print Z" sortKey="printZ" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} helpText="This week's average ATS print size against its trailing weekly window." helpAriaLabel="Print z-score details" />
          <SortTh<AtsSortKey> label="Short Vol" sortKey="shortVol" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} helpText="Short volume share of the off-exchange tape. The hedging baseline the divergence column is measured against." helpAriaLabel="Short volume details" />
          <SortTh<AtsSortKey> label="Divergence" sortKey="divergence" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} helpText="ATS share minus short-volume share, in percentage points. Positive means dark-pool share is running ahead of shorting: buying rather than hedging." helpAriaLabel="Divergence details" />
          <SortTh<AtsSortKey> label="Signal" sortKey="signal" activeKey={sort.key} direction={sort.direction} onToggle={toggle} helpText="Accumulation needs elevated ATS share AND a larger average ATS print in the same week; distribution needs both depressed. Either alone is noise." helpAriaLabel="Signal details" />
        </tr>
      </thead>
      <tbody>
        {sorted.map((row) => (
          <tr key={row.ticker} data-testid={`ats-venue-share-row-${row.ticker}`}>
            <td>{row.ticker}</td>
            <td className="right">{formatSharePct(row.ats_share_pct)}</td>
            <td className="right">{formatZ(row.ats_share_z)}</td>
            <td className="right">{formatPrintSize(row.avg_ats_print_size)}</td>
            <td className="right">{formatZ(row.avg_ats_print_size_z)}</td>
            <td className="right">{formatSharePct(row.short_volume_pct)}</td>
            <td className="right">{formatGap(venueDivergence(row))}</td>
            <td>
              <SignalChip row={row} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const FOOTNOTE_BLOCK: React.CSSProperties = {
  marginTop: "14px",
  paddingTop: "12px",
  borderTop: "1px solid var(--border-dim)",
  display: "grid",
  gap: "8px",
};

const MONO_FOOTNOTE: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: "9px",
  color: "var(--text-muted)",
  lineHeight: "1.7",
  letterSpacing: "0.02em",
  maxWidth: "82ch",
};

function methodNote(
  week: string | null,
  thresholds: AtsVenueShareThresholds | null,
): string {
  const window = thresholds?.z_window_weeks ?? null;
  const minimum = thresholds?.min_history_weeks ?? null;
  const scope = window && minimum
    ? `Z-scores measure each week against its trailing ${window} weeks, and stay blank until ${minimum} observations exist.`
    : "";
  const source = week
    ? `FINRA off-exchange file, latest week ${week}.`
    : "FINRA off-exchange file.";
  return `${source} ATS share is measured against off-exchange volume, the only denominator the source publishes. Accumulation requires elevated ATS share AND a larger average ATS print in the same week. ${scope}`.trim();
}

function SignalChip({ row }: { row: AtsVenueShareRow }) {
  return (
    <span
      title={row.classification_reason ?? undefined}
      style={{
        color: classificationColor(row.classification),
        fontFamily: "var(--font-mono)",
        fontSize: "10px",
        letterSpacing: "0.04em",
        border: `1px solid color-mix(in srgb, ${classificationColor(row.classification)} 35%, transparent)`,
        background: `color-mix(in srgb, ${classificationColor(row.classification)} 10%, transparent)`,
        borderRadius: "4px",
        padding: "1px 6px",
        whiteSpace: "nowrap",
      }}
    >
      {classificationLabel(row.classification)}
    </span>
  );
}

export default function AtsVenueSharePanel() {
  const { data, loading, syncing, lastSync } = useAtsVenueShare();

  if ((loading || syncing) && !data) {
    return <SpectralLoader label="Sampling FINRA off-exchange venue share" />;
  }

  const rows = data?.current ?? [];
  if (!data || data.missing || rows.length === 0) {
    return (
      <SectionEmptyState
        icon={Layers}
        headline="No off-exchange venue data yet"
        secondary="The venue-share refresh populates this tab from FINRA's weekly off-exchange volume file for every watchlist ticker. Rows appear after the first successful pull."
      />
    );
  }

  const tally = countByClassification(rows);
  const errors = data.errors ?? [];

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Layers size={14} />
            ATS Venue Share
            <InfoTooltip text="FINRA off-exchange volume is the denominator dark-pool prints lack. ATS share is the dark-pool slice of that off-exchange tape; rising ATS share WITH a larger average ATS print size is an institutional accumulation footprint, since size is being worked in the dark rather than internalized in odd lots. Either signal alone is noise. Short-volume share sits alongside because divergence, dark-pool share running ahead of short volume, is buying rather than hedging." />
          </div>
          {lastSync && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "9px", color: "var(--text-muted)" }}>
              {new Date(lastSync).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
            </span>
          )}
        </div>

        <RegimeStrip>
          <RegimeStripCell
            testId="ats-venue-share-strip-week"
            label="LATEST WEEK"
            value={weekLabel(data.week_start_date)}
            sub={<>FINRA OFF-EXCHANGE FILE</>}
          />
          <RegimeStripCell
            testId="ats-venue-share-strip-coverage"
            label="TICKERS COVERED"
            value={String(rows.length)}
            sub={<>WATCHLIST SCOPE</>}
          />
          <RegimeStripCell
            testId="ats-venue-share-strip-accumulation"
            label="ACCUMULATION"
            value={
              <span style={{ color: classificationColor("accumulation") }}>
                {tally.accumulation}
              </span>
            }
            sub={<>SHARE AND PRINT SIZE BOTH UP</>}
          />
          <RegimeStripCell
            testId="ats-venue-share-strip-distribution"
            label="DISTRIBUTION"
            value={
              <span style={{ color: classificationColor("distribution") }}>
                {tally.distribution}
              </span>
            }
            sub={<>SHARE AND PRINT SIZE BOTH DOWN</>}
          />
        </RegimeStrip>
      </div>

      <div className="section">
        <div className="table-wrap">
          <AtsVenueShareTable rows={rows} />
        </div>

        <div style={FOOTNOTE_BLOCK} data-testid="ats-venue-share-footnotes">
          <div style={MONO_FOOTNOTE} data-testid="ats-venue-share-methodnote">
            {methodNote(data.week_start_date, data.thresholds)}
          </div>

          {errors.length > 0 && (
            <div style={MONO_FOOTNOTE} data-testid="ats-venue-share-errors">
              NOT COVERED THIS CYCLE: {errors.map((e) => `${e.ticker} (${e.error})`).join(", ")}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

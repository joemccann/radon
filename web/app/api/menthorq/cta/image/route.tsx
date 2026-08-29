import { computeCtaImageHeight, truncateCtaTables } from "@/lib/ctaImageLayout";
import { requireRouteAccess } from "@/lib/routeAccess";

import { ImageResponse } from "next/og";
import { readdir, readFile } from "fs/promises";
import { join } from "path";
import { loadFonts } from "@/lib/og-fonts";
import { OG, posColor, pctileBg, zColor, zOpacity, fmt } from "@/lib/og-theme";
import { reconcileCtaTables } from "@/lib/ctaPercentiles";

export const runtime = "nodejs";
// A satori render holding the whole bitmap must not be able to run unbounded.
export const maxDuration = 30;

const CACHE_DIR = join(process.cwd(), "..", "data", "menthorq_cache");

type CtaRow = {
  underlying: string;
  position_today: number;
  position_yesterday: number;
  position_1m_ago: number;
  percentile_1m: number | null;
  percentile_3m: number | null;
  percentile_1y: number | null;
  z_score_3m: number;
};

type CtaCache = {
  date: string;
  tables: Record<string, CtaRow[]>;
};

const SECTION_LABELS: Record<string, string> = {
  main: "MAIN INDICES",
  index: "INDEX FUTURES",
  commodity: "COMMODITIES",
  currency: "CURRENCIES",
};

const SECTIONS = ["main", "index", "commodity", "currency"] as const;

const COL_WIDTHS = {
  underlying: 280,
  num: 90,
};

const HEADER_COLS = [
  "UNDERLYING",
  "TODAY",
  "YDAY",
  "1M AGO",
  "1M %ILE",
  "3M %ILE",
  "1Y %ILE",
  "3M Z",
];

/** Build a dedup key from a row's underlying + position values.
 *  The MenthorQ CTA page pins an S&P 500 benchmark row at the top of
 *  every section with identical values — this key lets us detect and
 *  remove those duplicates from later sections. */
function rowKey(r: CtaRow): string {
  return `${r.underlying}|${r.position_today}|${r.position_yesterday}|${r.position_1m_ago}`;
}

/** Remove duplicate reference rows that appear across multiple sections.
 *  Processes sections in order; a row is kept only in its first section. */
function deduplicateTables(
  tables: Record<string, CtaRow[]>
): Record<string, CtaRow[]> {
  const seen = new Set<string>();
  const result: Record<string, CtaRow[]> = {};
  for (const section of SECTIONS) {
    const rows = tables[section] ?? [];
    result[section] = rows.filter((r) => {
      const key = rowKey(r);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }
  return result;
}

async function loadLatestCta(
  section?: string
): Promise<CtaCache | null> {
  try {
    const files = await readdir(CACHE_DIR);
    const ctaFiles = files
      .filter((f) => f.startsWith("cta_") && f.endsWith(".json"))
      .sort();
    if (ctaFiles.length === 0) return null;
    const raw = await readFile(
      join(CACHE_DIR, ctaFiles[ctaFiles.length - 1]),
      "utf-8"
    );
    const data = JSON.parse(raw) as CtaCache;
    if (!data.tables) return data;

    // Same reconciliation the CTA route applies: rounded-away percentiles are
    // repaired from the duplicate row before dedupe drops it.
    data.tables = deduplicateTables(reconcileCtaTables(data.tables));

    if (section) {
      const filtered: Record<string, CtaRow[]> = {};
      if (data.tables[section]) filtered[section] = data.tables[section];
      return { ...data, tables: filtered };
    }
    return data;
  } catch {
    return null;
  }
}

function SectionHeader({ label, count }: { label: string; count: number }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "8px",
        padding: "10px 16px",
        borderBottom: `1px solid ${OG.border}`,
        borderTop: `1px solid ${OG.border}`,
        background: OG.panel,
      }}
    >
      <span
        style={{
          fontSize: "11px",
          fontWeight: 700,
          color: OG.text,
          letterSpacing: "0.08em",
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontSize: "10px",
          color: OG.muted,
          marginLeft: "4px",
        }}
      >
        ({count})
      </span>
    </div>
  );
}

function TableHeader() {
  return (
    <div
      style={{
        display: "flex",
        padding: "6px 16px",
        borderBottom: `1px solid ${OG.border}`,
      }}
    >
      <span
        style={{
          width: `${COL_WIDTHS.underlying}px`,
          fontSize: "9px",
          fontWeight: 700,
          color: OG.muted,
          letterSpacing: "0.1em",
        }}
      >
        {HEADER_COLS[0]}
      </span>
      {HEADER_COLS.slice(1).map((col) => (
        <span
          key={col}
          style={{
            width: `${COL_WIDTHS.num}px`,
            fontSize: "9px",
            fontWeight: 700,
            color: OG.muted,
            letterSpacing: "0.1em",
            textAlign: "right",
          }}
        >
          {col}
        </span>
      ))}
    </div>
  );
}

function DataRow({ row }: { row: CtaRow }) {
  const pctileText = (value: number | null) =>
    value == null ? "---" : value > 100 ? fmt(value) : String(value);
  const pctileYDisplay = pctileText(row.percentile_1y);

  return (
    <div
      style={{
        display: "flex",
        padding: "5px 16px",
        borderBottom: `1px solid ${OG.border}`,
      }}
    >
      <span
        style={{
          width: `${COL_WIDTHS.underlying}px`,
          fontSize: "11px",
          color: OG.text,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {row.underlying}
      </span>
      <span
        style={{
          width: `${COL_WIDTHS.num}px`,
          fontSize: "11px",
          color: posColor(row.position_today),
          textAlign: "right",
        }}
      >
        {fmt(row.position_today)}
      </span>
      <span
        style={{
          width: `${COL_WIDTHS.num}px`,
          fontSize: "11px",
          color: posColor(row.position_yesterday),
          textAlign: "right",
        }}
      >
        {fmt(row.position_yesterday)}
      </span>
      <span
        style={{
          width: `${COL_WIDTHS.num}px`,
          fontSize: "11px",
          color: posColor(row.position_1m_ago),
          textAlign: "right",
        }}
      >
        {fmt(row.position_1m_ago)}
      </span>
      <span
        style={{
          width: `${COL_WIDTHS.num}px`,
          fontSize: "11px",
          textAlign: "right",
          background: pctileBg(row.percentile_1m),
          padding: "2px 4px",
        }}
      >
        {pctileText(row.percentile_1m)}
      </span>
      <span
        style={{
          width: `${COL_WIDTHS.num}px`,
          fontSize: "11px",
          textAlign: "right",
          background: pctileBg(row.percentile_3m),
          padding: "2px 4px",
        }}
      >
        {pctileText(row.percentile_3m)}
      </span>
      <span
        style={{
          width: `${COL_WIDTHS.num}px`,
          fontSize: "11px",
          textAlign: "right",
          background: pctileBg(row.percentile_1y),
          padding: "2px 4px",
        }}
      >
        {pctileYDisplay}
      </span>
      <span
        style={{
          width: `${COL_WIDTHS.num}px`,
          fontSize: "11px",
          color: zColor(row.z_score_3m),
          opacity: zOpacity(row.z_score_3m),
          textAlign: "right",
        }}
      >
        {fmt(row.z_score_3m)}
      </span>
    </div>
  );
}

export const radonCapability = "read";

export async function GET(request: Request) {
  const access = await requireRouteAccess(undefined, { rate: { key: "menthorq/cta/image:route", limit: 20, windowMs: 60_000 } });
  if (!access.ok) return access.response;
  const { searchParams } = new URL(request.url);
  const section = searchParams.get("section") ?? undefined;

  const data = await loadLatestCta(section);
  if (!data?.tables) {
    return new Response("No CTA data available", { status: 404 });
  }

  const fonts = await loadFonts();

  // Truncate before laying out, not after: the row count comes from a cache
  // FILE, so a malformed extraction could otherwise ask for a canvas tens of
  // thousands of pixels tall on a Node-runtime satori render. R-310.
  truncateCtaTables(data.tables);

  const totalRows = Object.values(data.tables).reduce(
    (sum, rows) => sum + rows.length,
    0
  );
  const sectionCount = Object.keys(data.tables).length;
  const height = computeCtaImageHeight({ sectionCount, totalRows });

  const activeSections = SECTIONS.filter(
    (key) => data.tables[key] && data.tables[key].length > 0
  );

  return new ImageResponse(
    (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          width: "100%",
          height: "100%",
          background: OG.bg,
          fontFamily: "IBM Plex Mono",
          color: OG.text,
        }}
      >
        {/* Title bar */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            padding: "14px 16px",
            borderBottom: `1px solid ${OG.border}`,
          }}
        >
          <span
            style={{
              fontSize: "13px",
              fontWeight: 700,
              letterSpacing: "0.1em",
              color: OG.text,
            }}
          >
            CTA POSITIONING
          </span>
          <span
            style={{
              fontSize: "11px",
              color: OG.muted,
              marginLeft: "12px",
            }}
          >
            {data.date ?? "---"}
          </span>
        </div>

        {/* Sections */}
        {activeSections.map((key) => {
          const rows = data.tables[key];
          return (
            <div key={key} style={{ display: "flex", flexDirection: "column" }}>
              <SectionHeader
                label={SECTION_LABELS[key] ?? key.toUpperCase()}
                count={rows.length}
              />
              <TableHeader />
              {rows.map((row, i) => (
                <DataRow key={`${key}-${i}`} row={row} />
              ))}
            </div>
          );
        })}
      </div>
    ),
    {
      width: 920,
      height,
      fonts: fonts as any,
    }
  );
}

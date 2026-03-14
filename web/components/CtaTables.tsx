"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useMenthorqCta, type CtaRow } from "@/lib/useMenthorqCta";

/* ─── Helpers ────────────────────────────────────────── */

import { fmt } from "@/lib/format";

function posColor(v: number): string {
  if (v > 0) return "var(--positive)";
  if (v < 0) return "var(--negative)";
  return "var(--text-primary)";
}

function pctileBg(v: number): string {
  if (v <= 10) return "rgba(232,93,108,0.25)";
  if (v <= 25) return "rgba(232,93,108,0.12)";
  if (v <= 40) return "rgba(245,166,35,0.12)";
  if (v >= 75) return "rgba(5,173,152,0.25)";
  if (v >= 60) return "rgba(5,173,152,0.12)";
  return "transparent";
}

function zColor(z: number): string {
  if (z > 0) return "var(--positive)";
  if (z < 0) return "var(--negative)";
  return "var(--text-primary)";
}

function zOpacity(z: number): number {
  const abs = Math.abs(z);
  if (abs >= 2) return 1;
  if (abs >= 1) return 0.85;
  if (abs >= 0.5) return 0.7;
  return 0.55;
}

/* ─── Section ────────────────────────────────────────── */

const SECTION_LABELS: Record<string, string> = {
  main: "MAIN INDICES",
  index: "INDEX FUTURES",
  commodity: "COMMODITIES",
  currency: "CURRENCIES",
};

function CtaSection({ sectionKey, rows }: { sectionKey: string; rows: CtaRow[] }) {
  const [open, setOpen] = useState(true);

  return (
    <div >
      <button className="cta-s-hd" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {SECTION_LABELS[sectionKey] ?? sectionKey.toUpperCase()}
        <span className="cc90">{rows.length}</span>
      </button>
      {open && (
        <div className="ctw">
          <table className="cta-table">
            <thead>
              <tr>
                <th className="ctu">UNDERLYING</th>
                <th className="ctn">TODAY</th>
                <th className="ctn">YDAY</th>
                <th className="ctn">1M AGO</th>
                <th className="ctn">1M %ILE</th>
                <th className="ctn">3M %ILE</th>
                <th className="ctn">1Y %ILE</th>
                <th className="ctn">3M Z</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.underlying}>
                  <td className="cdu">{r.underlying}</td>
                  <td className="cdn" style={{ color: posColor(r.position_today) }}>
                    {fmt(r.position_today)}
                  </td>
                  <td className="cdn" style={{ color: posColor(r.position_yesterday) }}>
                    {fmt(r.position_yesterday)}
                  </td>
                  <td className="cdn" style={{ color: posColor(r.position_1m_ago) }}>
                    {fmt(r.position_1m_ago)}
                  </td>
                  <td className="cdn" style={{ background: pctileBg(r.percentile_1m) }}>
                    {r.percentile_1m}
                  </td>
                  <td className="cdn" style={{ background: pctileBg(r.percentile_3m) }}>
                    {r.percentile_3m}
                  </td>
                  <td className="cdn" style={{ background: pctileBg(r.percentile_1y) }}>
                    {typeof r.percentile_1y === "number" && r.percentile_1y > 100
                      ? fmt(r.percentile_1y)
                      : r.percentile_1y}
                  </td>
                  <td
                    className="cdn"
                    style={{ color: zColor(r.z_score_3m), opacity: zOpacity(r.z_score_3m) }}
                  >
                    {fmt(r.z_score_3m)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ─── Helpers ────────────────────────────────────────── */

function formatFetchedAt(iso: string | null | undefined): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleString("en-US", {
      timeZone: "America/New_York",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    }) + " ET";
  } catch {
    return "";
  }
}

/* ─── Main Component ─────────────────────────────────── */

export default function CtaTables() {
  const { data, loading } = useMenthorqCta();

  if (loading) {
    return (
      <div className="ce">
        Loading CTA positioning data...
      </div>
    );
  }

  if (!data?.tables) {
    return (
      <div className="ce">
        No MenthorQ CTA data available. Run: <code>menthorq-cta</code>
      </div>
    );
  }

  const order = ["main", "index", "commodity", "currency"] as const;
  const fetchLabel = formatFetchedAt(data.fetched_at);

  return (
    <div className="cc147">
      <div className="cl129">
        MENTHORQ CTA POSITIONING — {data.date ?? "---"}
        {fetchLabel && (
          <span className="ca130"> · FETCHED {fetchLabel}</span>
        )}
      </div>
      {order.map((key) => {
        const rows = data.tables![key];
        if (!rows || rows.length === 0) return null;
        return <CtaSection key={key} sectionKey={key} rows={rows} />;
      })}
    </div>
  );
}

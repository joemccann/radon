"use client";

import { useCallback, useEffect, useState } from "react";
import { FileSearch } from "lucide-react";
import InfoTooltip from "@/components/InfoTooltip";
import SectionEmptyState from "@/components/SectionEmptyState";
import SpectralLoader from "@/components/SpectralLoader";

/**
 * Filing Forensics — the pre-trade check on what the issuer has already told
 * the SEC it is about to do.
 *
 * The surface exists to keep four states apart. Absence of data is NEVER an
 * all clear:
 *   FLAGGED           a filing breaches a named threshold
 *   CLEAR             filings exist, none breach
 *   NO FILINGS FOUND  the source answered with nothing on file
 *   COULD NOT VERIFY  the source failed, so the check is unknown
 */

export type FilingVerdict = "flagged" | "clear" | "no-filings" | "unavailable";

export type FilingCitation = {
  form?: string | null;
  filed_at?: string | null;
  url?: string | null;
};

export type FilingFigure = {
  value: number;
  unit: "usd" | "count";
  label: string;
};

export type FilingCheck = {
  code: string;
  label: string;
  gate: number;
  tone: "adverse" | "supportive";
  verdict: FilingVerdict;
  detail: string;
  figure?: FilingFigure | null;
  filing?: FilingCitation | null;
};

export type FilingForensicsData = {
  ticker: string;
  as_of?: string | null;
  data_complete?: boolean;
  flag_count?: number;
  checks?: FilingCheck[];
  missing?: boolean;
};

const VERDICT_LABEL: Record<FilingVerdict, string> = {
  flagged: "FLAGGED",
  clear: "CLEAR",
  "no-filings": "NO FILINGS FOUND",
  unavailable: "COULD NOT VERIFY",
};

const GATE_LABEL: Record<number, string> = {
  2: "GATE 2 EDGE",
  3: "GATE 3 RISK",
};

const FORM_144_NOTE =
  "Form 144 is filed before an insider sells, so it is forward looking supply. Form 4, which the informed flow surface carries, is filed after the sale.";

const PARTIAL_DATA_NOTE =
  "Partial data. Checks marked COULD NOT VERIFY are unknown, not clear.";

export function verdictColor(check: Pick<FilingCheck, "verdict" | "tone">): string {
  if (check.verdict === "flagged") {
    return check.tone === "supportive" ? "var(--positive)" : "var(--negative)";
  }
  if (check.verdict === "unavailable") return "var(--warning)";
  if (check.verdict === "clear") return "var(--text-secondary)";
  return "var(--text-muted)";
}

export function formatFigure(figure: FilingFigure | null | undefined): string | null {
  if (!figure || typeof figure.value !== "number") return null;
  if (figure.unit === "count") return String(figure.value);
  const abs = Math.abs(figure.value);
  if (abs >= 1_000_000_000) return `$${(figure.value / 1_000_000_000).toFixed(1)}B`;
  if (abs >= 1_000_000) return `$${(figure.value / 1_000_000).toFixed(1)}M`;
  return `$${figure.value.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

export function formatAsOf(asOf: string | null | undefined): string | null {
  if (!asOf) return null;
  const stamp = new Date(asOf);
  if (Number.isNaN(stamp.getTime())) return null;
  return stamp.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

const META_STYLE = {
  fontFamily: "var(--font-mono)",
  fontSize: "9px",
  letterSpacing: "var(--tracking-meta)",
  color: "var(--text-muted)",
} as const;

function FilingLink({ filing }: { filing: FilingCitation }) {
  const form = filing.form ?? "FILING";
  const filed = filing.filed_at ? ` ${filing.filed_at}` : "";
  if (!filing.url) {
    return (
      <span style={META_STYLE} data-testid="filing-citation">
        SOURCE {form}
        {filed} (no link)
      </span>
    );
  }
  return (
    <a
      href={filing.url}
      target="_blank"
      rel="noopener noreferrer"
      style={{ ...META_STYLE, color: "var(--signal-core)", textDecoration: "none" }}
      data-testid="filing-citation"
    >
      SOURCE {form}
      {filed}
    </a>
  );
}

function CheckRow({ check }: { check: FilingCheck }) {
  const figure = formatFigure(check.figure);
  return (
    <li
      data-testid={`filing-check-${check.code}`}
      data-verdict={check.verdict}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "4px",
        padding: "10px 0",
        borderTop: "1px solid var(--border-dim)",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: "8px", flexWrap: "wrap" }}>
        <span style={{ fontFamily: "var(--font-sans)", fontSize: "12px", color: "var(--text-primary)" }}>
          {check.label}
        </span>
        <span style={META_STYLE}>{GATE_LABEL[check.gate] ?? `GATE ${check.gate}`}</span>
        <span
          data-testid={`filing-verdict-${check.code}`}
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "9px",
            letterSpacing: "var(--tracking-label)",
            color: verdictColor(check),
            border: `1px solid ${verdictColor(check)}`,
            borderRadius: "2px",
            padding: "1px 4px",
          }}
        >
          {VERDICT_LABEL[check.verdict]}
        </span>
        {figure && (
          <span
            data-testid={`filing-figure-${check.code}`}
            style={{ fontFamily: "var(--font-mono)", fontSize: "12px", color: verdictColor(check) }}
          >
            {figure}
          </span>
        )}
      </div>
      <span style={{ fontFamily: "var(--font-sans)", fontSize: "11px", color: "var(--text-secondary)" }}>
        {check.detail}
      </span>
      {check.filing && <FilingLink filing={check.filing} />}
    </li>
  );
}

export default function FilingForensicsDossier({
  ticker,
  active = true,
}: {
  ticker: string;
  active?: boolean;
}) {
  const [data, setData] = useState<FilingForensicsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(
        `/api/equibles-filing-forensics?ticker=${encodeURIComponent(ticker)}`,
        { cache: "no-store" },
      );
      if (!res.ok) throw new Error("Failed to load filing forensics");
      setData((await res.json()) as FilingForensicsData);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load filing forensics");
    } finally {
      setLoading(false);
    }
  }, [ticker]);

  useEffect(() => {
    if (!active) return;
    void load();
  }, [active, load]);

  if (loading && !data) {
    return <SpectralLoader label={`Loading filing forensics for ${ticker}`} />;
  }

  if (error) {
    return (
      <SectionEmptyState
        icon={FileSearch}
        headline="Filing forensics unavailable"
        secondary={`${error}. Treat this as unknown, not as an all clear.`}
        tone="danger"
        testId="filing-forensics-error"
      />
    );
  }

  const checks = data?.checks ?? [];
  if (!data || data.missing || checks.length === 0) {
    return (
      <SectionEmptyState
        icon={FileSearch}
        headline="No dossier yet"
        secondary={`The filing forensics refresh has not built a dossier for ${ticker}. No dossier is not the same as no overhang.`}
        testId="filing-forensics-missing"
      />
    );
  }

  const asOf = formatAsOf(data.as_of);

  return (
    <div className="section" data-testid="filing-forensics">
      <div className="section-header">
        <div className="section-title">
          <FileSearch size={14} />
          Filing Forensics
          <InfoTooltip text={FORM_144_NOTE} />
        </div>
        {asOf && (
          <span style={META_STYLE} data-testid="filing-forensics-as-of">
            AS OF {asOf.toUpperCase()}
          </span>
        )}
      </div>

      {data.data_complete === false && (
        <div
          data-testid="filing-forensics-partial"
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "10px",
            color: "var(--warning)",
            padding: "6px 0",
          }}
        >
          {PARTIAL_DATA_NOTE}
        </div>
      )}

      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {checks.map((check) => (
          <CheckRow key={check.code} check={check} />
        ))}
      </ul>
    </div>
  );
}

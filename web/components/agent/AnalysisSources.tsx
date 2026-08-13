"use client";

/**
 * AnalysisSources — source provenance + follow-ups for streamed analysis.
 * Adopted from beautifului.dev "Streaming Text". Renders under analysis
 * prose (Live Market Analysis, chat replies): a source grid mapping each
 * claim window to its feed, and follow-up chips that re-enter the composer.
 * Confidence and source over adjectives — house voice §"Content".
 */

export type AnalysisSource = {
  id: string;
  /** Display name, e.g. "Unusual Whales flow". */
  name: string;
  /** Feed telemetry, e.g. "uw feed", "ib feed", "derived". */
  feed: string;
  href?: string;
};

type AnalysisSourcesProps = {
  sources: AnalysisSource[];
  followUps?: string[];
  onFollowUp?: (prompt: string) => void;
};

export default function AnalysisSources({ sources, followUps = [], onFollowUp }: AnalysisSourcesProps) {
  return (
    <div className="analysis-sources">
      {sources.length ? (
        <>
          <div className="analysis-sources__label">
            SOURCES <span className="agent-chip agent-chip--signal">{sources.length}</span>
          </div>
          <div className="analysis-sources__grid">
            {sources.map((s) => (
              <a
                key={s.id}
                className="analysis-sources__row"
                href={s.href ?? "#"}
                onClick={s.href ? undefined : (e) => e.preventDefault()}
              >
                <span className="analysis-sources__name">{s.name}</span>
                <span className="analysis-sources__feed">{s.feed}</span>
              </a>
            ))}
          </div>
        </>
      ) : null}
      {followUps.length ? (
        <>
          <div className="analysis-sources__label">FOLLOW-UPS</div>
          <div className="analysis-sources__followups">
            {followUps.map((f) => (
              <button key={f} type="button" className="agent-chip agent-chip--action" onClick={() => onFollowUp?.(f)}>
                {f}
              </button>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}

/**
 * InlineSourceMark — tiny capsule cited inline in prose ("UW", "IB").
 * Usage: <InlineSourceMark label="UW" /> immediately after the claim.
 */
export function InlineSourceMark({ label, href }: { label: string; href?: string }) {
  const Tag = href ? "a" : "span";
  return (
    <Tag className="inline-source-mark" href={href}>
      {label}
    </Tag>
  );
}

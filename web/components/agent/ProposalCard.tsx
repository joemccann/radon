"use client";

/**
 * ProposalCard — actionable engine proposal with a confidence meter and
 * ranked alternatives. Adopted from beautifului.dev "Recommendation Card".
 * Rendered by Scanner for the top-ranked candidate when an engine emits an
 * actionable interpretation. Accept never routes an order directly — it
 * hands off to ApprovalGate when execution is implied.
 */

export type ProposalAlternative = {
  id: string;
  label: string;
  /** Mono verdict, e.g. "NEEDS REVIEW", "NO SIGNAL". */
  meta: string;
};

type ProposalCardProps = {
  /** Engine chips, e.g. ["MARKOV", "SPECTRAL"]. */
  engines?: string[];
  /** Declarative proposal statement. */
  statement: string;
  /** 0–1. */
  confidence: number;
  /** Mono confidence caption, e.g. "0.82 · HIGH". Derived if omitted. */
  confidenceLabel?: string;
  alternatives?: ProposalAlternative[];
  busy?: boolean;
  onAccept: () => void;
  onDismiss: () => void;
};

function defaultLabel(c: number) {
  const band = c >= 0.75 ? "HIGH" : c >= 0.5 ? "MODERATE" : "LOW";
  return `${c.toFixed(2)} · ${band}`;
}

export default function ProposalCard({
  engines = [],
  statement,
  confidence,
  confidenceLabel,
  alternatives = [],
  busy = false,
  onAccept,
  onDismiss,
}: ProposalCardProps) {
  const pct = Math.round(Math.min(1, Math.max(0, confidence)) * 100);
  return (
    <section className="proposal-card" aria-label="Proposed action">
      <div className="proposal-card__head">
        <span className="proposal-card__module">SCANNER / PROPOSAL</span>
        <span className="proposal-card__title">Proposed action</span>
        <span className="proposal-card__chips">
          {engines.map((e) => (
            <span key={e} className="agent-chip agent-chip--engine">{e}</span>
          ))}
        </span>
      </div>
      <div className="proposal-card__body">
        <p className="proposal-card__statement">{statement}</p>
        <div className="proposal-card__confidence">
          <div className="proposal-card__confidence-row">
            <span>CONFIDENCE</span>
            <span className="proposal-card__confidence-value">
              {confidenceLabel ?? defaultLabel(confidence)}
            </span>
          </div>
          <div
            className="proposal-card__meter"
            role="meter"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct}
            aria-label="Confidence"
          >
            <div className="proposal-card__meter-fill" style={{ width: `${pct}%` }} />
          </div>
        </div>
        {alternatives.length ? (
          <div className="proposal-card__alts">
            <div className="proposal-card__alts-label">ALTERNATIVES</div>
            {alternatives.map((a) => (
              <div key={a.id} className="proposal-card__alt">
                <span>{a.label}</span>
                <span className="proposal-card__alt-meta">{a.meta}</span>
              </div>
            ))}
          </div>
        ) : null}
        <div className="proposal-card__actions">
          <button type="button" className="btn-primary" disabled={busy} onClick={onAccept}>
            Accept
          </button>
          <button type="button" className="btn-secondary" disabled={busy} onClick={onDismiss}>
            Dismiss
          </button>
        </div>
      </div>
    </section>
  );
}

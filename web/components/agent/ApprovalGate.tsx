"use client";

import { useState, type ReactNode } from "react";

/**
 * ApprovalGate — structured human-in-the-loop confirmation. Adopted from
 * beautifului.dev "Approval Card". Upgrades the flat chat-order-confirm
 * banner: selectable handling options with per-option risk telemetry, an
 * optional expiry, and explicit Confirm/Dismiss. Never auto-executes (F7).
 */

export type GateOption = {
  id: string;
  label: string;
  /** Mono meta on the right, e.g. "2.1× BAND", "NO ROUTE". */
  meta?: string;
};

type ApprovalGateProps = {
  title?: string;
  /** Declarative condition statement, e.g. the order summary + why gated. */
  body: string;
  /**
   * Telemetry rendered above the handling options, e.g. the nine-field
   * <OrderQuoteTelemetry> block for the instrument being routed. The gate stays
   * presentational: the caller owns the quote, the gate owns the layout.
   */
  quote?: ReactNode;
  options: GateOption[];
  defaultOptionId?: string;
  /** Mono expiry telemetry, e.g. "EXPIRES 09:32:00 ET". */
  expiry?: string;
  busy?: boolean;
  /**
   * Blocks confirmation while leaving Dismiss live — for an upstream gate that
   * has not cleared yet (e.g. OrderRiskGate's coverage still resolving).
   * Distinct from `busy`, which means "this gate is itself routing".
   */
  confirmDisabled?: boolean;
  onConfirm: (optionId: string) => void;
  onDismiss: () => void;
};

export default function ApprovalGate({
  title = "Confirmation required",
  body,
  quote,
  options,
  defaultOptionId,
  expiry,
  busy = false,
  confirmDisabled = false,
  onConfirm,
  onDismiss,
}: ApprovalGateProps) {
  const [selected, setSelected] = useState(defaultOptionId ?? options[0]?.id ?? "");
  return (
    <section className="approval-gate" role="group" aria-label={title}>
      <div className="approval-gate__head">
        <span className="approval-gate__module">OPERATOR / GATE</span>
        <span className="approval-gate__title">{title}</span>
        <span className="agent-chip agent-chip--warn">HUMAN-IN-LOOP</span>
      </div>
      <div className="approval-gate__body">
        <p className="approval-gate__statement">{body}</p>
        {quote}
        <div className="approval-gate__options" role="radiogroup" aria-label="Handling options">
          {options.map((opt) => (
            <button
              key={opt.id}
              type="button"
              role="radio"
              aria-checked={selected === opt.id}
              className="approval-gate__option"
              data-selected={selected === opt.id}
              disabled={busy}
              onClick={() => setSelected(opt.id)}
            >
              <span className="approval-gate__option-label">{opt.label}</span>
              {opt.meta ? <span className="approval-gate__option-meta">{opt.meta}</span> : null}
            </button>
          ))}
        </div>
        <div className="approval-gate__actions">
          <button
            type="button"
            className="btn-primary"
            disabled={busy || confirmDisabled || !selected}
            onClick={() => onConfirm(selected)}
          >
            {busy ? "Routing…" : "Confirm selection"}
          </button>
          <button type="button" className="btn-secondary" disabled={busy} onClick={onDismiss}>
            Dismiss
          </button>
          {expiry ? <span className="approval-gate__expiry">{expiry}</span> : null}
        </div>
      </div>
    </section>
  );
}

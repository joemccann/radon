"use client";

import ThinkingWait from "@/components/fx/ThinkingWait";

/**
 * EngineTrace — expandable reasoning trace for engine turns (Spectral, Eigen,
 * Markov, Laplace). Adopted from beautifului.dev "Thinking", re-skinned to the
 * instrument grammar: square status dots, mono telemetry, per-step timing,
 * a one-line declarative reasoning summary. Drop-in replacement for the
 * typing-dots indicator once the backend emits step events.
 */

export type TraceStepState = "done" | "running" | "waiting";

export type TraceStep = {
  id: string;
  label: string;
  /** Mono meta on the right — elapsed time ("1.8s") or "—" while waiting. */
  meta?: string;
  state: TraceStepState;
};

type EngineTraceProps = {
  /** Engine chips shown in the header, e.g. ["SPECTRAL", "EIGEN"]. */
  engines?: string[];
  steps: TraceStep[];
  /** Total elapsed, mono in the header ("4.2S"). */
  elapsed?: string;
  /** Declarative one-line summary once available. */
  reasoning?: string;
  /** Collapsed shows only the header row. Default expanded while running. */
  collapsed?: boolean;
  onToggle?: () => void;
};

export default function EngineTrace({
  engines = [],
  steps,
  elapsed,
  reasoning,
  collapsed = false,
  onToggle,
}: EngineTraceProps) {
  const running = steps.some((s) => s.state === "running");
  return (
    <section className="engine-trace" aria-label="Engine trace" data-running={running}>
      <button
        type="button"
        className="engine-trace__head"
        onClick={onToggle}
        aria-expanded={!collapsed}
      >
        {running ? (
          <ThinkingWait kind="agent" label="Agent working" />
        ) : (
          <span className="agent-dot agent-dot--signal" aria-hidden="true" />
        )}
        <span className="engine-trace__title">Engine trace</span>
        <span className="engine-trace__chips">
          {engines.map((e) => (
            <span key={e} className="agent-chip agent-chip--engine">{e}</span>
          ))}
        </span>
        {elapsed ? <span className="engine-trace__elapsed">{elapsed}</span> : null}
      </button>
      {!collapsed ? (
        <div className="engine-trace__body">
          <ol className="engine-trace__steps">
            {steps.map((step) => (
              <li key={step.id} className="engine-trace__step" data-state={step.state}>
                {step.state === "running" ? (
                  <ThinkingWait kind="agent" label={step.label} />
                ) : (
                  <span
                    className={`agent-dot${
                      step.state === "done" ? " agent-dot--signal" : " agent-dot--muted"
                    }`}
                    aria-hidden="true"
                  />
                )}
                <span className="engine-trace__step-label">{step.label}</span>
                <span className="engine-trace__step-meta">{step.meta ?? "—"}</span>
              </li>
            ))}
          </ol>
          {reasoning ? <p className="engine-trace__reasoning">{reasoning}</p> : null}
        </div>
      ) : null}
    </section>
  );
}

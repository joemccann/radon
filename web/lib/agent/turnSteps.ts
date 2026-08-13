/**
 * Turn steps — maps the assistant loop's tool telemetry onto EngineTrace's
 * step model.
 *
 * `/api/assistant` already returns `toolEvents` (one per tool call the agentic
 * loop made) and `rounds`; the client just discarded them. This module is the
 * translation layer, kept pure so the trace can be unit-tested without
 * rendering the chat.
 *
 * Step ordering mirrors execution order: every completed tool call, then a
 * trailing synthetic step for the phase the turn is currently in. A turn that
 * has not yet come back from the network has no tool events at all, so it shows
 * a single running "Routing request" step — the drop-in for the typing dots.
 */

import type { TraceStep } from "@/components/agent";
import type { AssistantToolEvent } from "@/lib/types";

/** Chat request lifecycle, mirrored from ChatPanel's ChatStatus. */
export type TurnPhase = "submitted" | "streaming" | "done" | "error";

const TOOL_LABELS: Record<string, string> = {
  get_flow: "Read dark-pool flow",
  run_scan: "Run scanner",
  get_gex: "Read gamma exposure",
  get_portfolio: "Read portfolio",
  search_knowledge: "Search knowledge base",
  find_prior_evals: "Find prior evaluations",
  get_realized_pnl: "Read realized P&L",
  query_journal: "Query trade journal",
  place_order: "Stage order proposal",
};

/**
 * Human-readable label for a tool. Falls back to de-snake-casing the raw name
 * so a newly added tool degrades to something readable instead of vanishing.
 */
export function describeTool(name: string): string {
  const known = TOOL_LABELS[name];
  if (known) return known;
  const words = name.replace(/_/g, " ").trim();
  if (!words) return "Tool call";
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function toolMeta(event: AssistantToolEvent): string {
  if (!event.ok) return "FAILED";
  if (event.repeated) return "CACHED";
  return "OK";
}

/**
 * The trailing synthetic step describing what the turn is doing right now.
 * `done` contributes no trailing step — every real step has already settled.
 */
function phaseStep(phase: TurnPhase, hasTools: boolean): TraceStep | null {
  if (phase === "submitted") {
    return {
      id: "phase-route",
      label: hasTools ? "Reasoning over tool results" : "Routing request",
      state: "running",
    };
  }
  if (phase === "streaming") {
    return { id: "phase-compose", label: "Composing response", state: "running" };
  }
  if (phase === "error") {
    return { id: "phase-error", label: "Turn failed", meta: "ERROR", state: "waiting" };
  }
  return null;
}

/**
 * Builds the ordered trace for a turn. Tool events are always settled by the
 * time the client sees them — the loop runs server-side and returns once — so
 * they render as `done` (or `waiting` when the call itself errored).
 */
export function buildTurnSteps(toolEvents: AssistantToolEvent[], phase: TurnPhase): TraceStep[] {
  const steps: TraceStep[] = toolEvents.map((event, index) => ({
    id: `tool-${index}-${event.name}`,
    label: describeTool(event.name),
    meta: toolMeta(event),
    state: event.ok ? "done" : "waiting",
  }));

  const trailing = phaseStep(phase, steps.length > 0);
  if (trailing) steps.push(trailing);
  return steps;
}

/**
 * Engine chips for the trace header. The loop reports the concrete model id;
 * SPECTRAL is Radon's house name for the default reasoning engine, so an
 * unknown/absent model still labels the turn rather than rendering bare.
 */
export function describeEngines(model?: string | null): string[] {
  if (!model) return ["SPECTRAL"];
  const normalized = model.toLowerCase();
  if (normalized.includes("grok")) return ["GROK"];
  if (normalized.includes("claude") || normalized.includes("opus") || normalized.includes("sonnet")) {
    return ["CLAUDE"];
  }
  return [model.toUpperCase()];
}

/** Mono elapsed telemetry for the trace header, e.g. "4.2S". */
export function formatElapsed(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "";
  return `${(ms / 1000).toFixed(1)}S`;
}

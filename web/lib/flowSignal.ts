/**
 * Flow signal classification — derives BULLISH / NEUTRAL / BEARISH from a
 * single-ticker flow report (the shape produced by `scripts/fetch_flow.py`
 * combined with `scripts/scanner.py:analyze_signal`).
 *
 * Deterministic, side-effect free. Server and client both call this so that
 * callers see the same verdict regardless of where it is computed.
 *
 * Inputs we read (defensively — every field is optional):
 *   dark_pool.aggregate.flow_direction       ACCUMULATION | DISTRIBUTION | NEUTRAL | UNKNOWN
 *   dark_pool.aggregate.flow_strength        0-100
 *   options_flow.bias                        STRONGLY_BULLISH | BULLISH | NEUTRAL | BEARISH | STRONGLY_BEARISH | NO_DATA
 *   combined_signal                          STRONG_BULLISH_CONFLUENCE | STRONG_BEARISH_CONFLUENCE | DP_*_ONLY | OPTIONS_*_ONLY | NO_SIGNAL
 *   analysis.signal                          STRONG | MODERATE | WEAK | NONE | ERROR
 */

export type FlowDirection = "BULLISH" | "NEUTRAL" | "BEARISH";

export type FlowStrengthLabel = "STRONG" | "MODERATE" | "WEAK" | "NONE";

export type FlowSignalVerdict = {
  direction: FlowDirection;
  /** 0-100; magnitude of conviction. 0 = no signal, 100 = max strength. */
  confidence: number;
  /** Human label paired with the strength score. */
  strength: FlowStrengthLabel;
  /** Short rationale that can be rendered as supporting text. */
  rationale: string;
};

type FlowReportLike = {
  dark_pool?: {
    aggregate?: {
      flow_direction?: string | null;
      flow_strength?: number | null;
      dp_buy_ratio?: number | null;
    } | null;
  } | null;
  options_flow?: {
    bias?: string | null;
    put_call_ratio?: number | null;
    /** @deprecated Compatibility for flow reports cached before the P/C migration. */
    call_put_ratio?: number | null;
  } | null;
  combined_signal?: string | null;
  analysis?: {
    signal?: string | null;
    score?: number | null;
  } | null;
};

const BULLISH_OPTION_BIAS = new Set(["STRONGLY_BULLISH", "BULLISH"]);
const BEARISH_OPTION_BIAS = new Set(["STRONGLY_BEARISH", "BEARISH"]);

/**
 * Classify the directional bias of a flow report. The function never throws —
 * partial / malformed reports return a low-confidence NEUTRAL verdict.
 */
export function classifyFlowSignal(report: FlowReportLike | null | undefined): FlowSignalVerdict {
  if (!report) {
    return neutralVerdict("No flow data available");
  }

  const dpDirection = String(report.dark_pool?.aggregate?.flow_direction ?? "UNKNOWN");
  const dpStrength = clampStrength(report.dark_pool?.aggregate?.flow_strength);
  const optionsBias = String(report.options_flow?.bias ?? "NO_DATA");
  const combined = String(report.combined_signal ?? "NO_SIGNAL");
  const analysisSignal = String(report.analysis?.signal ?? "NONE");

  const dpDirectionLabel = mapDarkPoolDirection(dpDirection);
  const optionsDirectionLabel = mapOptionsBias(optionsBias);

  const direction = resolveDirection(dpDirectionLabel, optionsDirectionLabel, combined);
  const confidence = computeConfidence(direction, dpStrength, dpDirectionLabel, optionsDirectionLabel);
  const strength = labelForConfidence(confidence, analysisSignal);
  const rationale = buildRationale(direction, dpDirectionLabel, optionsDirectionLabel, dpStrength, combined);

  return { direction, confidence, strength, rationale };
}

function neutralVerdict(rationale: string): FlowSignalVerdict {
  return { direction: "NEUTRAL", confidence: 0, strength: "NONE", rationale };
}

function clampStrength(value: number | null | undefined): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

function mapDarkPoolDirection(direction: string): FlowDirection {
  if (direction === "ACCUMULATION") return "BULLISH";
  if (direction === "DISTRIBUTION") return "BEARISH";
  return "NEUTRAL";
}

function mapOptionsBias(bias: string): FlowDirection {
  if (BULLISH_OPTION_BIAS.has(bias)) return "BULLISH";
  if (BEARISH_OPTION_BIAS.has(bias)) return "BEARISH";
  return "NEUTRAL";
}

function resolveDirection(
  dp: FlowDirection,
  options: FlowDirection,
  combined: string,
): FlowDirection {
  if (combined === "STRONG_BULLISH_CONFLUENCE") return "BULLISH";
  if (combined === "STRONG_BEARISH_CONFLUENCE") return "BEARISH";

  // Dark pool is the primary signal per CLAUDE.md ("Strong flow overrides …").
  if (dp !== "NEUTRAL") return dp;
  if (options !== "NEUTRAL") return options;
  return "NEUTRAL";
}

function computeConfidence(
  direction: FlowDirection,
  dpStrength: number,
  dpDirection: FlowDirection,
  optionsDirection: FlowDirection,
): number {
  if (direction === "NEUTRAL") return 0;

  let confidence = dpDirection === direction ? dpStrength : 0;
  if (optionsDirection === direction) {
    confidence = Math.min(100, confidence + 15);
  } else if (optionsDirection !== "NEUTRAL") {
    // Options flow disagrees → cap conviction.
    confidence = Math.max(0, confidence - 10);
  }
  return Math.round(confidence);
}

function labelForConfidence(confidence: number, analysisSignal: string): FlowStrengthLabel {
  if (analysisSignal === "STRONG" && confidence >= 50) return "STRONG";
  if (confidence >= 60) return "STRONG";
  if (confidence >= 30) return "MODERATE";
  if (confidence > 0) return "WEAK";
  return "NONE";
}

function buildRationale(
  direction: FlowDirection,
  dp: FlowDirection,
  options: FlowDirection,
  dpStrength: number,
  combined: string,
): string {
  if (direction === "NEUTRAL") {
    return "Flow is balanced across dark pool prints and options activity";
  }

  const directionWord = direction === "BULLISH" ? "bullish" : "bearish";

  if (combined === "STRONG_BULLISH_CONFLUENCE" || combined === "STRONG_BEARISH_CONFLUENCE") {
    return `Dark pool and options flow both point ${directionWord}`;
  }

  if (dp === direction && options === direction) {
    return `Dark pool ${describeStrength(dpStrength)} and options bias align ${directionWord}`;
  }

  if (dp === direction) {
    return `Dark pool prints lean ${directionWord} (${describeStrength(dpStrength)})`;
  }

  return `Options bias leans ${directionWord} without dark pool confirmation`;
}

function describeStrength(strength: number): string {
  if (strength >= 60) return "high conviction";
  if (strength >= 30) return "moderate conviction";
  return "early signal";
}

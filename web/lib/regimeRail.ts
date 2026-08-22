import { vcgRow, gexRow } from "./engineState";
import type { VcgData } from "./useVcg";
import type { GexData } from "./useGex";

/**
 * Grouped registry for the /regime indicator rail ("Regime — Grouped Rail").
 * Single source of truth for tab keys, display labels, and group assignment;
 * RegimePanel derives both the desktop rail and the narrow-viewport strip
 * from it.
 */

export type RegimeTab =
  | "cri" | "vcg" | "gex" | "grg"
  | "breadth" | "bpi" | "margin" | "straddle"
  | "cor" | "vixcor" | "ivrank" | "skew" | "skew2d" | "curve"
  | "cot" | "ats" | "short" | "llm" | "backtest" | "credit";

export type RegimeRailGroup = { label: string; tabs: readonly RegimeTab[] };

export const REGIME_RAIL_GROUPS: readonly RegimeRailGroup[] = [
  { label: "Composite", tabs: ["cri", "grg", "vcg"] },
  { label: "Volatility", tabs: ["vixcor", "ivrank", "skew", "skew2d", "curve", "straddle"] },
  { label: "Positioning", tabs: ["gex", "margin", "credit", "cot", "short", "ats"] },
  { label: "Breadth & sentiment", tabs: ["breadth", "bpi", "cor"] },
  { label: "Models", tabs: ["llm", "backtest"] },
];

export const REGIME_TABS: readonly RegimeTab[] = REGIME_RAIL_GROUPS.flatMap((g) => g.tabs);

export const REGIME_TAB_LABEL: Record<RegimeTab, string> = {
  cri: "CRI",
  grg: "GRG",
  vcg: "VCG",
  vixcor: "VIX-COR",
  ivrank: "IV RANK",
  skew: "SKEW",
  skew2d: "SKEW 2D",
  curve: "CURVE",
  straddle: "STRADDLE",
  gex: "GEX",
  margin: "MARGIN",
  credit: "CREDIT",
  cot: "COT",
  short: "SHORT",
  ats: "ATS",
  breadth: "BREADTH",
  bpi: "BULLISH %",
  cor: "COR",
  llm: "LLM",
  backtest: "BACKTEST",
};

/** Same clarity/severity vocabulary as the dashboard engine rows. */
export type RailTone = "core" | "warn" | "fault" | "neutral";

export type RailStatus = { value: string; tone: RailTone };

export type RailStatuses = Partial<Record<RegimeTab, RailStatus>>;

/** COR1M above this reads as herding risk — the CRI crash-trigger threshold. */
const COR1M_WARN_THRESHOLD = 60;

function criStatus(cri: { score: number; level: string } | null | undefined): RailStatus | null {
  if (!cri || !Number.isFinite(cri.score)) return null;
  const level = cri.level?.toUpperCase() ?? "";
  const tone: RailTone = level === "LOW" ? "core" : level === "ELEVATED" ? "warn" : level ? "fault" : "neutral";
  return { value: String(Math.round(cri.score)), tone };
}

function corStatus(cor1m: number | null | undefined): RailStatus | null {
  if (cor1m == null || !Number.isFinite(cor1m)) return null;
  return { value: cor1m.toFixed(2), tone: cor1m > COR1M_WARN_THRESHOLD ? "warn" : "neutral" };
}

function vcgStatus(data: VcgData | null | undefined): RailStatus | null {
  const interpretation = data?.signal?.interpretation;
  if (!interpretation) return null;
  return { value: interpretation.replace(/_/g, " "), tone: vcgRow(data).tone };
}

function gexStatus(data: GexData | null | undefined): RailStatus | null {
  const direction = data?.bias?.direction;
  if (!direction) return null;
  return { value: direction.replace(/_/g, " "), tone: gexRow(data).tone };
}

/**
 * Derive per-indicator rail readings from whatever payloads are loaded.
 * Missing data yields no entry — the rail renders an honest neutral row,
 * never a fault.
 */
export function buildRailStatuses(inputs: {
  cri?: { score: number; level: string } | null;
  cor1m?: number | null;
  vcg?: VcgData | null;
  gex?: GexData | null;
}): RailStatuses {
  const statuses: RailStatuses = {};
  const cri = criStatus(inputs.cri);
  if (cri) statuses.cri = cri;
  const cor = corStatus(inputs.cor1m);
  if (cor) statuses.cor = cor;
  const vcg = vcgStatus(inputs.vcg);
  if (vcg) statuses.vcg = vcg;
  const gex = gexStatus(inputs.gex);
  if (gex) statuses.gex = gex;
  return statuses;
}

export function isFlagged(status: RailStatus | undefined): boolean {
  return status != null && (status.tone === "warn" || status.tone === "fault");
}

export function elevatedCount(statuses: RailStatuses): number {
  return REGIME_TABS.filter((tab) => isFlagged(statuses[tab])).length;
}

export type AiPane = "demand" | "compute" | "delivery" | "finance";
export interface AiMetric {
  id: string; label: string; value: number | null; unit: string;
  period_start: string; period_end: string; published_at: string | null; fetched_at: string;
  source_id: string; source_url: string; measurement: string; methodology_version: string;
  cohort_version: string; lineage_group: string; raw_hash: string; metadata: Record<string, unknown>;
}
export interface AiHistoryPoint { date: string; value: number; unit: string; series_id: string; label: string; source_id: string }
export interface AiIndicator {
  id: string; title: string; pane: AiPane; priority: string; status: string; reason: string;
  methodology: string; tickers: string[]; source_ids: string[]; metrics: AiMetric[]; history: AiHistoryPoint[];
}
export interface AiSource {
  id: string; name: string; url: string; status: string; reason: string; checked_at: string | null;
  cadence: string; license: string; lineage_group: string; observation_count: number;
  observed_from: string | null; observed_through: string | null;
}
export interface AiSnapshot {
  version: 1; generated_at: string; as_of: string; indicators: AiIndicator[]; sources: AiSource[];
  shadow: { status: "experimental"; state: "insufficient_evidence" | "watch" | "clear"; reason: string; evaluated_at: string; eligible_weeks: number };
}
export const AI_PANES: { id: AiPane; title: string; question: string; note: string }[] = [
  { id: "demand", title: "Demand", question: "Is useful activity converting into demand?", note: "Routed tokens measure publisher activity. Shares describe mix, not market size or actual spend." },
  { id: "compute", title: "Compute", question: "What does comparable compute cost?", note: "Read asking prices alongside matched supply. Falling prices alone do not establish excess capacity." },
  { id: "delivery", title: "Delivery", question: "Is promised capacity becoming productive?", note: "Orders, backlog, revenue and energized capacity are separate stages. Grid load is not AI consumption." },
  { id: "finance", title: "Finance", question: "Can cash generation fund the buildout?", note: "Compare synchronized fiscal periods. Cloud revenue and company capex are not AI-only measurements." },
];
export function sourceHref(url: string): string | undefined {
  try { const parsed = new URL(url); return ["https:", "http:"].includes(parsed.protocol) && !parsed.username && !parsed.password ? parsed.href : undefined; } catch { return undefined; }
}
export function aiNumber(value: number | null): string {
  return value == null || !Number.isFinite(value) ? "Unavailable" : new Intl.NumberFormat("en-US", { maximumFractionDigits: 3, notation: Math.abs(value) >= 1e6 ? "compact" : "standard" }).format(value);
}
export function aiDate(value: string | null | undefined): string {
  if (!value) return "Not disclosed";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Not disclosed" : new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(date);
}
export function aiObservationDate(value: string | null | undefined): string {
  if (!value || !value.includes("T") || !Number.isFinite(Date.parse(value))) return aiDate(value);
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC", timeZoneName: "short" }).format(new Date(value));
}
export function historyGroups(history: AiHistoryPoint[]): AiHistoryPoint[][] {
  const groups = new Map<string, AiHistoryPoint[]>();
  for (const point of history) {
    if (!Number.isFinite(point.value) || !Number.isFinite(Date.parse(point.date))) continue;
    const key = `${point.source_id}\u0000${point.series_id}\u0000${point.unit}`;
    const group = groups.get(key) ?? []; group.push(point); groups.set(key, group);
  }
  return [...groups.values()].map(points => points.sort((a, b) => Date.parse(a.date) - Date.parse(b.date)));
}
export const AI_RESEARCH_MAPPING: Record<string, { role: string; pane: AiPane; source: string }> = {
  NVDA: { role: "Compute supply", pane: "delivery", source: "https://investor.nvidia.com/" },
  AMD: { role: "Compute supply", pane: "compute", source: "https://ir.amd.com/" },
  MSFT: { role: "Cloud monetization", pane: "finance", source: "https://www.microsoft.com/en-us/Investor/" },
  AMZN: { role: "Cloud monetization", pane: "finance", source: "https://ir.aboutamazon.com/" },
  GOOGL: { role: "Cloud monetization", pane: "finance", source: "https://abc.xyz/investor/" },
  ORCL: { role: "Cloud monetization", pane: "finance", source: "https://investor.oracle.com/" },
  DELL: { role: "Server delivery", pane: "delivery", source: "https://investors.delltechnologies.com/" },
  SMCI: { role: "Server delivery", pane: "delivery", source: "https://ir.supermicro.com/" },
  VRT: { role: "Power equipment", pane: "delivery", source: "https://investors.vertiv.com/" },
  ETN: { role: "Power equipment", pane: "delivery", source: "https://www.eaton.com/us/en-us/company/investor-relations.html" },
};

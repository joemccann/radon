import { aiNumber, type AiMetric } from "./aiInfrastructure";

type MetricIdentity = { label: string; source_id: string; unit: string; id?: string; series_id?: string };
const activity: Record<string, { label: string; description: string; percent?: boolean }> = {
  other: { label: "Tokens from unlisted models", description: "Tokens in OpenRouter's combined category for models not listed individually. This is reported activity, not missing data. Free versus paid usage is not disclosed for this group." },
  other_share: { label: "Unlisted models' share of tokens", description: "Tokens from models not listed individually divided by all reported OpenRouter tokens, shown as a percentage. This describes OpenRouter's reported traffic mix, not the entire AI market.", percent: true },
  total_tokens: { label: "Total routed tokens", description: "All reported OpenRouter tokens for the observation day, including individually listed models and the combined unlisted-model category. Private traffic is excluded; tokens do not measure revenue." },
  mean_7d: { label: "Average daily tokens · 7 days", description: "Total reported tokens across the latest seven complete days divided by seven. This smooths daily variation without treating missing days as zero." },
  sum_28d: { label: "Total tokens · 28 days", description: "Sum of reported OpenRouter tokens over the latest 28 complete days. It measures activity on this publisher, not total AI usage." },
  growth_28d: { label: "Token growth · 28 days", description: "The latest 28-day token total divided by the preceding 28-day total, minus one, shown as a percentage. Requires 56 consecutive complete days; A positive value means activity increased versus the previous period.", percent: true },
  visible_nonfree_tokens: { label: "Tokens from listed non-free models", description: "Tokens from individually listed OpenRouter models excluding variants marked :free. The unlisted-model group is excluded. A non-free listing does not establish actual payment or revenue." },
};
function definition(metric: MetricIdentity) {
  if (metric.source_id !== "openrouter") return undefined;
  const key = (metric.id ?? metric.series_id ?? metric.label.replaceAll(" ", "_")).split(":")[0];
  return activity[key];
}
function modelLabel(metric: MetricIdentity): string | undefined {
  if (metric.source_id !== "openrouter" || !metric.label.includes("/")) return undefined;
  const [provider, model] = metric.label.split("/");
  if (provider !== "anthropic" || !model.startsWith("claude-")) return undefined;
  const match = model.match(/^claude-(\d+(?:\.\d+)?)-(sonnet|opus|haiku)(?:-(\d{8}))?(:free)?$/);
  if (!match) return undefined;
  const [, version, family, date, free] = match;
  return `Claude ${family[0].toUpperCase()}${family.slice(1)} ${version}${date ? ` (${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6)})` : ""}${free ? " · free variant" : ""}`;
}
export function aiMetricLabel(metric: MetricIdentity): string {
  return definition(metric)?.label ?? modelLabel(metric) ?? metric.label.replaceAll("_", " ");
}
export function aiMetricDescription(metric: AiMetric): string {
  const known = definition(metric);
  if (known) return known.description;
  if (metric.source_id === "openrouter" && metric.unit === "tokens" && metric.label.includes("/")) {
    return `Reported OpenRouter tokens for ${aiMetricLabel(metric)} during the observation period. Model identifier: ${metric.label}. This is publisher-visible usage, not market-wide usage or revenue.`;
  }
  const description = metric.metadata?.description;
  return typeof description === "string" && description.trim()
    ? description
    : `${aiMetricLabel(metric)}, reported in ${metric.unit} for the stated observation period. ${metric.measurement === "derived" ? "Calculated from source observations." : "Observed in the source dataset."} Open Sources & methodology for coverage, exclusions and the original record.`;
}
export function aiMetricDisplay(metric: MetricIdentity & { value: number | null }): { value: string; unit: string } {
  const percent = metric.unit === "ratio" && definition(metric)?.percent;
  return { value: aiNumber(percent && metric.value != null ? metric.value * 100 : metric.value), unit: percent ? "%" : metric.unit };
}
/** Display-only conversion; transport identities and raw observations stay intact. */
export function aiMetricChartPoint<T extends MetricIdentity & { value: number }>(point: T): T {
  const percent = point.unit === "ratio" && definition(point)?.percent;
  return { ...point, label: aiMetricLabel(point), value: percent ? point.value * 100 : point.value, unit: percent ? "%" : point.unit };
}

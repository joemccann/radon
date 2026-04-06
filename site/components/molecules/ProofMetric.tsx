import { EdgeTrace } from "@/components/atoms/EdgeTrace";
import { TelemetryLabel } from "@/components/atoms/TelemetryLabel";
import type { ProofItem } from "@/lib/landing-content";

type LegacyMetric = {
  label: string;
  value: string;
  meta?: string;
  note?: string;
};

type ProofMetricProps = {
  item?: ProofItem;
  metric?: LegacyMetric;
  compact?: boolean;
};

const toneMap = {
  core: "text-accent",
  strong: "text-signal-strong",
  neutral: "text-primary",
};

export function ProofMetric({ item, metric, compact }: ProofMetricProps) {
  const normalizedItem: ProofItem = item ?? {
    label: metric?.label ?? "Metric",
    value: metric?.value ?? "",
    detail: metric?.note ?? metric?.meta ?? "",
    tone: "neutral",
  };

  const tone = normalizedItem.tone ?? "neutral";

  return (
    <div className="relative border border-grid bg-panel px-4 py-4">
      {!compact && <EdgeTrace tone={tone === "neutral" ? "core" : tone} />}
      <TelemetryLabel tone={compact ? "muted" : undefined}>{normalizedItem.label}</TelemetryLabel>
      <div className={`mt-3 font-mono leading-[1.05] ${compact ? "text-[18px] text-secondary" : `text-[24px] ${toneMap[tone]}`}`}>
        {normalizedItem.value}
      </div>
      <p className="mt-3 max-w-[26ch] text-sm leading-6 text-secondary">
        {normalizedItem.detail}
      </p>
    </div>
  );
}

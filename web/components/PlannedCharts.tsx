import type { CSSProperties } from "react";

import { AiCreditYieldChart } from "@/components/AiCreditYieldChart";
import { RatesMoveChart } from "@/components/RatesMoveChart";
import { chartsForPost, type ChartPlan } from "@/lib/planCharts";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function formatResearchDate(iso?: string): string | undefined {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso ?? "");
  if (!match) return undefined;
  const month = MONTHS[Number(match[2]) - 1];
  if (!month) return undefined;
  return `${Number(match[3])} ${month} ${match[1]}`;
}

type ChartPost = {
  title: string;
  content?: string;
  images?: string[];
  source?: {
    kind?: string;
    figures?: unknown[];
    charts?: ChartPlan[];
    documentDate?: string;
  };
};

export function PlannedCharts({ plans, date }: { plans: readonly ChartPlan[]; date?: string }) {
  if (!plans.length) return null;
  const label = formatResearchDate(date);
  return (
    <div data-slot="under-body" data-generated-charts="" style={stackStyle}>
      {plans.map((plan, index) => plan.kind === "levels" ? (
        <AiCreditYieldChart
          key={`${plan.kind}-${index}`}
          marks={plan.marks}
          title={plan.title}
          ariaLabel={plan.title}
          dek={plan.dek ?? null}
          axis={plan.axis}
          ticks={plan.ticks}
          reference={plan.reference ?? null}
          source={plan.sourceNote ?? null}
          eyebrow={label ? `Yields · ${label}` : "Yields"}
        />
      ) : (
        <RatesMoveChart
          key={`${plan.kind}-${index}`}
          bars={plan.bars}
          axisMax={plan.axisMax || undefined}
          ticks={plan.ticks}
          probability={plan.probability ?? null}
          readout={plan.readout ?? null}
          title={plan.title}
          barAria={plan.barAria}
          note={plan.note ?? null}
          source={null}
          eyebrow={label ? `Rates · ${label}` : "Rates"}
        />
      ))}
    </div>
  );
}

export function ResearchCharts({ post }: { post: ChartPost }) {
  return <PlannedCharts plans={chartsForPost(post)} date={post.source?.documentDate} />;
}

const stackStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  marginTop: 16,
};

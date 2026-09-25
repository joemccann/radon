import type { CSSProperties } from "react";

import { ResearchChart } from "@/components/ResearchChart";
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
      {label ? <p style={dateStyle}>{label}</p> : null}
      {plans.map((plan, index) => (
        <ResearchChart key={`${plan.kind}-${index}`} plan={plan} />
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

const dateStyle: CSSProperties = {
  margin: 0,
  color: "var(--text-muted)",
  fontFamily: "var(--font-mono)",
  fontSize: 12,
  fontWeight: 500,
  letterSpacing: "0.04em",
  textTransform: "uppercase",
};

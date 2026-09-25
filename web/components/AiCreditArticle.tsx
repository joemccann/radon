import { PlannedCharts } from "@/components/PlannedCharts";
import { planCharts } from "@/lib/planCharts";

const BODY = [
  "Rates are getting into the AI credit story, GS Johnstone notes. The 10-year is back above 5.10% and the 2-year sits at 4.90% after the hot September PMI, oil and the hawkish Fed tone. The desk says the speed is what matters: the 10-year is up ~25 bp in two weeks and ~35 bp over the past month, which is close to where equities usually start to care, especially with the S&P still trading around ~19x.",
  "The clearest stress test is SoftBank. Its ~$11.1bn HY raise to fund the final $10bn OpenAI tranche and refinance bridge debt cleared with strong demand, but at record dollar yields of 8.6-9.75%. That is the tell for AI-linked credit. Earlier in the week, CoreWeave's Virginia data-centre financing was still marketing in the low- to mid-9% area. The desk's read: markets will still fund AI infrastructure and neocloud project finance, even with pre-revenue construction risk. But they are not doing it cheap. Credit investors only want near-10% coupons.",
  "The desk prices a 71% chance of an October hike and about 36 bp of tightening by year-end.",
];

export function AiCreditArticle() {
  return (
    <article style={articleStyle}>
      <p style={kickerStyle}>Goldman Sachs · Research · 24 Sep 2026</p>
      <h1 style={headlineStyle}>SoftBank $11.1bn HY AI raise clears at 8.6-9.75%</h1>
      <div data-slot="body">
        {BODY.map((paragraph) => (
          <p key={paragraph.slice(0, 24)} style={bodyStyle}>
            {paragraph}
          </p>
        ))}
      </div>
      <PlannedCharts plans={planCharts(BODY.join("\n\n"))} date="2026-09-24" />
    </article>
  );
}

const articleStyle: React.CSSProperties = {
  boxSizing: "border-box",
  width: 760,
  margin: 0,
  padding: "20px 0 8px",
  background: "var(--bg-canvas)",
  color: "var(--text-primary)",
  fontFamily: "var(--font-sans)",
};

const kickerStyle: React.CSSProperties = {
  margin: "0 20px",
  color: "var(--text-muted)",
  fontFamily: "var(--font-mono)",
  fontSize: 12,
  letterSpacing: "0.04em",
  textTransform: "uppercase",
};

const headlineStyle: React.CSSProperties = {
  margin: "8px 20px 0",
  color: "var(--text-primary)",
  fontFamily: "var(--font-sans)",
  fontSize: 28,
  fontWeight: 600,
  letterSpacing: "-0.01em",
  lineHeight: 1.15,
};

const bodyStyle: React.CSSProperties = {
  margin: "14px 20px 0",
  color: "var(--text-secondary)",
  fontFamily: "var(--font-sans)",
  fontSize: 15,
  lineHeight: 1.5,
};

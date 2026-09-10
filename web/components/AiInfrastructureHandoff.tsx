import Link from "next/link";
import { AI_RESEARCH_MAPPING } from "@/lib/aiInfrastructure";

/** A dated research classification, not a revenue beta or portfolio stress estimate. */
export default function AiInfrastructureHandoff({ ticker }: { ticker: string }) {
  const mapping = AI_RESEARCH_MAPPING[ticker.toUpperCase()];
  if (!mapping) return null;
  return <aside aria-label="AI infrastructure research" style={{ padding: "16px 0", borderBottom: "1px solid var(--line-grid)", fontSize: 14 }}>
    <strong>AI infrastructure · {mapping.role}</strong>
    <p style={{ color: "var(--text-muted)", fontSize: 12, margin: "6px 0" }}>Research classification · September 7, 2026. Exposure magnitude and current revenue sensitivity are unverified.</p>
    <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}><Link href={`/regime/llm?pane=${mapping.pane}`} style={{ minHeight: 44, display: "inline-flex", alignItems: "center", color: "var(--signal-core-text)" }}>Review infrastructure evidence ↗</Link><a href={mapping.source} target="_blank" rel="noopener noreferrer" style={{ minHeight: 44, display: "inline-flex", alignItems: "center", color: "var(--signal-core-text)" }}>Issuer disclosures ↗</a></div>
  </aside>;
}

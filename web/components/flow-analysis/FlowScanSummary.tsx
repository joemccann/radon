import styles from "./FlowScanSummary.module.css";

export type FlowCounts = { supports: number; against: number; neutral: number; watch: number };

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

/** One-sentence finding for the flow scan; zero sections collapse into it. */
export function flowFinding({ supports, against }: FlowCounts): string {
  if (supports === 0 && against === 0) return "No position has flow behind or against it.";
  return `${supports} ${plural(supports, "position has flow behind it", "positions have flow behind them")}, ${against} against.`;
}

function scanDetail(counts: FlowCounts, scanned: number, scannedAt: string | null): string {
  const parts = [`${scanned} ${plural(scanned, "position", "positions")} scanned${scannedAt ? ` at ${scannedAt}` : ""}.`];
  if (scanned > 0 && counts.neutral === scanned) parts.push(`All ${scanned} read neutral or low signal.`);
  if (counts.watch === 0) parts.push("Nothing to watch.");
  return parts.join(" ");
}

export default function FlowScanSummary({ counts, scanned, scannedAt }: { counts: FlowCounts; scanned: number; scannedAt: string | null }) {
  const items: Array<[string, number]> = [["Supports", counts.supports], ["Against", counts.against], ["Neutral", counts.neutral], ["Watch", counts.watch]];
  return (
    <section className={styles.summary} aria-labelledby="flow-scan-finding">
      <h2 id="flow-scan-finding" className={styles.finding}>{flowFinding(counts)}</h2>
      <p className={styles.detail}>{scanDetail(counts, scanned, scannedAt)}</p>
      <dl className={styles.counts}>
        {items.map(([label, n]) => <div key={label}><dt>{label}</dt><dd>{n}</dd></div>)}
      </dl>
    </section>
  );
}

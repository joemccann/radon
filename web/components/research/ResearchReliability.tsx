"use client";
import { useState } from "react";
import SortTh from "@/components/SortTh";
import { useSort } from "@/lib/useSort";
import { parseMcpEvaluationReport, type McpEvaluationReport } from "@/lib/researchWorkbench/reliability";
import styles from "./ResearchWorkbench.module.css";
export default function ResearchReliability() {
  const [report, setReport] = useState<McpEvaluationReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  type Query = McpEvaluationReport["queries"][number];
  type Key = "query" | "passed" | "latency_ms";
  const { sorted, sort, toggle } = useSort<Query, Key>(report?.queries ?? [], (row, key) => key === "passed" ? Number(row.passed) : row[key], "latency_ms", "desc");
  return <section className={styles.fact}><h3>MCP evaluation board</h3><p>Import the JSON produced by the MCP golden-query evaluator. Summary values are checked against every query result. Imported reports do not establish their own authenticity.</p>
    <label className={styles.field}>Import MCP evaluator report<input type="file" accept="application/json,.json" onChange={async (event) => {
      const input = event.currentTarget; const file = input.files?.[0]; if (!file) return;
      try { if (file.size > 1_000_000) throw new Error("Evaluation report exceeds 1 MB."); const parsed = parseMcpEvaluationReport(JSON.parse(await file.text())); setReport(parsed); setError(null); }
      catch (reason) { setError(reason instanceof Error ? reason.message : "Report could not be imported."); }
      input.value = "";
    }} /></label>
    {error ? <p role="alert" className={styles.error}>{error}</p> : null}
    {report ? <><p className={styles.meta}>{report.mode === "offline-contracts" ? "Offline fixture contracts" : "Public live probes"} · {report.generated_at} · Operator-imported report</p>
      <p>{report.metrics.passed}/{report.metrics.total} contracts passed · {(report.metrics.contract_accuracy * 100).toFixed(1)}% agreement · p50 {report.metrics.p50_ms.toFixed(2)} ms · p95 {report.metrics.p95_ms.toFixed(2)} ms</p>
      <p className={styles.muted}>{report.mode === "offline-contracts" ? "Latency measures fixture execution, not service performance." : "Public probes do not measure licensed data or operator-book accuracy."} Contract agreement is not financial fact accuracy.</p>
      <div className={styles.tableWrap}><table><thead><tr>{([['query','Golden query'],['passed','Result'],['latency_ms','Latency (ms)']] as const).map(([key,label]) => <SortTh key={key} label={label} sortKey={key} activeKey={sort.key} direction={sort.direction} onToggle={toggle} />)}</tr></thead><tbody>{sorted.map((row) => <tr key={row.query}><td>{row.query}</td><td>{row.passed ? "Passed" : "Failed"}{row.error_class ? <span className={styles.meta}>{row.error_class}</span> : null}</td><td>{row.latency_ms.toFixed(3)}</td></tr>)}</tbody></table></div>
    </> : <p className={styles.muted}>No evaluation report imported. Latency and contract agreement are unavailable.</p>}
  </section>;
}

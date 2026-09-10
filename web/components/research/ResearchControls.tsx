"use client";
import { useEffect, useState } from "react";
import ResearchReliability from "./ResearchReliability";
import SortTh from "@/components/SortTh";
import { useSort } from "@/lib/useSort";
import styles from "./ResearchWorkbench.module.css";
type Policy = { surface: string; roles: string[]; action: string; control: string; evidence: string };
type AuditStream = { status: "available" | "unavailable"; rows: unknown[]; truncated: boolean };
type Report = { generatedAt: string; policy: Policy[]; audit: { orders: AuditStream; assistant: AuditStream }; reliability: { status: string; description: string }; limitations: string[] };
function object(value: unknown): value is Record<string, unknown> { return !!value && typeof value === "object" && !Array.isArray(value); }
function stream(value: unknown): value is AuditStream { return object(value) && (value.status === "available" || value.status === "unavailable") && Array.isArray(value.rows) && typeof value.truncated === "boolean"; }
function validReport(value: unknown): value is Report {
  return object(value) && value.schemaVersion === 1 && typeof value.generatedAt === "string"
    && Array.isArray(value.policy) && value.policy.every((row: unknown) => object(row) && typeof row.surface === "string" && typeof row.action === "string" && typeof row.control === "string" && typeof row.evidence === "string" && Array.isArray(row.roles) && row.roles.every((role: unknown) => typeof role === "string"))
    && object(value.audit) && stream(value.audit.orders) && stream(value.audit.assistant)
    && object(value.reliability) && typeof value.reliability.status === "string" && typeof value.reliability.description === "string"
    && Array.isArray(value.limitations) && value.limitations.every((limit: unknown) => typeof limit === "string");
}
export default function ResearchControls() {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const { sorted, sort, toggle } = useSort<Policy, "surface" | "action" | "control">(report?.policy ?? [], (row, key) => row[key], "surface");
  useEffect(() => {
    const controller = new AbortController(); setError(null);
    void fetch("/api/research/governance", { cache: "no-store", signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error(response.status === 403 ? "This audit requires operator access." : `Audit request failed (${response.status}).`);
      const data: unknown = await response.json();
      if (!validReport(data)) throw new Error("Audit response has an unsupported format.");
      if (!controller.signal.aborted) setReport(data);
    }).catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Audit unavailable."); });
    return () => controller.abort();
  }, [attempt]);
  return <section><h2>Execution controls & evidence</h2><p className={styles.muted}>Research prepares context. Existing authentication, risk checks, and explicit order confirmation govern execution.</p>
    {error ? <p role="alert" className={styles.error}>{error} <button className={styles.button} onClick={() => setAttempt((value) => value + 1)}>Retry audit</button></p> : null}
    {!report && !error ? <p role="status">Loading policy and audit evidence…</p> : null}
    {report ? <><div className={styles.sectionHead}><span className={styles.meta}>Generated {report.generatedAt}</span><a className={styles.button} href="/api/research/governance?download=1" download>Download audit JSON</a></div>
      <div className={styles.tableWrap}><table><thead><tr>{([['surface','Surface / roles'],['action','Action'],['control','Enforced control']] as const).map(([key,label]) => <SortTh key={key} label={label} sortKey={key} activeKey={sort.key} direction={sort.direction} onToggle={toggle} />)}</tr></thead><tbody>{sorted.map((policy) => <tr key={policy.surface}><td>{policy.surface}<span className={styles.meta}>{policy.roles.join(", ")}</span></td><td>{policy.action}</td><td>{policy.control}</td></tr>)}</tbody></table></div>
      <section className={styles.fact}><h3>Audit availability</h3>{Object.entries(report.audit).map(([name, stream]) => <p key={name}>{name === "orders" ? "Order events" : "Assistant turns"}: {stream.status === "available" ? `${stream.rows.length} records${stream.truncated ? ", truncated" : ""}` : "Unavailable"}</p>)}<ul className={styles.muted}>{report.limitations.map((limit) => <li key={limit}>{limit}</li>)}</ul></section>
      <section className={styles.fact}><h3>MCP reliability</h3><p>Operational measurement: {report.reliability.status}</p><p className={styles.muted}>{report.reliability.description}</p><p>Golden queries validate authorization, source attribution, payload shape, and tool boundaries. Live probe results must be measured before a latency or accuracy claim is displayed.</p></section>
    </> : null}
    <ResearchReliability />
  </section>;
}

"use client";
import ErrorToast from "@/components/ErrorToast";
import { useCallback, useEffect, useState } from "react";
import styles from "./ResearchHeldReview.module.css";

type Rule = { id: string; kind: "series_deny" | "publisher_deny" | "doc_type_drop"; key: string; downs: number; ups: number; evidence: number };
type Decision = "approve" | "reject" | "revoke";

function describe(rule: Rule): string {
  if (rule.kind === "series_deny") return `Stop reviewing the series “${rule.key}”`;
  if (rule.kind === "publisher_deny") return `Stop reviewing everything from “${rule.key}”`;
  return `Drop every “${rule.key}” document before review`;
}

/** Triage rules the worker proposes from the operator's votes. Nothing takes effect until approved here. */
export default function ResearchRuleProposals() {
  const [proposed, setProposed] = useState<Rule[]>([]);
  const [approved, setApproved] = useState<Rule[]>([]);
  const [rejected, setRejected] = useState<Rule[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const response = await fetch("/api/newsfeed/research/rules", { cache: "no-store", signal: controller.signal });
        if (!response.ok) return;   // The Held list reports its own outage; an absent rules list is not an error state.
        const body = await response.json() as { proposed?: Rule[]; approved?: Rule[]; rejected?: Rule[] };
        setProposed(Array.isArray(body.proposed) ? body.proposed : []);
        setApproved(Array.isArray(body.approved) ? body.approved : []);
        setRejected(Array.isArray(body.rejected) ? body.rejected : []);
      } catch { /* aborted or offline: render nothing */ }
    })();
    return () => controller.abort();
  }, []);

  const decide = useCallback(async (rule: Rule, decision: Decision) => {
    if (busy) return;
    setBusy(rule.id);
    setError("");
    try {
      const response = await fetch("/api/newsfeed/research/rules", {
        method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store",
        body: JSON.stringify({ id: rule.id, decision }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null) as { error?: string } | null;
        throw new Error(body?.error || "Could not save the decision.");
      }
      setProposed(current => current.filter(item => item.id !== rule.id));
      if (decision === "approve") {
        setRejected(current => current.filter(item => item.id !== rule.id));
        setApproved(current => [rule, ...current.filter(item => item.id !== rule.id)]);
      } else {
        setApproved(current => current.filter(item => item.id !== rule.id));
        setRejected(current => [rule, ...current.filter(item => item.id !== rule.id)]);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save the decision.");
    } finally {
      setBusy("");
    }
  }, [busy]);

  if (!proposed.length && !approved.length && !rejected.length && !error) return null;
  return <section className={styles.rules} aria-label="Triage rules">
    {proposed.length ? <div className={styles.ruleGroup}>
      <h4 className={styles.rulesTitle}>Proposed rules</h4>
      <ul className={styles.ruleList} aria-label="Proposed rules">
        {proposed.map(rule => <li key={rule.id} className={styles.ruleItem}>
          <div className={styles.ruleCopy}>
            <p className={styles.file}>{describe(rule)}</p>
            <p className={styles.note}>{`You rejected ${rule.downs} of its items and approved none.`}</p>
          </div>
          <div className={styles.ruleActions}>
            <button type="button" className={`${styles.ruleButton} ${styles.ruleButtonPrimary}`} disabled={busy === rule.id} onClick={() => decide(rule, "approve")}>Approve rule</button>
            <button type="button" className={styles.ruleButton} disabled={busy === rule.id} onClick={() => decide(rule, "reject")}>Reject</button>
          </div>
        </li>)}
      </ul>
    </div> : null}
    {approved.length ? <div className={styles.ruleGroup}>
      <h4 className={styles.rulesTitle}>Active rules</h4>
      <ul className={styles.ruleList} aria-label="Active rules">
        {approved.map(rule => <li key={rule.id} className={`${styles.ruleItem} ${styles.ruleItemActive}`}>
          <p className={styles.file}>{describe(rule)}</p>
          <div className={styles.ruleActions}>
            <button type="button" className={styles.ruleButton} disabled={busy === rule.id} onClick={() => decide(rule, "revoke")}>Revoke</button>
          </div>
        </li>)}
      </ul>
    </div> : null}
    {rejected.length ? <div className={styles.ruleGroup}>
      <h4 className={styles.rulesTitle}>Rejected rules</h4>
      <ul className={styles.ruleList} aria-label="Rejected rules">
        {rejected.map(rule => <li key={rule.id} className={`${styles.ruleItem} ${styles.ruleItemRejected}`}>
          <div className={styles.ruleCopy}>
            <p className={styles.file}>{describe(rule)}</p>
            <p className={styles.note}>Rejected. Approve to make it active.</p>
          </div>
          <div className={styles.ruleActions}>
            <button type="button" className={`${styles.ruleButton} ${styles.ruleButtonPrimary}`} disabled={busy === rule.id} onClick={() => decide(rule, "approve")}>Approve rule</button>
          </div>
        </li>)}
      </ul>
    </div> : null}
    {error ? <ErrorToast message={error} /> : null}
  </section>;
}

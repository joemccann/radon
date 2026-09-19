"use client";
import ErrorToast from "@/components/ErrorToast";
import { useCallback, useEffect, useState } from "react";
import type { HeldDocument } from "@/lib/researchFeedback";
import { reasonCodeLabel } from "@/lib/researchReasonCodes";
import ResearchFeedback from "./ResearchFeedback";
import styles from "./ResearchHeldReview.module.css";

/** A small daily sample of documents the intake held or dropped, so the operator can label what it got wrong. */
export default function ResearchHeldReview() {
  const [items, setItems] = useState<HeldDocument[] | null>(null);
  const [pending, setPending] = useState(0);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError("");
    (async () => {
      try {
        const response = await fetch("/api/newsfeed/research/held", { cache: "no-store", signal: controller.signal });
        const body = await response.json().catch(() => null) as { items?: HeldDocument[]; pending?: number; error?: string } | null;
        if (!response.ok) throw new Error(body?.error || "Could not load held research.");
        setItems(Array.isArray(body?.items) ? body.items : []);
        setPending(typeof body?.pending === "number" ? body.pending : 0);
      } catch (caught) {
        if (controller.signal.aborted) return;
        setItems(current => current ?? []);
        setError(caught instanceof Error ? caught.message : "Could not load held research.");
      }
    })();
    return () => controller.abort();
  }, [attempt]);

  const remove = useCallback((workKey: string) => {
    setItems(current => (current ?? []).filter(item => item.workKey !== workKey));
    setPending(current => Math.max(0, current - 1));
  }, []);

  return <div className={styles.root}>
    {items === null ? <p className={styles.note} role="status">Loading held research…</p> : null}
    {items && items.length === 0 && !error ? <p className={styles.note}>Nothing held is waiting for review.</p> : null}
    {items && items.length > 0 ? <>
      <p className={styles.note}>{`${items.length} of ${pending} awaiting review`}</p>
      <ul className={styles.list}>
        {items.map(item => <li key={item.workKey} className={styles.item}>
          <p className={styles.meta}>{`${item.publisher} · ${item.folderDate} · ${item.outcome === "dropped" ? "Dropped before review" : "Held"}`}</p>
          <h4 className={styles.file}>{item.fileName}</h4>
          <ul className={styles.codes} aria-label="Reasons">
            {item.reasonCodes.map(code => <li key={code} className={styles.code}>{reasonCodeLabel(code)}</li>)}
          </ul>
          {item.drafts.map((draft, index) => <details key={index} className={styles.draft}>
            <summary><span>{draft.title || "Untitled draft"}</span></summary>
            <p className={styles.draftReason}>{reasonCodeLabel(draft.held)}{draft.detail ? <>: <span className={styles.detail}>{draft.detail}</span></> : null}</p>
            {draft.content ? <p className={styles.draftBody}>{draft.content}</p> : null}
          </details>)}
          <ResearchFeedback workKey={item.workKey} onHidden={remove} />
        </li>)}
      </ul>
    </> : null}
    {error ? <ErrorToast message={error} onRetry={() => setAttempt(value => value + 1)} /> : null}
  </div>;
}

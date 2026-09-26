"use client";

import { useEffect, useRef, useState } from "react";
import type { AdminAttentionCondition } from "@/lib/adminAttention";
import styles from "./adminActionQueue.module.css";

type QueueCondition = AdminAttentionCondition & { resolved?: boolean };

type AdminAttentionQueueProps = {
  conditions: AdminAttentionCondition[];
  loading?: boolean;
  now: number;
  onReview: (action: AdminAttentionCondition["action"]) => void;
  primaryActionRef?: (element: HTMLDivElement | null) => void;
};

const REVIEW_LABELS: Record<AdminAttentionCondition["action"], string> = {
  gateway: "Review gateway",
  services: "Review services",
  writers: "Review writers",
  refresh: "Refresh status",
  reliability: "Review probe",
};

export function observationAge(value: string | number | null | undefined, now: number): string {
  const timestamp = typeof value === "number" ? value : value ? Date.parse(value) : NaN;
  if (!Number.isFinite(timestamp)) return "Observation time unknown";
  const seconds = Math.max(0, Math.floor((now - timestamp) / 1000));
  if (seconds < 5) return "Observed just now";
  if (seconds < 60) return `Observed ${seconds}s ago`;
  if (seconds < 3_600) return `Observed ${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86_400) return `Observed ${Math.floor(seconds / 3_600)}h ago`;
  return `Observed ${Math.floor(seconds / 86_400)}d ago`;
}

/** The queue owns presentation only. Mutations remain in the existing controls. */
export default function AdminAttentionQueue({ conditions, loading = false, now, onReview, primaryActionRef }: AdminAttentionQueueProps) {
  const queueRef = useRef<HTMLElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [pointerInside, setPointerInside] = useState(false);
  const [focusInside, setFocusInside] = useState(false);
  const [ordered, setOrdered] = useState<QueueCondition[]>(conditions);
  const interacting = pointerInside || focusInside;

  // Polls update observations without moving a focused or hovered control.
  // New conditions are appended during interaction; priority order resumes
  // when the operator leaves the queue. Resolved controls disable immediately.
  useEffect(() => {
    setOrdered(previous => {
      const focusedPortal = queueRef.current?.contains(document.activeElement) ?? false;
      if (!interacting && !focusedPortal && !queueRef.current?.matches(":hover")) return conditions;
      const current = new Map(conditions.map(condition => [condition.id, condition]));
      const seen = new Set(previous.map(condition => condition.id));
      return [
        ...previous.map(condition => current.get(condition.id) ?? { ...condition, resolved: true }),
        ...conditions.filter(condition => !seen.has(condition.id)),
      ];
    });
  }, [conditions, interacting]);

  const currentIds = new Set(conditions.map(condition => condition.id));
  const shown = expanded ? ordered : ordered.slice(0, 4);
  return (
    <section
      className={styles.queue}
      ref={queueRef}
      aria-labelledby="admin-attention-heading"
      data-testid="admin-attention-queue"
      onPointerEnter={() => setPointerInside(true)}
      onPointerLeave={() => setPointerInside(false)}
      onFocusCapture={() => setFocusInside(true)}
      onBlurCapture={event => {
        if (!event.currentTarget.contains(event.relatedTarget)) setFocusInside(false);
      }}
    >
      <header className={styles.sectionHeader}>
        <h2 id="admin-attention-heading">Needs attention</h2>
        <span className={styles.count}>{loading ? "Checking" : `${conditions.length} ${conditions.length === 1 ? "condition" : "conditions"}`}</span>
      </header>
      {loading && conditions.length === 0 ? (
        <div className={styles.empty} data-testid="admin-attention-loading" role="status">
          <h3>Checking current observations</h3>
          <p>Waiting for broker, service and writer status.</p>
        </div>
      ) : shown.length === 0 ? (
        <div className={styles.empty} data-testid="admin-attention-empty">
          <span className={styles.status} data-tone="positive">No action needed</span>
          <h3>No outstanding conditions observed</h3>
          <p>Broker, services and scheduled writers are within their observed operating conditions.</p>
          <button type="button" className={styles.reviewButton} onClick={() => onReview("services")}>Inspect all services</button>
        </div>
      ) : (
        <ol className={styles.queueList}>
          {shown.map((condition, index) => {
            const resolved = condition.resolved || !currentIds.has(condition.id);
            const primary = index === 0;
            return (
              <li key={condition.id} className={primary ? styles.primaryCondition : styles.condition} data-tone={resolved ? "neutral" : condition.tone} data-priority={primary ? "primary" : "secondary"} data-testid={`admin-attention-${condition.id}`}>
                <div className={styles.conditionContent}>
                  <span className={styles.status} data-tone={resolved ? "neutral" : condition.tone}>{resolved ? "No longer observed" : condition.source === "health" ? "Broker" : condition.source === "services" ? "Services" : "Telemetry"}</span>
                  <h3>{condition.title}</h3>
                  <p>{resolved ? "This condition cleared in the latest observation." : condition.detail}</p>
                  {primary && !resolved && condition.action === "gateway" && primaryActionRef ? (
                    <div className={styles.primaryActions} ref={primaryActionRef} />
                  ) : (
                    <button type="button" className={`${styles.reviewButton} ${primary ? styles.primaryButton : ""}`} disabled={resolved} onClick={() => onReview(condition.action)}>
                      {resolved ? "Resolved" : REVIEW_LABELS[condition.action]}
                    </button>
                  )}
                  <span className={styles.observation}>{observationAge(condition.observedAt, now)}</span>
                </div>
              </li>
            );
          })}
        </ol>
      )}
      {ordered.length > 4 && (
        <button type="button" className={styles.showAll} aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>
          {expanded ? "Show fewer conditions" : `Show all ${ordered.length} conditions`}
        </button>
      )}
    </section>
  );
}

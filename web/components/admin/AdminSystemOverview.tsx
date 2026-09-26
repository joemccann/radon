"use client";

import { ADMIN_OBSERVATION_STALE_MS, isAdminObservationCurrent, type AdminAttentionSources } from "@/lib/adminAttention";
import { unitVerdict } from "@/lib/adminFormat";
import { externalProbeSummary, freshnessSummary, ibAuthSummary, livenessSummary } from "@/lib/adminReliability";
import { getMarketStateFromDate, getServiceCategory } from "@/lib/serviceHealthWindows";
import type { AdminHealthPayload, EdgeHealthStatus, ServicesListResponse } from "@/lib/adminTypes";
import { observationAge } from "./AdminAttentionQueue";
import styles from "./adminActionQueue.module.css";

type AdminSystemOverviewProps = {
  health: AdminHealthPayload | null;
  services: ServicesListResponse | null;
  edge: (EdgeHealthStatus & { reachable?: boolean }) | null;
  sources: AdminAttentionSources;
  now: number;
};

type OverviewRow = { id: string; label: string; value: string; detail: string; tone: string };

/** Current source observations only; historical SLOs live in inspection. */
export default function AdminSystemOverview({ health, services, edge, sources, now }: AdminSystemOverviewProps) {
  const healthCurrent = isAdminObservationCurrent(sources.health, now) && health !== null;
  const servicesCurrent = isAdminObservationCurrent(sources.services, now) && services !== null;
  const edgeGeneratedAt = edge?.generated_at ? Date.parse(edge.generated_at) : null;
  const edgeCurrent = isAdminObservationCurrent(sources.edge, now) && edge?.reachable !== false && edge !== null &&
    (edgeGeneratedAt === null || (Number.isFinite(edgeGeneratedAt) && now - edgeGeneratedAt <= ADMIN_OBSERVATION_STALE_MS));
  const auth = ibAuthSummary(health);
  const pool = Object.values(health?.ib_pool ?? {});
  const disconnected = pool.filter(role => !role.connected).length;
  const gateway = health?.ib_gateway;
  const connectionUnavailable = gateway?.upstream_dead || gateway?.auth_state === "unreachable" ||
    (gateway?.port_listening === false && gateway.auth_state !== "remote");
  const poolUnknown = gateway?.auth_state === "authenticated" && pool.length === 0;
  const poolIncomplete = gateway?.auth_state === "authenticated" && disconnected > 0;
  const brokerValue = connectionUnavailable ? "Connection unavailable" : poolUnknown ? "API clients unconfirmed"
    : poolIncomplete ? `${disconnected} / ${pool.length} clients disconnected` : auth.label;
  const brokerTone = connectionUnavailable ? "negative" : poolUnknown || poolIncomplete ? "warning" : auth.tone;
  const liveness = livenessSummary(services?.units ?? []);
  const unknownUnits = (services?.units ?? []).filter(unit => unitVerdict(unit).label === "Unknown").length;
  const writers = edge?.service_health?.rows ?? [];
  const freshness = freshnessSummary(writers, getMarketStateFromDate(new Date(now)), now);
  const errors = writers.filter(row => getServiceCategory(row.service) === "scheduled" && row.state === "error").length;
  const warnings = writers.filter(row => getServiceCategory(row.service) === "scheduled" && (row.state === "warn" || row.state === "warning")).length;
  const unknownWriters = writers.filter(row => getServiceCategory(row.service) === "scheduled" && (!row.state || row.state === "unknown")).length;
  const probe = externalProbeSummary(edge?.external_probe, now);
  const unavailable = (source: keyof AdminAttentionSources) => sources[source].loading && sources[source].observedAt === null ? "Checking" : sources[source].observedAt === null ? "Unknown" : "Last known";
  const rows: OverviewRow[] = [
    { id: "broker", label: "Broker session", value: healthCurrent ? brokerValue : unavailable("health"), tone: healthCurrent ? brokerTone : "neutral", detail: observationAge(sources.health.observedAt, now) },
    { id: "services", label: "Service liveness", value: servicesCurrent && liveness.total ? `${liveness.ok} / ${liveness.total} OK${unknownUnits ? ` · ${unknownUnits} unknown` : ""}` : servicesCurrent ? "No observed units" : unavailable("services"), tone: servicesCurrent && liveness.total ? liveness.ok === liveness.total && !unknownUnits ? "positive" : "warning" : "neutral", detail: observationAge(sources.services.observedAt, now) },
    { id: "writers", label: "Scheduled freshness", value: !edgeCurrent || edge?.service_health?.state !== "ok" ? unavailable("edge") : freshness.total ? `${freshness.stale} overdue${errors ? ` · ${errors} failed` : ""}${warnings ? ` · ${warnings} warning` : ""}${unknownWriters ? ` · ${unknownWriters} unknown` : ""}` : "No scheduled observations", tone: edgeCurrent && edge?.service_health?.state === "ok" && freshness.total ? freshness.stale || errors || warnings || unknownWriters ? "warning" : "positive" : "neutral", detail: edgeCurrent && freshness.total ? `${freshness.total} scheduled writers · ${observationAge(sources.edge.observedAt, now).toLowerCase()}` : observationAge(sources.edge.observedAt, now) },
    { id: "probe", label: "External probe", value: !edgeCurrent ? unavailable("edge") : probe.state === "healthy" ? "Passed" : probe.state === "down" ? "Failed" : probe.state === "stale" ? "Stale" : "Unknown", tone: edgeCurrent ? probe.state === "healthy" ? "positive" : probe.state === "down" ? "negative" : probe.state === "stale" ? "warning" : "neutral" : "neutral", detail: observationAge(edge?.external_probe?.checked_at, now) },
  ];
  return (
    <section className={styles.overview} aria-labelledby="admin-overview-heading" data-testid="admin-system-overview">
      <h2 id="admin-overview-heading">System overview</h2>
      <dl className={styles.overviewList}>
        {rows.map(row => (
          <div key={row.id} className={styles.overviewRow} data-testid={`overview-${row.id}`}>
            <dt>{row.label}</dt>
            <dd><span className={styles.status} data-tone={row.tone}>{row.value}</span><span className={styles.observation}>{row.detail}</span></dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

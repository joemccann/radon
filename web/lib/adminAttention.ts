/** Pure projection of observed operator conditions. Actions only identify existing controls. */
import { unitKind, unitVerdict } from "./adminFormat";
import { externalProbeSummary, ibAuthSummary } from "./adminReliability";
import { getMarketStateFromDate, getServiceCategory, isStale } from "./serviceHealthWindows";
import type { AdminHealthPayload, EdgeHealthStatus, ServiceHealthRow, ServicesListResponse } from "./adminTypes";

export type AdminAttentionSource = "health" | "services" | "edge";
export type AdminObservationState = {
  /** Receipt time of the last successful response, never the last request attempt. */
  observedAt: number | null;
  loading: boolean;
  error?: boolean;
};
export type AdminAttentionSources = Record<AdminAttentionSource, AdminObservationState>;
export type AdminAttentionCondition = {
  id: string;
  title: string;
  detail: string;
  tone: "warning" | "negative" | "neutral";
  source: AdminAttentionSource;
  observedAt: string | null;
  action: "gateway" | "services" | "writers" | "reliability" | "refresh";
  subject?: string;
};
export type AdminAttentionInput = {
  health: AdminHealthPayload | null;
  services: ServicesListResponse | null;
  edge: EdgeHealthStatus | null;
  sources: AdminAttentionSources;
  now: number;
};

/** Six missed 5-second polls mean that current operational state is unconfirmed. */
export const ADMIN_OBSERVATION_STALE_MS = 30_000;
export function isAdminObservationCurrent(source: AdminObservationState, now: number): boolean {
  return !source.error && source.observedAt !== null && Number.isFinite(source.observedAt)
    && source.observedAt <= now + 5_000 && now - source.observedAt <= ADMIN_OBSERVATION_STALE_MS;
}

function iso(value: number | string | null | undefined): string | null {
  const time = typeof value === "string" ? Date.parse(value) : value;
  return time != null && Number.isFinite(time) ? new Date(time).toISOString() : null;
}

const SOURCE_LABELS: Record<AdminAttentionSource, string> = {
  health: "Broker health", services: "Service inventory", edge: "Writer and probe observations",
};

/**
 * Priority is navigation order, not an incident severity or a causal assertion:
 * broker and daemon failures, loss of critical visibility, writer results,
 * overdue scheduled data, then independent external-probe visibility.
 * Conditions sharing a subject retain one stable key across polling updates.
 */
export function deriveAdminAttention(input: AdminAttentionInput): AdminAttentionCondition[] {
  const { health, services, edge, sources, now } = input;
  const conditions = new Map<string, AdminAttentionCondition & { priority: number }>();
  const current = {
    health: isAdminObservationCurrent(sources.health, now),
    services: isAdminObservationCurrent(sources.services, now),
    edge: isAdminObservationCurrent(sources.edge, now),
  };
  if (edge?.generated_at && (!iso(edge.generated_at) || now - Date.parse(edge.generated_at) > ADMIN_OBSERVATION_STALE_MS)) {
    current.edge = false;
  }
  const add = (priority: number, condition: AdminAttentionCondition) => {
    conditions.set(condition.id, { ...condition, priority: !current[condition.source] && !condition.id.startsWith("source:") ? Math.max(40, priority) : priority });
  };
  const observation = (source: AdminAttentionSource) => iso(sources[source].observedAt);
  const detail = (source: AdminAttentionSource, text: string) => current[source]
    ? text : `Last recorded observation. ${text} Refresh to confirm the current state.`;

  for (const source of ["health", "services", "edge"] as const) {
    const data = input[source];
    if ((data === null || !current[source]) && !(sources[source].loading && data === null)) {
      add(15, {
        id: `source:${source}`, title: `${SOURCE_LABELS[source]} unconfirmed`,
        detail: data === null ? "No observation is available. Refresh to check this source."
          : "Showing the last received values. Refresh to confirm the current state.",
        tone: "warning", source, observedAt: observation(source), action: "refresh",
      });
    }
  }

  if (health?.ib_gateway) {
    const gateway = health.ib_gateway;
    const auth = ibAuthSummary(health);
    const disconnected = Object.entries(health.ib_pool ?? {}).filter(([, role]) => !role.connected);
    if (gateway.auth_state === "awaiting_2fa") {
      add(0, { id: "broker:auth", title: "Broker authentication needs attention",
        detail: detail("health", "IB Gateway is awaiting two-factor authentication. Open recovery controls to review the pending push."),
        tone: "warning", source: "health", observedAt: observation("health"), action: "gateway" });
    } else if (gateway.auth_state === "unreachable" || gateway.upstream_dead || !gateway.port_listening && gateway.auth_state !== "remote") {
      add(0, { id: "broker:auth", title: "Broker connection unavailable",
        detail: detail("health", "The gateway reports an unavailable broker connection. Review gateway state and recovery controls."),
        tone: "negative", source: "health", observedAt: observation("health"), action: "gateway" });
    } else if (gateway.auth_state === "unknown") {
      add(15, { id: "broker:auth", title: "Broker authentication unconfirmed",
        detail: detail("health", "The gateway has not reported a known authentication state."),
        tone: "warning", source: "health", observedAt: observation("health"), action: "gateway" });
    }
    if (gateway.auth_state === "authenticated" && Object.keys(health.ib_pool ?? {}).length === 0) {
      add(15, { id: "broker:pool", title: "Broker API clients unconfirmed",
        detail: detail("health", "No API pool clients were reported. Inspect broker and API service observations."),
        tone: "warning", source: "health", observedAt: observation("health"), action: "services", subject: "radon-api.service" });
    }
    // Auth failure already explains unavailability of these connections. Do not
    // recommend a pool restart while authentication is still required.
    if (gateway.auth_state === "authenticated" && disconnected.length > 0) {
      add(5, { id: "broker:pool", title: auth.poolStuck ? "Broker API clients disconnected" : "Broker API connection incomplete",
        detail: detail("health", `${disconnected.length} of ${Object.keys(health.ib_pool).length} pool clients are disconnected. Review API service state before recovery.`),
        tone: "warning", source: "health", observedAt: observation("health"), action: "services", subject: "radon-api.service" });
    }
  }

  // Inventory liveness is independent of writer freshness and host permission.
  // Read-only known failures still warrant inspection; idle jobs are normal.
  for (const unit of [...(services?.units ?? [])].sort((a, b) => a.unit.localeCompare(b.unit))) {
    const verdict = unitVerdict(unit);
    if (verdict.tone === "positive" || verdict.label === "Idle") continue;
    add(unitKind(unit) === "daemon" && verdict.tone === "negative" ? 10 : verdict.label === "Unknown" ? 20 : 40, {
      id: `unit:${unit.unit}`, title: `${unit.unit}: ${verdict.label.toLowerCase()}`,
      detail: detail("services", verdict.label === "Unknown" ? "No known service state was reported. Inspect the inventory for control availability."
        : `The service reports ${verdict.label.toLowerCase()}. Inspect its state and available controls.`),
      tone: verdict.tone === "neutral" ? "warning" : verdict.tone,
      source: "services", observedAt: observation("services"), action: "services", subject: unit.unit,
    });
  }
  if (services && services.units.length === 0 && !sources.services.loading) {
    add(20, { id: "services:empty", title: "No services observed", detail: "This host has not reported a service inventory.",
      tone: "warning", source: "services", observedAt: observation("services"), action: "services" });
  }

  if (edge) {
    // Same 30s validity bound as health_service/probes.py UNIT_STATE_MAX_AGE_SECS.
    const unitAge = edge.units_age_secs;
    const validUnitAge = typeof unitAge === "number" && Number.isFinite(unitAge) && unitAge >= 0;
    if (edge.units && (!validUnitAge || unitAge * 1000 > ADMIN_OBSERVATION_STALE_MS)) {
      add(20, { id: "edge:units", title: "Edge service observations unconfirmed",
        detail: "The edge service snapshot is outside its observation window. Review the live service inventory.",
        tone: "warning", source: "edge", observedAt: validUnitAge ? iso((sources.edge.observedAt ?? now) - unitAge * 1000) : null, action: "services" });
    }
    const rows = edge.service_health?.rows;
    if (edge.service_health?.state !== "ok" || !rows?.length) {
      add(20, { id: "writers:source", title: "Writer health unconfirmed",
        detail: !rows?.length ? "No writer observations are available from this source."
          : "The writer health source is unavailable. Retained observations may not reflect current state.",
        tone: "warning", source: "edge", observedAt: observation("edge"), action: "writers" });
    }
    const market = getMarketStateFromDate(new Date(now));
    // A source should have one row per writer. If duplicates are supplied, use
    // its newest observation; at identical times a reported error takes precedence.
    const writers = new Map<string, ServiceHealthRow>();
    for (const row of [...(rows ?? [])].sort((a, b) => {
      const aTime = Date.parse(a.updated_at ?? "") || 0;
      const bTime = Date.parse(b.updated_at ?? "") || 0;
      return bTime - aTime || Number(b.state === "error") - Number(a.state === "error") || a.state.localeCompare(b.state);
    })) {
      if (!writers.has(row.service)) writers.set(row.service, row);
    }
    for (const row of writers.values()) {
      const failed = row.state === "error";
      const warning = row.state === "warn" || row.state === "warning";
      const stale = getServiceCategory(row.service) === "scheduled" && isStale(row.service, row.updated_at, market, now);
      const unknown = row.state === "unknown" || !row.state;
      if (!failed && !warning && !stale && !unknown) continue;
      const result = failed ? "The latest reported writer result is an error." : warning
        ? "The latest reported writer result is a warning." : unknown
        ? "The latest writer result is unknown." : "The latest reported writer result is not an error.";
      const freshness = stale ? " Its scheduled update is overdue." : "";
      add(failed ? 30 : warning ? 35 : unknown ? 40 : 50, {
        id: `writer:${row.service}`, title: `${row.service}: ${failed ? "update failed" : warning ? "update warning" : unknown ? "result unknown" : "update overdue"}`,
        detail: edge.service_health?.state === "ok"
          ? detail("edge", `${result}${freshness} Inspect writer diagnostics.`)
          : `Last recorded observation. ${result}${freshness} The writer source must recover to confirm current state.`,
        tone: failed ? "negative" : "warning", source: "edge", observedAt: iso(row.updated_at), action: "writers", subject: row.service,
      });
    }
    const probe = externalProbeSummary(edge.external_probe, now);
    if (probe.state !== "healthy") {
      add(probe.state === "down" ? 25 : 60, { id: "probe:external",
        title: probe.state === "down" ? "External probe reported a failure" : "External probe unconfirmed",
        detail: detail("edge", probe.state === "down" ? "The latest external sample did not report healthy reachability. Inspect the probe evidence."
          : probe.state === "stale" ? "The last external sample is outside its expected observation window."
          : "No valid external sample time is available."),
        tone: probe.state === "down" ? "negative" : "warning", source: "edge", observedAt: iso(edge.external_probe?.checked_at), action: "reliability" });
    }
  }

  return [...conditions.values()]
    .sort((a, b) => a.priority - b.priority || a.id.localeCompare(b.id))
    .map(({ priority: _priority, ...condition }) => condition);
}

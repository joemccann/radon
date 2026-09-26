"use client";

import RequestError from "@/components/RequestError";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  AdminHealthPayload,
  EdgeHealthStatus,
  RestartLogEntry,
  ServiceAction,
  ServicesListResponse,
} from "@/lib/adminTypes";
import type { ReliabilityHistoryPayload } from "@/lib/adminReliability";
import type { HostMetricsPayload } from "@/lib/adminHostMetrics";
import type { SloPayload } from "@/lib/adminSlo";

/** Latest action result; drives the row-level success/failure flash. */
export type FlashTarget = {
  unit: string;
  at: number;
  ok: boolean;
};
import IbGatewayCard from "./IbGatewayCard";
import Ib2faControls from "./Ib2faControls";
import ServiceControlPanel from "./ServiceControlPanel";
import RestartLog from "./RestartLog";
import AdminAttentionQueue from "./AdminAttentionQueue";
import AdminSystemOverview from "./AdminSystemOverview";
import { deriveAdminAttention, isAdminObservationCurrent, type AdminAttentionSources, type AdminAttentionCondition } from "@/lib/adminAttention";
import styles from "./adminActionQueue.module.css";
import ReliabilityStrip from "./ReliabilityStrip";
import SloStrip from "./SloStrip";
import HostMetricsStrip from "./HostMetricsStrip";
import WriterFreshnessTable from "./WriterFreshnessTable";
import DemoUsersTable from "./DemoUsersTable";
import TradingKillSwitch from "./TradingKillSwitch";

type EdgePayload = (EdgeHealthStatus & { reachable?: boolean }) | null;

const HEALTH_POLL_MS = 5_000;
const EDGE_POLL_MS = 5_000;
// History tiles aggregate a 7-day window; minute-level freshness is plenty
// and keeps the Turso events query (bounded + indexed) off the hot poll path.
const RELIABILITY_POLL_MS = 60_000;
// Aligned with HEALTH_POLL_MS for now — both endpoints hit local FastAPI and
// have similar refresh budgets. Tune independently if /admin/services proves
// expensive under load (current implementation runs a single ``systemctl
// list-units`` per call, so this is comfortable).
const SERVICES_POLL_MS = 5_000;
// Tighter cadence while a unit is in a transitional state (``activating``,
// ``reloading``, ``deactivating``). Mirrors the operator's "did it work?"
// glance pattern after clicking restart.
const SERVICES_TRANSITIONAL_POLL_MS = 2_000;
// How long to keep the success-flash class on the row that was just acted on.
const FLASH_DURATION_MS = 2_000;

/**
 * Shell for the /admin route. Owns:
 *   - polled IB Gateway health (every 5s)
 *   - radon-* unit catalogue (on mount + after each service action)
 *   - in-memory action log (last 5 entries)
 *
 * Renders responsively: the same panel serves desktop and mobile (393px). On
 * mobile it sits inside the MobileShell chrome; actionable conditions lead
 * the page and the full inventory is available through disclosures.
 *
 * Keeps the page-level component thin: child components are render-only.
 */
export default function AdminWorkspace() {
  const [health, setHealth] = useState<AdminHealthPayload | null>(null);
  const [telemetryErrors, setTelemetryErrors] = useState<Record<string, unknown>>({});
  const [healthError, setHealthError] = useState<string | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);

  const [services, setServices] = useState<ServicesListResponse | null>(null);
  const [servicesError, setServicesError] = useState<string | null>(null);
  const [servicesLoading, setServicesLoading] = useState(true);

  const [edge, setEdge] = useState<EdgePayload>(null);
  const [edgeReachable, setEdgeReachable] = useState(false);
  const [edgeLoaded, setEdgeLoaded] = useState(false);

  const [reliability, setReliability] = useState<ReliabilityHistoryPayload | null>(null);
  const [hostMetrics, setHostMetrics] = useState<HostMetricsPayload | null>(null);
  const [slo, setSlo] = useState<SloPayload | null>(null);

  // Epoch ms of the last successful poll, + a 1s tick so "updated Ns ago"
  // counts up live without a fetch.
  const [healthObservedAt, setHealthObservedAt] = useState<number | null>(null);
  const [servicesObservedAt, setServicesObservedAt] = useState<number | null>(null);
  const [edgeObservedAt, setEdgeObservedAt] = useState<number | null>(null);
  const [primaryActionContainer, setPrimaryActionContainer] = useState<HTMLDivElement | null>(null);
  const disclosures = useRef<Partial<Record<string, HTMLDetailsElement>>>({});
  const [nowTick, setNowTick] = useState(() => Date.now());

  const [log, setLog] = useState<RestartLogEntry[]>([]);

  // Row-level success flash: ``{ unit, at, ok }`` set on action completion,
  // cleared after FLASH_DURATION_MS. Drives the ``admin-row-flash`` class
  // on the matching ServiceRow.
  const [flashTarget, setFlashTarget] = useState<FlashTarget | null>(null);

  // Lock against concurrent polls; the panel hits localhost FastAPI so we
  // don't want overlapping fetches when a card re-renders.
  const healthInflightRef = useRef(false);
  const servicesInflightRef = useRef(false);
  const edgeInflightRef = useRef(false);
  const reliabilityInflightRef = useRef(false);
  const hostMetricsInflightRef = useRef(false);
  const sloInflightRef = useRef(false);
  const flashTimerRef = useRef<number | null>(null);

  const fetchHealth = useCallback(async () => {
    if (healthInflightRef.current) return;
    healthInflightRef.current = true;
    try {
      const res = await fetch("/api/admin/health", { cache: "no-store" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error ?? `health ${res.status}`);
      }
      const data = (await res.json()) as AdminHealthPayload;
      if (!data?.ib_gateway || !data.ib_pool) throw new Error("Invalid broker observation");
      setHealth(data);
      setHealthObservedAt(Date.now());
      setHealthError(null);
    } catch (err) {
      setHealthError(err instanceof Error ? err.message : "health probe failed");
    } finally {
      setHealthLoading(false);
      healthInflightRef.current = false;
    }
  }, []);

  const fetchServices = useCallback(async () => {
    if (servicesInflightRef.current) return;
    servicesInflightRef.current = true;
    try {
      const res = await fetch("/api/admin/services", { cache: "no-store" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error ?? `services ${res.status}`);
      }
      const data = (await res.json()) as ServicesListResponse;
      if (!Array.isArray(data?.units)) throw new Error("Invalid service observation");
      setServices(data);
      setServicesObservedAt(Date.now());
      setServicesError(null);
    } catch (err) {
      setServicesError(err instanceof Error ? err.message : "service list failed");
    } finally {
      setServicesLoading(false);
      servicesInflightRef.current = false;
    }
  }, []);

  // Edge health (the isolated daemon's aggregate, via the always-200 proxy):
  // service_health rows + the off-box probe. Always returns 200 with a
  // `reachable` flag, so a daemon/edge outage is data, not an exception.
  const fetchEdge = useCallback(async () => {
    if (edgeInflightRef.current) return;
    edgeInflightRef.current = true;
    try {
      const res = await fetch("/api/admin/edge-health", { cache: "no-store" });
      if (!res.ok) throw new Error("Edge observation unavailable");
      const data = (await res.json().catch(() => null)) as EdgePayload;
      if (data?.reachable) {
        setEdge(data);
        setEdgeObservedAt(Date.now());
      }
      setEdgeReachable(Boolean(data?.reachable));
    } catch {
      setEdgeReachable(false);
    } finally {
      setEdgeLoaded(true);
      edgeInflightRef.current = false;
    }
  }, []);

  // The 7-day service_health_events history behind the uptime / MTTR /
  // transitions / deploy tiles. The route is always-200 (missing table or
  // unreachable DB is data — `missing: true` — not an exception).
  const fetchReliability = useCallback(async () => {
    if (reliabilityInflightRef.current) return;
    reliabilityInflightRef.current = true;
    try {
      const res = await fetch("/api/admin/reliability", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as ReliabilityHistoryPayload | null;
      if (!data || !Array.isArray(data.events)) throw new Error("Invalid telemetry response");
      setReliability(data);
      setTelemetryErrors(previous => ({ ...previous, "reliability": null }));
    } catch (error) {
      setTelemetryErrors(previous => ({ ...previous, "reliability": error }));
      // Keep the last good payload; the tiles degrade to "--" only when
      // nothing has ever loaded.
    } finally {
      reliabilityInflightRef.current = false;
    }
  }, []);

  // The 1h host_metrics window behind the CPU / memory / loop-lag strip
  // (DUR-12). Always-200 route: a missing table or unreachable DB is data
  // (`missing: true`), not an exception. Minutely samples + a 60s poll keep
  // the strip one sample behind at worst.
  const fetchHostMetrics = useCallback(async () => {
    if (hostMetricsInflightRef.current) return;
    hostMetricsInflightRef.current = true;
    try {
      const res = await fetch("/api/admin/host-metrics", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as HostMetricsPayload | null;
      if (!data || !Array.isArray(data.rows)) throw new Error("Invalid telemetry response");
      setHostMetrics(data);
      setTelemetryErrors(previous => ({ ...previous, "host-metrics": null }));
    } catch (error) {
      setTelemetryErrors(previous => ({ ...previous, "host-metrics": error }));
      // Keep the last good payload; the tiles flag staleness themselves.
    } finally {
      hostMetricsInflightRef.current = false;
    }
  }, []);

  // The 7-day external_probe_runs history behind the SLO attainment tiles
  // (DUR-16). Always-200 route: a missing table or unreachable DB is data
  // (`missing: true`), not an exception.
  const fetchSlo = useCallback(async () => {
    if (sloInflightRef.current) return;
    sloInflightRef.current = true;
    try {
      const res = await fetch("/api/admin/slo", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as SloPayload | null;
      if (!data || !Array.isArray(data.rows)) throw new Error("Invalid telemetry response");
      setSlo(data);
      setTelemetryErrors(previous => ({ ...previous, "slo": null }));
    } catch (error) {
      setTelemetryErrors(previous => ({ ...previous, "slo": error }));
      // Keep the last good payload; the tiles degrade to "--" only when
      // nothing has ever loaded.
    } finally {
      sloInflightRef.current = false;
    }
  }, []);

  // True while any visible unit is in a transitional state — drives a faster
  // poll cadence so the operator sees activating -> active without waiting.
  const hasTransitionalUnit = hasTransitionalRow(services);

  useEffect(() => {
    void fetchHealth();
    void fetchServices();
    void fetchEdge();
    void fetchReliability();
    void fetchHostMetrics();
    void fetchSlo();
    const healthId = window.setInterval(fetchHealth, HEALTH_POLL_MS);
    const servicesInterval = hasTransitionalUnit
      ? SERVICES_TRANSITIONAL_POLL_MS
      : SERVICES_POLL_MS;
    const servicesId = window.setInterval(fetchServices, servicesInterval);
    const edgeId = window.setInterval(fetchEdge, EDGE_POLL_MS);
    const reliabilityId = window.setInterval(fetchReliability, RELIABILITY_POLL_MS);
    const hostMetricsId = window.setInterval(fetchHostMetrics, RELIABILITY_POLL_MS);
    const sloId = window.setInterval(fetchSlo, RELIABILITY_POLL_MS);
    return () => {
      window.clearInterval(healthId);
      window.clearInterval(servicesId);
      window.clearInterval(edgeId);
      window.clearInterval(reliabilityId);
      window.clearInterval(hostMetricsId);
      window.clearInterval(sloId);
    };
  }, [fetchHealth, fetchServices, fetchEdge, fetchReliability, fetchHostMetrics, fetchSlo, hasTransitionalUnit]);

  // 1s ticker so the "updated Ns ago" indicator counts up between polls.
  useEffect(() => {
    const id = window.setInterval(() => setNowTick(Date.now()), 1_000);
    return () => window.clearInterval(id);
  }, []);

  // Cleanup the flash timer on unmount so we don't call setState on a
  // disposed component.
  useEffect(() => {
    return () => {
      if (flashTimerRef.current !== null) {
        window.clearTimeout(flashTimerRef.current);
        flashTimerRef.current = null;
      }
    };
  }, []);

  const [actionError, setActionError] = useState<RestartLogEntry | null>(null);
  const appendLog = useCallback((entry: RestartLogEntry) => {
    setActionError(entry.ok ? null : entry);
    setLog((prev) => [entry, ...prev].slice(0, 25));
  }, []);

  const forcePush = useCallback(async () => {
    const at = new Date().toISOString();
    try {
      const res = await fetch("/api/admin/ib/restart", { method: "POST", cache: "no-store" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = typeof body.error === "string" ? body.error : `HTTP ${res.status}`;
        appendLog({ at, action: "force-2fa", target: "ib-gateway", ok: false, detail });
      } else {
        const detail = body.authenticated
          ? "restart authenticated"
          : body.reason
            ? `deferred: ${body.reason}`
            : "restart fired";
        appendLog({ at, action: "force-2fa", target: "ib-gateway", ok: true, detail });
      }
    } catch (err) {
      const detail = err instanceof Error ? err.message : "force push failed";
      appendLog({ at, action: "force-2fa", target: "ib-gateway", ok: false, detail });
    }
    void fetchHealth();
  }, [appendLog, fetchHealth]);

  const resetBackoff = useCallback(async () => {
    const at = new Date().toISOString();
    try {
      const res = await fetch("/api/admin/ib/reset-backoff", { method: "POST", cache: "no-store" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = typeof body.error === "string" ? body.error : `HTTP ${res.status}`;
        appendLog({ at, action: "reset-backoff", target: "ib-gateway", ok: false, detail });
      } else {
        appendLog({ at, action: "reset-backoff", target: "ib-gateway", ok: true, detail: "backoff cleared" });
      }
    } catch (err) {
      const detail = err instanceof Error ? err.message : "reset failed";
      appendLog({ at, action: "reset-backoff", target: "ib-gateway", ok: false, detail });
    }
    void fetchHealth();
  }, [appendLog, fetchHealth]);

  const restartStack = useCallback(async () => {
    const at = new Date().toISOString();
    let succeeded = false;
    try {
      const res = await fetch("/api/admin/stack/restart", { method: "POST", cache: "no-store" });
      const body = await res.json().catch(() => ({}));
      if (res.ok || res.status === 202) {
        succeeded = true;
        const detail = body.in_flight
          ? "restart in flight: FastAPI cycled, polling for recovery"
          : typeof body.detail === "string"
            ? body.detail.slice(0, 120)
            : "stack restart fired";
        appendLog({ at, action: "stack-restart", target: "all radon-*", ok: true, detail });
      } else {
        const detail = typeof body.error === "string" ? body.error : `HTTP ${res.status}`;
        appendLog({ at, action: "stack-restart", target: "all radon-*", ok: false, detail });
      }
    } catch (err) {
      // A browser-side connection loss is not an acknowledgement from the
      // control plane. Keep polling, but never record it as a successful
      // restart: the lock-aware backend remains safe to retry after status
      // confirms the previous request was not accepted.
      const detail = err instanceof Error ? err.message : "stack restart failed";
      appendLog({
        at,
        action: "stack-restart",
        target: "all radon-*",
        ok: false,
        detail,
      });
    }
    void fetchHealth();
    void fetchServices();
    return succeeded;
  }, [appendLog, fetchHealth, fetchServices]);

  // Targeted per-unit stop of the gateway. Leaves it cleanly stopped + stable
  // (watchdog stands down on a port-down gateway). Start is the matching
  // per-unit start: on the app host that is the broker mTLS daemon, not
  // `radon restart` (no operator CLI in the API container).
  const stopGateway = useCallback(async () => {
    const at = new Date().toISOString();
    const unit = "radon-ib-gateway.service";
    let succeeded = false;
    try {
      const res = await fetch(`/api/admin/services/${unit}/stop`, {
        method: "POST",
        cache: "no-store",
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = typeof body.error === "string" ? body.error : `HTTP ${res.status}`;
        appendLog({ at, action: "service-action", target: `${unit} stop`, ok: false, detail });
      } else {
        succeeded = true;
        appendLog({
          at,
          action: "service-action",
          target: `${unit} stop`,
          ok: true,
          detail: typeof body.detail === "string" ? body.detail.slice(0, 120) : "ok",
        });
      }
    } catch (err) {
      const detail = err instanceof Error ? err.message : "gateway stop failed";
      appendLog({ at, action: "service-action", target: `${unit} stop`, ok: false, detail });
    }
    void fetchServices();
    void fetchHealth();
    return succeeded;
  }, [appendLog, fetchHealth, fetchServices]);

  const startGateway = useCallback(async () => {
    const at = new Date().toISOString();
    const unit = "radon-ib-gateway.service";
    let succeeded = false;
    try {
      const res = await fetch(`/api/admin/services/${unit}/start`, {
        method: "POST",
        cache: "no-store",
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = typeof body.error === "string" ? body.error : `HTTP ${res.status}`;
        appendLog({ at, action: "service-action", target: `${unit} start`, ok: false, detail });
      } else {
        succeeded = true;
        appendLog({
          at,
          action: "service-action",
          target: `${unit} start`,
          ok: true,
          detail: typeof body.detail === "string" ? body.detail.slice(0, 120) : "ok",
        });
      }
    } catch (err) {
      const detail = err instanceof Error ? err.message : "gateway start failed";
      appendLog({ at, action: "service-action", target: `${unit} start`, ok: false, detail });
    }
    void fetchServices();
    void fetchHealth();
    return succeeded;
  }, [appendLog, fetchHealth, fetchServices]);

  const flashRow = useCallback((unit: string, ok: boolean) => {
    if (flashTimerRef.current !== null) {
      window.clearTimeout(flashTimerRef.current);
    }
    setFlashTarget({ unit, at: Date.now(), ok });
    flashTimerRef.current = window.setTimeout(() => {
      setFlashTarget(null);
      flashTimerRef.current = null;
    }, FLASH_DURATION_MS);
  }, []);

  const runServiceAction = useCallback(
    async (unit: string, action: ServiceAction) => {
      const at = new Date().toISOString();
      let succeeded = false;
      try {
        const res = await fetch(`/api/admin/services/${unit}/${action}`, {
          method: "POST",
          cache: "no-store",
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          const detail = typeof body.error === "string" ? body.error : `HTTP ${res.status}`;
          appendLog({ at, action: "service-action", target: `${unit} ${action}`, ok: false, detail });
        } else {
          succeeded = true;
          appendLog({
            at,
            action: "service-action",
            target: `${unit} ${action}`,
            ok: true,
            detail: typeof body.detail === "string" ? body.detail.slice(0, 120) : "ok",
          });
        }
      } catch (err) {
        const detail = err instanceof Error ? err.message : "service control failed";
        appendLog({ at, action: "service-action", target: `${unit} ${action}`, ok: false, detail });
      }
      flashRow(unit, succeeded);
      void fetchServices();
      void fetchHealth();
    },
    [appendLog, fetchHealth, fetchServices, flashRow],
  );

  const units = services?.units ?? [];
  const serviceHealthRows = edge?.service_health?.rows ?? [];
  const edgeFirstLoading = !edgeLoaded;
  const reliabilityLoading = (servicesLoading && !services) || edgeFirstLoading;
  const sources: AdminAttentionSources = {
    health: { observedAt: healthObservedAt, loading: healthLoading, error: healthError !== null },
    services: { observedAt: servicesObservedAt, loading: servicesLoading, error: servicesError !== null },
    edge: { observedAt: edgeObservedAt, loading: edgeFirstLoading, error: edgeLoaded && !edgeReachable },
  };
  const conditions = deriveAdminAttention({ health, services, edge, sources, now: nowTick });
  const checking = healthLoading || servicesLoading || edgeFirstLoading;
  const inspect = (section: string) => {
    const disclosure = disclosures.current[section];
    if (!disclosure) return;
    disclosure.open = true;
    if (section === "writers" && disclosures.current.services) disclosures.current.services.open = true;
    disclosure.querySelector("summary")?.focus({ preventScroll: true });
    disclosure.scrollIntoView?.({ block: "start" });
  };
  const refreshStatus = () => { void fetchHealth(); void fetchServices(); void fetchEdge(); };
  const review = (action: AdminAttentionCondition["action"]) => {
    if (action === "refresh") refreshStatus();
    else inspect(action);
  };
  const disclosureProps = (id: string) => ({
    className: styles.disclosure,
    "data-testid": `admin-disclosure-${id}`,
    ref: (node: HTMLDetailsElement | null) => { if (node) disclosures.current[id] = node; else delete disclosures.current[id]; },
  });

  return (
    <div className={`admin-shell ${styles.workspace}`} data-testid="admin-page">
      <div className="admin-page">
        {Object.entries(telemetryErrors).map(([source, error]) => (
          <RequestError key={source} error={error} fallback={`${source} telemetry could not be refreshed. Try again.`} />
        ))}
        <RequestError key={actionError?.at} error={actionError?.detail} fallback="The action could not be completed. Review service status and try again." />
        <header className={styles.pageHeader}>
          <div>
            <h1 className={styles.pageTitle}>Operator</h1>
            <p className={styles.pageMeta}>Current observations and recovery controls</p>
          </div>
          <div className={styles.headerActions}><TradingKillSwitch compact /></div>
        </header>
        <div className={styles.summary} data-testid="admin-status-summary">
          <span className={styles.summaryTitle}>{checking ? "Checking sources" : conditions.length ? `${conditions.length} ${conditions.length === 1 ? "needs" : "need"} attention` : "No action needed"}</span>
          <span className={styles.summaryDescription}>Broker, service and writer observations are checked independently.</span>
          <button type="button" className="admin-btn admin-btn-ghost" onClick={refreshStatus}>Refresh status</button>
        </div>

        <div className={styles.split}>
          <div>
            <AdminAttentionQueue conditions={conditions} loading={checking} now={nowTick} onReview={review} primaryActionRef={setPrimaryActionContainer} />
            <button type="button" className={styles.inspectButton} onClick={() => inspect("services")}>View all services and writers <span aria-hidden>→</span></button>
            <details {...disclosureProps("session")} open className={`${styles.disclosure} ${styles.history}`}>
              <summary className={styles.disclosureSummary}>Recent actions <span>This session</span></summary>
              <div className={styles.disclosureBody}><RestartLog entries={log} /></div>
            </details>
          </div>
          <aside className={styles.rail}>
            <AdminSystemOverview health={health} services={services} edge={edge} sources={sources} now={nowTick} />
            <nav className={styles.inspect} aria-label="Operator diagnostics">
              <h2 className={styles.inspectHeading}>Inspect</h2>
              {[["gateway", "Broker connection"], ["services", "Services & writers"], ["reliability", "Reliability · 7 days"], ["host", "Host resources · 1 hour"], ["access", "Access administration"]].map(([id, label]) => (
                <button key={id} type="button" className={styles.inspectButton} onClick={() => inspect(id)}>{label}<span aria-hidden>→</span></button>
              ))}
            </nav>
          </aside>
        </div>

        <div className={styles.secondaryGrid}>
          <details {...disclosureProps("gateway")}>
            <summary className={styles.disclosureSummary}>Broker connection & recovery</summary>
            <div className={`${styles.disclosureBody} admin-ib-row`}>
              <IbGatewayCard health={health} loading={healthLoading} error={healthError} />
              <Ib2faControls
                health={health}
                onForcePush={forcePush}
                onResetBackoff={resetBackoff}
                onRestartStack={restartStack}
                gatewayUnit={units.find((u) => u.unit === "radon-ib-gateway.service") ?? null}
                servicesSupported={services?.supported ?? false}
                hostRole={services?.host_role ?? health?.host_role}
                onStopGateway={stopGateway}
                onStartGateway={startGateway}
                apiUnreachable={servicesError != null && healthError != null}
                primaryActionContainer={primaryActionContainer}
                primaryObservationCurrent={isAdminObservationCurrent(sources.health, nowTick) && isAdminObservationCurrent(sources.services, nowTick)}
                onInspect={() => inspect("gateway")}
              />
            </div>
          </details>
          <details {...disclosureProps("services")}>
            <summary className={styles.disclosureSummary}>Services & writers</summary>
            <div className={styles.disclosureBody}>
              <ServiceControlPanel services={services} loading={servicesLoading} error={servicesError} onAction={runServiceAction} flashTarget={flashTarget} />
              <details {...disclosureProps("writers")}>
                <summary className={styles.disclosureSummary}>Writer freshness</summary>
                <div className={styles.disclosureBody}><WriterFreshnessTable rows={serviceHealthRows} reachable={edgeReachable} loading={edgeFirstLoading} /></div>
              </details>
            </div>
          </details>
          <details {...disclosureProps("reliability")}>
            <summary className={styles.disclosureSummary}>Reliability & objectives <span>7 days</span></summary>
            <div className={styles.disclosureBody}>
              <ReliabilityStrip units={units} edge={edge} health={health} edgeReachable={edgeReachable} history={reliability} loading={reliabilityLoading} />
              <SloStrip slo={slo} />
            </div>
          </details>
          <details {...disclosureProps("host")}>
            <summary className={styles.disclosureSummary}>Host resources <span>1 hour</span></summary>
            <div className={styles.disclosureBody}><HostMetricsStrip metrics={hostMetrics} /></div>
          </details>
          <details {...disclosureProps("access")}>
            <summary className={styles.disclosureSummary}>Access administration</summary>
            <div className={styles.disclosureBody}><DemoUsersTable /></div>
          </details>
        </div>
      </div>
    </div>
  );
}

/**
 * True when any visible unit is in a transitional state. Used to bump
 * polling cadence so the operator sees ``activating`` -> ``active``
 * quickly after firing a restart.
 */
function hasTransitionalRow(services: ServicesListResponse | null): boolean {
  if (!services?.units) return false;
  return services.units.some((u) =>
    u.active_state === "activating"
    || u.active_state === "reloading"
    || u.active_state === "deactivating",
  );
}

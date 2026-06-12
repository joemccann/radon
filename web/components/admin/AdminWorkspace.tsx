"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  AdminHealthPayload,
  EdgeHealthStatus,
  RestartLogEntry,
  ServiceAction,
  ServicesListResponse,
} from "@/lib/adminTypes";
import type { ReliabilityHistoryPayload } from "@/lib/adminReliability";

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
import SystemStatusBar from "./SystemStatusBar";
import ReliabilityStrip from "./ReliabilityStrip";
import WriterFreshnessTable from "./WriterFreshnessTable";

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
 * mobile it sits inside the MobileShell chrome; the wide tables scroll
 * horizontally and controls grow to 44px touch targets via the .admin-*
 * mobile media query in globals.css.
 *
 * Keeps the page-level component thin: child components are render-only.
 */
export default function AdminWorkspace() {
  const [health, setHealth] = useState<AdminHealthPayload | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);

  const [services, setServices] = useState<ServicesListResponse | null>(null);
  const [servicesError, setServicesError] = useState<string | null>(null);
  const [servicesLoading, setServicesLoading] = useState(true);

  const [edge, setEdge] = useState<EdgePayload>(null);
  const [edgeReachable, setEdgeReachable] = useState(false);
  const [edgeLoaded, setEdgeLoaded] = useState(false);

  const [reliability, setReliability] = useState<ReliabilityHistoryPayload | null>(null);

  // Epoch ms of the last successful poll, + a 1s tick so "updated Ns ago"
  // counts up live without a fetch.
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
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
      setHealth(data);
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
      setServices(data);
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
      const data = (await res.json().catch(() => null)) as EdgePayload;
      setEdge(data);
      setEdgeReachable(Boolean(data?.reachable));
      if (data?.reachable) setLastUpdatedAt(Date.now());
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
      const data = (await res.json().catch(() => null)) as ReliabilityHistoryPayload | null;
      if (data && Array.isArray(data.events)) setReliability(data);
    } catch {
      // Keep the last good payload; the tiles degrade to "--" only when
      // nothing has ever loaded.
    } finally {
      reliabilityInflightRef.current = false;
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
    const healthId = window.setInterval(fetchHealth, HEALTH_POLL_MS);
    const servicesInterval = hasTransitionalUnit
      ? SERVICES_TRANSITIONAL_POLL_MS
      : SERVICES_POLL_MS;
    const servicesId = window.setInterval(fetchServices, servicesInterval);
    const edgeId = window.setInterval(fetchEdge, EDGE_POLL_MS);
    const reliabilityId = window.setInterval(fetchReliability, RELIABILITY_POLL_MS);
    return () => {
      window.clearInterval(healthId);
      window.clearInterval(servicesId);
      window.clearInterval(edgeId);
      window.clearInterval(reliabilityId);
    };
  }, [fetchHealth, fetchServices, fetchEdge, fetchReliability, hasTransitionalUnit]);

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

  const appendLog = useCallback((entry: RestartLogEntry) => {
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
    try {
      const res = await fetch("/api/admin/stack/restart", { method: "POST", cache: "no-store" });
      const body = await res.json().catch(() => ({}));
      if (res.ok || res.status === 202) {
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
      // Network drop is the EXPECTED path because FastAPI is one of the
      // units being restarted. Treat as in-flight and rely on the next
      // health poll to confirm recovery.
      const detail = err instanceof Error ? err.message : "stack restart failed";
      const looksLikeRestartDrop =
        detail.includes("aborted") ||
        detail.includes("Failed to fetch") ||
        detail.includes("ECONNRESET");
      appendLog({
        at,
        action: "stack-restart",
        target: "all radon-*",
        ok: looksLikeRestartDrop,
        detail: looksLikeRestartDrop ? "restart in flight (connection cycled)" : detail,
      });
    }
    void fetchHealth();
    void fetchServices();
  }, [appendLog, fetchHealth, fetchServices]);

  // Targeted per-unit stop of the gateway. Leaves it cleanly stopped + stable
  // (watchdog stands down on a port-down gateway). Start is NOT a per-unit
  // start — it routes through restartStack so the cascade dependents recover.
  const stopGateway = useCallback(async () => {
    const at = new Date().toISOString();
    const unit = "radon-ib-gateway.service";
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
  // A poll failed but prior data remains: surface a stale badge rather than
  // letting frozen data read as healthy.
  const stalled = Boolean((healthError || servicesError) && (health || services));
  const updatedSecsAgo =
    lastUpdatedAt != null ? Math.max(0, Math.floor((nowTick - lastUpdatedAt) / 1000)) : null;
  const serviceHealthRows = edge?.service_health?.rows ?? [];
  // First-paint loading (before any data) drives skeletons rather than empty
  // "Unknown"/"Loading..." text.
  const edgeFirstLoading = !edgeLoaded;
  const reliabilityLoading = (servicesLoading && !services) || edgeFirstLoading;

  return (
    <div className="admin-shell" data-testid="admin-page">
      <main className="admin-page">
        <header className="admin-page-header">
          <h1 className="admin-page-title">Operator</h1>
          <p className="admin-page-subtitle">
            Live status and controls for IB Gateway and the radon-* stack.
            Reliability signals are instantaneous and freshness based.
          </p>
        </header>

        <SystemStatusBar
          units={units}
          health={health}
          updatedSecsAgo={updatedSecsAgo}
          stalled={stalled}
          loading={reliabilityLoading}
        />

        <ReliabilityStrip
          units={units}
          edge={edge}
          health={health}
          edgeReachable={edgeReachable}
          history={reliability}
          loading={reliabilityLoading}
        />

        <div className="admin-grid">
          <div className="admin-ib-row">
            <IbGatewayCard health={health} loading={healthLoading} error={healthError} />
            <Ib2faControls
              health={health}
              onForcePush={forcePush}
              onResetBackoff={resetBackoff}
              onRestartStack={restartStack}
              gatewayUnit={units.find((u) => u.unit === "radon-ib-gateway.service") ?? null}
              servicesSupported={services?.supported ?? false}
              onStopGateway={stopGateway}
              onStartGateway={restartStack}
            />
          </div>
          <ServiceControlPanel
            services={services}
            loading={servicesLoading}
            error={servicesError}
            onAction={runServiceAction}
            flashTarget={flashTarget}
          />
          <WriterFreshnessTable
            rows={serviceHealthRows}
            reachable={edgeReachable}
            loading={edgeFirstLoading}
          />
          <RestartLog entries={log} />
        </div>
      </main>
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

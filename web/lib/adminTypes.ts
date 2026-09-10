/**
 * Shared types for the operator admin panel. Mirrors the FastAPI shape
 * exposed by ``GET /health`` and the ``services`` module so UI components
 * can be tested without a live FastAPI process.
 */

export type IbAuthState =
  | "authenticated"
  | "awaiting_2fa"
  | "unreachable"
  | "unknown"
  | "remote";

export type PushLockState = {
  holder: string;
  acquired_at: number;
  expires_at: number;
  remaining_secs: number;
  reason: string | null;
};

export type RestartBackoffState = {
  attempt_count: number;
  last_attempt_at: number;
  next_attempt_after: number;
  next_attempt_in_secs: number;
  last_outcome: string | null;
  push_lock: PushLockState | null;
};

export type IbPoolRoleStatus = {
  connected: boolean;
  client_id: number;
  managed_accounts: string[];
};

export type IbPoolStatus = Record<string, IbPoolRoleStatus>;

export type IbGatewayHealth = {
  auth_state: IbAuthState;
  port_listening: boolean;
  upstream_dead?: boolean;
  service_state?: string;
  host?: string;
  port?: number;
  gateway_mode?: string;
  restart_backoff?: RestartBackoffState;
  container_state?: string;
  container_health?: string;
};

export type HostRole = "app" | "broker" | "combined";

export type AdminHealthPayload = {
  status: string;
  host_role?: HostRole;
  ib_gateway: IbGatewayHealth;
  ib_pool: IbPoolStatus;
  uw?: boolean;
};

export type UnitStatus = {
  unit: string;
  load_state: string;
  active_state: string;
  sub_state: string;
  description: string;
  can_control: boolean;
  // When the unit last became active OR last finished (oneshots). UTC ISO8601.
  // ``null`` when the unit has never been started.
  last_active_at?: string | null;
  // Most recent exit code, populated only for ``Type=oneshot`` services.
  last_exit_code?: number | null;
  // Seconds since the unit became active (currently-running daemons only).
  uptime_secs?: number | null;
};

export type ServicesListResponse = {
  supported: boolean;
  host_role?: HostRole;
  units: UnitStatus[];
};

export type ServiceAction = "start" | "stop" | "restart";

export type ServiceActionResult = {
  unit: string;
  action: ServiceAction;
  ok: boolean;
  detail: string;
  returncode: number;
};

export type RestartLogEntry = {
  at: string;             // ISO timestamp written by the client
  action: "force-2fa" | "reset-backoff" | "service-action" | "stack-restart";
  target: string;         // unit name or "ib-gateway"
  ok: boolean;
  detail: string;
};

// --- Reliability surface (the isolated /edge-health daemon payload) ---

export type UnitKind = "daemon" | "job";
export type VerdictTone = "positive" | "warning" | "negative" | "neutral";
export type UnitVerdict = { label: string; tone: VerdictTone };

/** One row of the Turso `service_health` table, as surfaced by /edge-health/status. */
export type ServiceHealthRow = {
  service: string;
  state: string;
  updated_at?: string | null;
  last_attempt_started_at?: string | null;
  last_attempt_finished_at?: string | null;
  last_error?: string | null;
  age_secs?: number | null;
};

/** The Tier-3 off-box prober row (Turso `external_probe`, latest-per-source). */
export type ExternalProbeRow = {
  source: string;
  ok: number;             // 1 = edge reachable + healthy, 0 = not
  http_status?: number | null;
  latency_ms?: number | null;
  checked_at?: string | null;
  detail?: string | null;
};

/** /edge-health/status — the always-200 aggregate from the isolated daemon. */
export type EdgeHealthStatus = {
  health_service?: string;
  generated_at?: string;
  probes?: Record<string, { state: string; http_status?: number; payload?: unknown; detail?: string }>;
  units?: Record<string, { state: string; active_state?: string; sub_state?: string }>;
  units_age_secs?: number | null;
  service_health?: { state: string; detail?: string; rows?: ServiceHealthRow[] };
  external_probe?: ExternalProbeRow | null;
};

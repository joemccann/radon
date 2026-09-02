/**
 * Operator credentials client contract.
 *
 * Wire types mirror `_service_entry()` in `scripts/api/routes/credentials.py`
 * exactly, and the browser helpers talk only to `/api/credentials` (the
 * Next.js proxy), never to FastAPI directly. No value ever comes back from
 * this surface: reads carry masked hints only.
 */

export type ValidationStatus = "valid" | "invalid" | "error" | "unchecked";

export type CredentialFieldEntry = {
  name: string;
  label: string;
  secret: boolean;
  placeholder: string;
  configured: boolean;
  hint: string;
  version: number;
  updated_at: string | null;
  updated_by: string | null;
  env_fallback: boolean;
};

export type CredentialServiceEntry = {
  id: string;
  label: string;
  group: string;
  validator: boolean;
  slow: boolean;
  note: string;
  fields: CredentialFieldEntry[];
};

export type CredentialsPayload = {
  services: CredentialServiceEntry[];
  groups: string[];
  generated_at: string;
};

export type ValidationVerdict = {
  status: ValidationStatus;
  message: string;
};

export type CredentialMutationResult = {
  service: CredentialServiceEntry;
  validation: ValidationVerdict;
};

export class CredentialsRequestError extends Error {
  readonly status: number;
  readonly code: string;
  readonly verdict: ValidationVerdict | null;

  constructor(
    status: number,
    code: string,
    message: string,
    verdict: ValidationVerdict | null = null,
  ) {
    super(message);
    this.name = "CredentialsRequestError";
    this.status = status;
    this.code = code;
    this.verdict = verdict;
  }
}

const CREDENTIALS_ENDPOINT = "/api/credentials";

// PUT can carry a real Playwright login (MenthorQ / TheMarketEar) which the
// backend bounds at 90s; the proxy allows 100s, this backstop sits above it.
const READ_TIMEOUT_MS = 20_000;
const MUTATE_TIMEOUT_MS = 110_000;

function errorFromBody(status: number, body: unknown): CredentialsRequestError {
  const payload = (body ?? {}) as Record<string, unknown>;
  const detail = (payload.detail ?? payload) as Record<string, unknown>;
  const code = typeof detail.code === "string" ? detail.code : "UPSTREAM_ERROR";
  const message =
    typeof detail.message === "string"
      ? detail.message
      : typeof payload.error === "string"
        ? payload.error
        : `HTTP ${status}`;
  const verdict =
    typeof detail.status === "string" && code === "CREDENTIAL_REJECTED"
      ? { status: detail.status as ValidationStatus, message }
      : null;
  return new CredentialsRequestError(status, code, message, verdict);
}

async function requestJson<T>(
  input: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<T> {
  const res = await fetch(input, {
    cache: "no-store",
    ...init,
    signal: AbortSignal.timeout(timeoutMs),
  });
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) {
    throw errorFromBody(res.status, body);
  }
  return body as T;
}

export async function fetchCredentials(): Promise<CredentialsPayload> {
  return requestJson<CredentialsPayload>(
    CREDENTIALS_ENDPOINT,
    { method: "GET" },
    READ_TIMEOUT_MS,
  );
}

export async function saveCredentials(
  serviceId: string,
  values: Record<string, string>,
): Promise<CredentialMutationResult> {
  return requestJson<CredentialMutationResult>(
    `${CREDENTIALS_ENDPOINT}/${encodeURIComponent(serviceId)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values }),
    },
    MUTATE_TIMEOUT_MS,
  );
}

export async function deleteCredential(
  serviceId: string,
  name: string,
): Promise<{ removed: boolean; service: CredentialServiceEntry }> {
  return requestJson(
    `${CREDENTIALS_ENDPOINT}/${encodeURIComponent(serviceId)}?name=${encodeURIComponent(name)}`,
    { method: "DELETE" },
    READ_TIMEOUT_MS,
  );
}

export function groupCredentialServices(
  services: CredentialServiceEntry[],
  groups: string[],
): Array<{ group: string; services: CredentialServiceEntry[] }> {
  const buckets = new Map<string, CredentialServiceEntry[]>();
  for (const service of services) {
    const bucket = buckets.get(service.group);
    if (bucket) {
      bucket.push(service);
      continue;
    }
    buckets.set(service.group, [service]);
  }
  const known = groups.filter((group) => (buckets.get(group)?.length ?? 0) > 0);
  const unknown = [...buckets.keys()]
    .filter((group) => !groups.includes(group))
    .sort((a, b) => a.localeCompare(b));
  return [...known, ...unknown].map((group) => ({
    group,
    services: buckets.get(group) ?? [],
  }));
}

/* ── Playful rejection copy ─────────────────────────────
 * The operator asked for cheek on the retry path. Deterministic pick per
 * attempt count so tests stay stable and repeated failures rotate the line. */

const REJECTION_LINES = [
  (vendor: string) =>
    `${vendor} took one look at that key and said absolutely not. Check for stray spaces and try again.`,
  (vendor: string) =>
    `Bounced at the door. ${vendor} insists it has never seen that credential in its life. Another go?`,
  (vendor: string) =>
    `Swing and a miss. ${vendor} rejected that one, so paste fresh and fire again.`,
  (vendor: string) =>
    `${vendor} says no. It did not elaborate. A newly minted key usually changes its mind.`,
] as const;

export function playfulRejection(vendorLabel: string, attempt: number): string {
  const line = REJECTION_LINES[Math.abs(attempt) % REJECTION_LINES.length];
  return line(vendorLabel);
}

export function outageNotice(vendorLabel: string): string {
  return `We could not reach ${vendorLabel} to check that one, so we saved it on good faith. You two can sort it out later.`;
}

export function slowValidationNotice(vendorLabel: string): string {
  return `${vendorLabel} makes us log in the long way. Give it up to a minute.`;
}

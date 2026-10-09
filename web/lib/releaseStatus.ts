export type ReleaseChannel = "local" | "production";

export type ReleaseIdentity = {
  version: string;
  sha: string;
  builtAt: string;
  channel: ReleaseChannel;
};

export type ReleaseState = "current" | "update" | "local" | "unknown";

export const RELEASE_VERSION_PATH = "/api/version";
export const RELEASE_POLL_MS = 5 * 60 * 1000;

export function runningRelease(): ReleaseIdentity {
  const channel = process.env.NEXT_PUBLIC_RADON_CHANNEL === "production" ? "production" : "local";
  return {
    version: process.env.NEXT_PUBLIC_RADON_VERSION || "0.7.0",
    sha: process.env.NEXT_PUBLIC_RADON_GIT_SHA || "dev",
    builtAt: process.env.NEXT_PUBLIC_RADON_BUILT_AT || "",
    channel,
  };
}

export function parseRelease(body: unknown): ReleaseIdentity | null {
  if (!body || typeof body !== "object") return null;
  const row = body as Record<string, unknown>;
  if (typeof row.version !== "string" || !row.version || typeof row.sha !== "string" || !row.sha) return null;
  return {
    version: row.version,
    sha: row.sha,
    builtAt: typeof row.builtAt === "string" ? row.builtAt : "",
    channel: row.channel === "production" ? "production" : "local",
  };
}

/** SHA is the deploy identity. Semver is the label. A local bundle is never old. */
export function releaseState(running: ReleaseIdentity, latest: ReleaseIdentity | null, failed: boolean): ReleaseState {
  if (running.channel !== "production" || running.sha === "dev" || running.sha === "unknown") return "local";
  if (failed || !latest || !latest.sha || latest.sha === "unknown") return "unknown";
  if (latest.sha !== running.sha) return "update";
  return "current";
}

export function formatReleaseWhen(iso: string): string {
  if (!iso) return "---";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "---";
  const day = new Intl.DateTimeFormat("en-GB", {
    timeZone: "America/New_York",
    day: "2-digit",
    month: "short",
  }).format(date);
  const time = new Intl.DateTimeFormat("en-GB", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
  return `${day} ${time} ET`;
}

export function releaseStamp(identity: ReleaseIdentity): string {
  const when = identity.builtAt ? formatReleaseWhen(identity.builtAt) : "---";
  return `${identity.sha} · ${when}`;
}

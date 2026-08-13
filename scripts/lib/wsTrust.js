/**
 * WebSocket upgrade trust decision for the IB realtime relay.
 *
 * SECURITY: The relay sits behind Caddy in production (`reverse_proxy
 * localhost:8765`). Every internet client therefore reaches the relay over a
 * fresh loopback TCP connection, so `socket.remoteAddress` is ALWAYS
 * `127.0.0.1` for real traffic. Trusting the peer address alone (the original
 * implementation) silently disabled WebSocket ticket auth for every production
 * connection — anyone could `wss://app.radon.run/ws` with no ticket.
 *
 * The fix mirrors `scripts/api/auth.py:is_trusted_local_request`: a connection
 * is only "trusted local" when the peer is loopback AND the request did NOT
 * arrive through the reverse proxy (Caddy stamps forwarding headers on every
 * hop). A genuine server-to-server call (Next.js → relay, local browser dev)
 * carries none of those headers.
 */

const FORWARDING_HEADERS = [
  "x-forwarded-for",
  "x-real-ip",
  "forwarded",
  "x-forwarded-host",
  "x-forwarded-proto",
];

const LOOPBACK_ADDRS = new Set(["127.0.0.1", "::1", "::ffff:127.0.0.1"]);

/** True if the request carries any reverse-proxy forwarding header. */
export function arrivedViaProxy(headers = {}) {
  return FORWARDING_HEADERS.some((name) => Boolean(headers && headers[name]));
}

/** Browser WebSocket handshakes must never inherit server-to-server trust. */
export function isBrowserUpgrade(headers = {}) {
  return Boolean(headers && (headers.origin || headers["sec-fetch-site"]));
}

/** True only for genuine loopback server-to-server calls (not browsers/proxies). */
export function isTrustedLocalUpgrade(remoteAddr, headers = {}) {
  return LOOPBACK_ADDRS.has(remoteAddr)
    && !arrivedViaProxy(headers)
    && !isBrowserUpgrade(headers);
}

/**
 * Decide whether ticket validation may be skipped for an upgrade request.
 * Skip only for a trusted loopback server-to-server call. Missing Clerk
 * configuration fails closed unless development explicitly opts in; browser
 * and proxied connections always require a valid ticket.
 */
export function shouldSkipTicketValidation({
  clerkConfigured,
  allowUnauthenticatedDev = false,
  remoteAddr,
  headers = {},
}) {
  if (!clerkConfigured) {
    return allowUnauthenticatedDev && isTrustedLocalUpgrade(remoteAddr, headers);
  }
  return isTrustedLocalUpgrade(remoteAddr, headers);
}

export function resolveRelaySecurityConfig(env = process.env) {
  const production = env.NODE_ENV === "production" || env.RADON_WS_REQUIRE_CLERK === "1";
  return {
    allowUnauthenticatedDev:
      !production && env.RADON_WS_ALLOW_UNAUTHENTICATED_DEV === "1",
    bindHost: env.WS_BIND_HOST || "127.0.0.1",
    clerkConfigured: Boolean(env.CLERK_JWKS_URL && env.CLERK_ISSUER),
    requireClerk: production,
  };
}

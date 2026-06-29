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

/** True only for genuine loopback server-to-server calls (not proxied). */
export function isTrustedLocalUpgrade(remoteAddr, headers = {}) {
  return LOOPBACK_ADDRS.has(remoteAddr) && !arrivedViaProxy(headers);
}

/**
 * Decide whether ticket validation may be skipped for an upgrade request.
 * Skip ONLY when Clerk is not configured (local dev with no auth) OR the
 * connection is a trusted loopback server-to-server call. Every proxied
 * (public) connection must present a valid ticket.
 */
export function shouldSkipTicketValidation({ clerkConfigured, remoteAddr, headers = {} }) {
  if (!clerkConfigured) return true;
  return isTrustedLocalUpgrade(remoteAddr, headers);
}

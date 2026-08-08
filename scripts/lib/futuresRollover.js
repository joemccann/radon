/**
 * Futures-chain rollover policy for the WS relay's forward-priced indices.
 *
 * The relay resolves a futures root to a qualified contract once
 * (reqContractDetails) and caches it FOREVER — `resolvedFuturesContracts` /
 * `resolvedFuturesChain` had no invalidation. A relay process outlives the roll
 * (the VPS unit only restarts on deploy), so after the cached expiry passes, the
 * relay keeps publishing a DEAD future's last print into `prices[VIX].fwd` and
 * `prices[VIX].fwdCurve[...]` — the numbers every VIX option's Black-Scholes
 * underlying is derived from. Frozen, but never marked stale.
 *
 * Everything here is pure (the ET-date helper is the single Intl edge): the
 * relay supplies `now` / the cached timestamps and executes the re-resolve.
 */

/**
 * How long a resolved chain may go unverified.
 *
 * 12h guarantees at least one re-check per relay day, which is what catches the
 * three things a forever-cache misses: a newly listed expiry (VIX lists a new
 * weekly every Wednesday, so a chain resolved on Monday is already short), a
 * contract re-specified upstream, and a chain resolved before a roll on a
 * long-lived process. It is also long enough that a busy trading session costs
 * at most ONE extra reqContractDetails per root — comfortably inside IB's
 * contract-details pacing budget (~50 requests / 10 min).
 */
export const FUTURES_RECHECK_TTL_MS = 12 * 60 * 60 * 1000;

/**
 * Floor between re-resolve ATTEMPTS for one root.
 *
 * A rolled expiry is stale on every lookup until a successful re-resolve
 * replaces it, and a re-resolve can fail (IB unreachable, no non-expired expiry
 * returned). Without a floor, every subscribe message would fire another
 * reqContractDetails. One minute bounds the failure mode to ~1 request/min/root
 * while still recovering promptly once IB answers again.
 */
export const FUTURES_RERESOLVE_MIN_GAP_MS = 60_000;

/** Digits-only YYYYMMDD, or "" when the input cannot supply 8 digits. */
function dateDigits(value) {
  const digits = String(value ?? "").replace(/\D/g, "");
  return digits.length >= 8 ? digits.slice(0, 8) : "";
}

/**
 * Should the relay re-resolve the cached contract/chain for a futures root?
 *
 * Rules, in order:
 *   1. An unusable cached expiry (missing, short, non-numeric) → re-resolve.
 *   2. The cached expiry is behind ET today → the contract has rolled. NOTE the
 *      boundary: expiry == ET today is NOT stale. A same-day future is still
 *      listed and still quoting (sortedNonExpiredChain admits `expiry >= today`)
 *      and remains the option's settlement reference through the session, so the
 *      roll is recognised at the ET date change rather than at the closing bell.
 *      That keeps a live session from having its curve re-resolved underneath it.
 *   3. Otherwise re-check on the TTL, so a still-valid contract is periodically
 *      re-verified and newly listed expiries enter the chain.
 *
 * @param {string|undefined|null} expiryYYYYMMDD cached contract expiry
 * @param {number|undefined|null} resolvedAtMs when the cache was written
 * @param {number} nowMs
 * @param {string} etTodayYYYYMMDD today's date in America/New_York
 * @returns {boolean}
 */
export function isResolvedFutureStale(expiryYYYYMMDD, resolvedAtMs, nowMs, etTodayYYYYMMDD) {
  const expiry = dateDigits(expiryYYYYMMDD);
  if (!expiry) return true;

  const today = dateDigits(etTodayYYYYMMDD);
  if (today && expiry < today) return true;

  if (!Number.isFinite(resolvedAtMs)) return true;
  return nowMs - resolvedAtMs >= FUTURES_RECHECK_TTL_MS;
}

/**
 * Rate-limit gate for re-resolve attempts on one root.
 *
 * @param {number|undefined|null} lastAttemptAtMs
 * @param {number} nowMs
 * @returns {boolean}
 */
export function shouldAttemptReresolve(lastAttemptAtMs, nowMs) {
  if (!Number.isFinite(lastAttemptAtMs)) return true;
  return nowMs - lastAttemptAtMs >= FUTURES_RERESOLVE_MIN_GAP_MS;
}

/**
 * Today's date in America/New_York as YYYYMMDD. The one non-pure function here
 * (Intl + the supplied instant); never a hardcoded UTC offset.
 *
 * @param {number|Date} [now]
 * @returns {string}
 */
export function etDateYYYYMMDD(now) {
  const at = typeof now === "number" ? new Date(now) : (now ?? new Date());
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(at);
  const p = Object.fromEntries(parts.map((x) => [x.type, x.value]));
  return `${p.year}${p.month}${p.day}`;
}

function epochDays(yyyymmdd) {
  const digits = dateDigits(yyyymmdd);
  if (!digits) return null;
  const y = Number(digits.slice(0, 4));
  const m = Number(digits.slice(4, 6));
  const d = Number(digits.slice(6, 8));
  if (!Number.isFinite(y) || !Number.isFinite(m) || !Number.isFinite(d)) return null;
  return Math.floor(Date.UTC(y, m - 1, d) / 86_400_000);
}

/**
 * Match an option expiry to the future it should be priced off.
 *
 * A future expiring ON OR AFTER the option always wins over one expiring
 * BEFORE it, even when the earlier one is closer in raw date distance. The old
 * pure-|distance| scan handed back the earlier contract on a tie (and whenever
 * it was nearer), which is the one contract that CANNOT serve as the option's
 * forward: it settles and stops ticking while the option is still live, so its
 * last print freezes and the relay publishes a dead future as a live forward.
 * Only when nothing listed reaches the option expiry (the option is beyond the
 * curve) do we fall back to the nearest earlier future.
 *
 * @param {Array<{lastTradeDateOrContractMonth?: string}>} chain nearest-first
 * @param {string} optExpiryDigits option expiry, YYYYMMDD
 * @returns {object|null}
 */
export function pickNearestExpiry(chain, optExpiryDigits) {
  if (!Array.isArray(chain) || chain.length === 0) return null;
  const target = epochDays(optExpiryDigits);
  if (target == null) return chain[0];

  let onOrAfter = null;
  let onOrAfterDist = Infinity;
  let before = null;
  let beforeDist = Infinity;

  for (const c of chain) {
    const e = epochDays(c?.lastTradeDateOrContractMonth);
    if (e == null) continue;
    const dist = Math.abs(e - target);
    if (e >= target) {
      if (dist < onOrAfterDist) { onOrAfterDist = dist; onOrAfter = c; }
    } else if (dist < beforeDist) {
      beforeDist = dist;
      before = c;
    }
  }

  return onOrAfter || before || chain[0];
}

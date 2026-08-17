/**
 * Per-client depth-ticket admission under the relay-global IB budget (R-082).
 *
 * IB caps concurrent reqMktDepth tickets (~3 on a baseline account), so the
 * budget itself is relay-global — but WHICH ticket to recycle is a per-client
 * decision. Recycling used to pick the globally-oldest ticket, so one
 * session's subscribe silently evicted another session's implied-book leg and
 * the victim never re-requested. The planner only ever recycles tickets the
 * requesting client EXCLUSIVELY subscribes to (its own focus rotation); when
 * the cap is held by other sessions' legs — or by tickets shared with them —
 * the newcomer is refused instead, and the relay tells it so explicitly.
 *
 * Pure decision function: no relay state, fully unit-testable.
 *
 * @param {Object} params
 * @param {Array<{key: string, focusedAt: number, subscribers: Set<unknown>}>} params.activeTickets
 *   Tickets with a live reqMktDepth id and the clients subscribed to each.
 * @param {unknown} params.requestingClient The client whose subscribe needs a
 *   ticket, or null when there is no requester (reconnect restore path).
 * @param {string} params.exceptKey The key being (re)subscribed — never evicted.
 * @param {number} params.maxConcurrent The relay-global ticket budget.
 * @returns {{admit: boolean, evictKeys: string[]}}
 */
export function planDepthAdmission({ activeTickets, requestingClient, exceptKey, maxConcurrent }) {
  const overflow = activeTickets.length - (maxConcurrent - 1);
  if (overflow <= 0) return { admit: true, evictKeys: [] };

  const exclusivelyOwn = activeTickets
    .filter((ticket) =>
      ticket.key !== exceptKey
      && requestingClient != null
      && ticket.subscribers.size === 1
      && ticket.subscribers.has(requestingClient))
    .sort((a, b) => a.focusedAt - b.focusedAt);

  if (exclusivelyOwn.length < overflow) return { admit: false, evictKeys: [] };
  return {
    admit: true,
    evictKeys: exclusivelyOwn.slice(0, overflow).map((ticket) => ticket.key),
  };
}

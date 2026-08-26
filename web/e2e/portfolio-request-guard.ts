import { expect, type Page } from "@playwright/test";

/**
 * T-172 guard: /orders mounts WorkspaceShell with `includeEntryDates`, so
 * `usePortfolio` requests "/api/portfolio?include=entry-dates". A bare
 * "**\/api/portfolio" glob does NOT match a URL carrying a query string, so
 * that request escapes the mock and hits the real server — a live Turso read
 * inside an e2e run. Specs stay green because their assertions are
 * orders-based, which is exactly why the isolation break is silent.
 *
 * Usage: route "**\/api/portfolio**" and call `record(route.request().url())`
 * from the handler, then `await assertAllRouted()` at the end of the test.
 */
export function watchPortfolioRequests(page: Page) {
  const seen: string[] = [];
  const routed = new Set<string>();

  page.on("request", (request) => {
    let pathname: string;
    try {
      pathname = new URL(request.url()).pathname;
    } catch {
      return;
    }
    if (pathname.startsWith("/api/portfolio")) seen.push(request.url());
  });

  return {
    /** Called from the route handler for every URL the mock actually served. */
    record: (url: string) => {
      routed.add(url);
    },
    seen,
    /** Fails if any /api/portfolio* request was issued without a route handler. */
    async assertAllRouted() {
      await expect
        .poll(() => seen.filter((url) => !routed.has(url)), {
          message:
            "unrouted /api/portfolio* request(s) reached the real server — widen the page.route glob to '**/api/portfolio**'",
        })
        .toEqual([]);
    },
  };
}

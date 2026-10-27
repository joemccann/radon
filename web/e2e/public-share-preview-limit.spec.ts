import { expect, test } from "@playwright/test";

const paths = [
  "/api/gex/share/content",
  "/api/internals/share/content",
  "/api/menthorq/cta/share/content",
  "/api/regime/share/content",
  "/api/share/pnl",
  "/api/vcg/share/content",
];

test("real middleware shares the anonymous preview request budget", async ({ request }, testInfo) => {
  test.setTimeout(120_000);
  const prefix = `2001:db8:${(testInfo.workerIndex + 1).toString(16)}`;
  // Compile each dev route before starting the measured window. Missing
  // parameters stop the handler before any renderer or backend call.
  for (const path of paths) {
    const warm = await request.get(path, { headers: { "x-forwarded-for": `${prefix}::2` } });
    expect(warm.status()).toBe(400);
  }
  for (let n = 0; n < 60; n++) {
    const response = await request.get(paths[n % paths.length], {
      headers: { "x-forwarded-for": `192.0.2.${n}, ${prefix}::1` },
    });
    expect(response.status(), `preview request ${n + 1}`).toBe(400);
  }
  const blocked = await request.get(paths[0], {
    headers: { "x-forwarded-for": `203.0.113.7, ${prefix}::1` },
  });
  expect(blocked.status()).toBe(429);
  expect(Number(blocked.headers()["retry-after"])).toBeGreaterThan(0);
  expect(blocked.headers()["cache-control"]).toContain("no-store");
  const independent = await request.get(paths[0], {
    headers: { "x-forwarded-for": `${prefix}::3` },
  });
  expect(independent.status()).toBe(400);
});

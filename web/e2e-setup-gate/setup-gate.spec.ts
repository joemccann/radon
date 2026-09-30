import { expect, test } from "@playwright/test";

const AUTH_KEYS_NOT_LOADED = "authentication keys are not loaded";

test("GET /portfolio without authless header redirects 307 to /setup", async ({
  request,
  page,
  baseURL,
}, testInfo) => {
  const res = await request.get("/portfolio", { maxRedirects: 0 });
  if (res.status() === 503) {
    const body = await res.text();
    if (body.includes(AUTH_KEYS_NOT_LOADED)) {
      throw new Error(
        "/portfolio returned 503 'authentication keys are not loaded'. "
        + "../.radon/setup-complete exists or RADON_SETUP_COMPLETE=1 is set. "
        + "Remove the marker and unset the flag so isSetupMode() can fire.",
      );
    }
  }
  expect(res.status(), `raw Location: ${res.headers().location ?? "<missing>"}`).toBe(307);
  const location = res.headers().location;
  console.log("setup-gate Location:", location);
  expect(location).toBeTruthy();
  expect(new URL(location!, baseURL).pathname).toBe("/setup");

  const resp = await page.goto("/portfolio");
  await expect(page).toHaveURL(/\/setup$/);
  expect(new URL(page.url()).pathname).toBe("/setup");

  const from = resp?.request().redirectedFrom();
  expect(from, "navigation should have redirected from /portfolio").toBeTruthy();
  expect(new URL(from!.url()).pathname).toBe("/portfolio");
  const fromResp = await from!.response();
  expect(fromResp?.status()).toBe(307);

  const navHeaders = from!.headers();
  expect(navHeaders["x-radon-authless-test"]).toBeUndefined();

  await page.screenshot({
    path: testInfo.outputPath("setup-gate-redirect.png"),
    fullPage: true,
  });

  const api = await request.get("/api/portfolio", { maxRedirects: 0 });
  expect(api.status()).toBe(503);
  const json = await api.json();
  expect(json.code).toBe("SETUP_MODE");
});

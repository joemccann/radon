/**
 * TradingView alert webhook — wire-level contract (docs/tradingview-integration.md
 * Phase 1). The handler order is the design:
 *   1. path token mismatch, or a presented body secret that does not match
 *      -> 401, write NOTHING. A message with no secret is stored.
 *   2. oversized body                    -> 413, write NOTHING
 *   3. INSERT the raw body BEFORE any parse
 *   4. parse JSON or text/plain; failure leaves parsed columns NULL
 *   5. 200 for everything past step 1 (TradingView never retries)
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ dbExecute: vi.fn() }));
vi.mock("@/lib/dbExecute", () => ({ dbExecute: mocks.dbExecute }));

import { POST } from "../app/api/webhooks/tradingview/[token]/route";
import { parseTvAlertBody, secretMatches, TV_WEBHOOK_MAX_BYTES } from "../lib/tvWebhook";

const TOKEN = "path-token-abcdefghijklmnopqrstuvwxyz012345";
const SECRET = "body-secret-abcdefghijklmnopqrstuvwxyz0123";
const URL_BASE = "https://app.radon.run/api/webhooks/tradingview";

function jsonBody(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    secret: SECRET,
    symbol: "NVDA",
    exchange: "NASDAQ",
    price: "181.25",
    interval: "15",
    alert: "NVDA breakout",
    bar_time: "2026-09-19T14:30:00Z",
    sent_at: "2026-09-19T14:30:02Z",
    ...overrides,
  });
}

async function post(token: string, body: string, contentType = "application/json") {
  const request = new Request(`${URL_BASE}/${token}`, {
    method: "POST",
    headers: { "content-type": contentType, "x-forwarded-for": "10.0.0.9, 52.89.214.238" },
    body,
  });
  return POST(request, { params: Promise.resolve({ token }) });
}

function insertCalls() {
  return mocks.dbExecute.mock.calls.filter(([stmt]) =>
    String(stmt.sql).startsWith("INSERT INTO tv_alert_events"),
  );
}

beforeEach(() => {
  mocks.dbExecute.mockReset();
  mocks.dbExecute.mockImplementation(async (stmt: { sql: string }) =>
    stmt.sql.startsWith("INSERT")
      ? { rows: [{ id: 42 }], rowsAffected: 1, lastInsertRowid: 42n }
      : { rows: [], rowsAffected: 1 },
  );
  vi.stubEnv("TV_WEBHOOK_PATH_TOKEN", TOKEN);
  vi.stubEnv("TV_WEBHOOK_SECRET", SECRET);
});

describe("POST /api/webhooks/tradingview/[token]", () => {
  it("writes the raw body first, then the parsed columns, and returns 200", async () => {
    const body = jsonBody();
    const res = await post(TOKEN, body);
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toContain("no-store");

    const [first, second] = mocks.dbExecute.mock.calls.map(([stmt]) => stmt);
    expect(first.sql).toBe(
      "INSERT INTO tv_alert_events (received_at, source_ip, raw_body) VALUES (?, ?, ?) RETURNING id",
    );
    expect(first.args[1]).toBe("52.89.214.238");
    expect(first.args[2]).toBe(body.split(SECRET).join("[REDACTED]"));
    expect(Number.isNaN(Date.parse(first.args[0]))).toBe(false);

    expect(second.sql).toBe(
      "UPDATE tv_alert_events SET symbol = ?, exchange = ?, price = ?, interval = ?, " +
        "alert_name = ?, bar_time = ?, sent_at = ?, parse_error = ? WHERE id = ?",
    );
    expect(second.args).toEqual([
      "NVDA", "NASDAQ", 181.25, "15", "NVDA breakout",
      "2026-09-19T14:30:00Z", "2026-09-19T14:30:02Z", null, 42,
    ]);
  });

  it("redacts the body secret from raw_body before insert (JSON field)", async () => {
    const res = await post(TOKEN, jsonBody());
    expect(res.status).toBe(200);
    const [insert] = insertCalls();
    const stored = String(insert[0].args[2]);
    expect(stored).not.toContain(SECRET);
    expect(stored).toContain("[REDACTED]");
    // Non-secret content survives redaction.
    expect(stored).toContain("NVDA");
  });

  it("redacts every occurrence of the secret anywhere in the body, all rotation values", async () => {
    vi.stubEnv("TV_WEBHOOK_SECRET", `${SECRET},second-secret-zyxwvutsrqponmlkjihgfedcba98`);
    const body = jsonBody({ alert: `leak ${SECRET} and second-secret-zyxwvutsrqponmlkjihgfedcba98 twice ${SECRET}` });
    const res = await post(TOKEN, body);
    expect(res.status).toBe(200);
    const stored = String(insertCalls()[0][0].args[2]);
    expect(stored).not.toContain(SECRET);
    expect(stored).not.toContain("second-secret-zyxwvutsrqponmlkjihgfedcba98");
    expect(stored.split("[REDACTED]").length - 1).toBe(4);
  });

  it("never persists the body secret in parsed columns", async () => {
    await post(TOKEN, jsonBody());
    const update = mocks.dbExecute.mock.calls[1][0];
    expect(update.args).not.toContain(SECRET);
  });

  it("wrong path token -> 401 and nothing written", async () => {
    const res = await post("wrong-token", jsonBody());
    expect(res.status).toBe(401);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });

  it("wrong body secret -> 401 and nothing written", async () => {
    const res = await post(TOKEN, jsonBody({ secret: "nope" }));
    expect(res.status).toBe(401);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });

  it("default crossing message has no body secret and is still stored", async () => {
    const body = "ALAB Crossing 357.02";
    const res = await post(TOKEN, body, "text/plain");
    expect(res.status).toBe(200);
    expect(insertCalls()).toHaveLength(1);
    expect(insertCalls()[0][0].args[2]).toBe(body);
    const update = mocks.dbExecute.mock.calls[1][0];
    expect(update.args).toEqual([
      "ALAB", null, 357.02, null, "ALAB Crossing 357.02", null, null, null, 42,
    ]);
  });

  it("exchange-prefixed crossing message fills symbol, exchange, and price", async () => {
    const res = await post(TOKEN, "NASDAQ:ALAB Crossing 357.02\n", "text/plain");
    expect(res.status).toBe(200);
    const update = mocks.dbExecute.mock.calls[1][0];
    expect(update.args.slice(0, 5)).toEqual([
      "ALAB", "NASDAQ", 357.02, null, "NASDAQ:ALAB Crossing 357.02",
    ]);
    expect(update.args[7]).toBeNull();
  });

  it("plain text with no secret and no crossing shape is stored, not rejected", async () => {
    const res = await post(TOKEN, "NVDA crossed 180", "text/plain");
    expect(res.status).toBe(200);
    expect(insertCalls()).toHaveLength(1);
    const update = mocks.dbExecute.mock.calls[1][0];
    expect(update.args.slice(0, 7)).toEqual([null, null, null, null, null, null, null]);
    expect(update.args[7]).toMatch(/json/i);
  });

  it("unconfigured env fails closed -> 401 and nothing written", async () => {
    vi.stubEnv("TV_WEBHOOK_PATH_TOKEN", "");
    const res = await post("", jsonBody());
    expect(res.status).toBe(401);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });

  it("oversized body -> 413 and nothing written", async () => {
    const res = await post(TOKEN, jsonBody({ pad: "x".repeat(TV_WEBHOOK_MAX_BYTES) }));
    expect(res.status).toBe(413);
    expect(mocks.dbExecute).not.toHaveBeenCalled();
  });

  it("text/plain body carrying the secret persists redacted and returns 200", async () => {
    const body = `secret=${SECRET} NVDA crossed 180`;
    const res = await post(TOKEN, body, "text/plain");
    expect(res.status).toBe(200);
    expect(insertCalls()).toHaveLength(1);
    expect(insertCalls()[0][0].args[2]).toBe("secret=[REDACTED] NVDA crossed 180");
  });

  it("malformed JSON with the secret persists redacted with parse_error and returns 200", async () => {
    const body = `{"secret":"${SECRET}","symbol":"NVDA","price":}`;
    const res = await post(TOKEN, body, "text/plain");
    expect(res.status).toBe(200);
    expect(insertCalls()[0][0].args[2]).toBe('{"secret":"[REDACTED]","symbol":"NVDA","price":}');
    const update = mocks.dbExecute.mock.calls[1][0];
    expect(update.args.slice(0, 7)).toEqual([null, null, null, null, null, null, null]);
    expect(update.args[7]).toMatch(/json/i);
  });

  it("a DB failure after auth still returns 200 (TradingView never retries)", async () => {
    mocks.dbExecute.mockRejectedValue(new Error("fetch failed"));
    const res = await post(TOKEN, jsonBody());
    expect(res.status).toBe(200);
  });

  it("accepts either value of a comma-separated rotation pair", async () => {
    vi.stubEnv("TV_WEBHOOK_PATH_TOKEN", `old-token,${TOKEN}`);
    vi.stubEnv("TV_WEBHOOK_SECRET", `${SECRET},new-secret`);
    expect((await post(TOKEN, jsonBody())).status).toBe(200);
    expect((await post("old-token", jsonBody({ secret: "new-secret" }))).status).toBe(200);
  });
});

describe("tvWebhook helpers", () => {
  it("secretMatches rejects empty candidates and empty configs", () => {
    expect(secretMatches("", "a,b")).toBe(false);
    expect(secretMatches("a", "")).toBe(false);
    expect(secretMatches("a", undefined)).toBe(false);
    expect(secretMatches("b", " a , b ")).toBe(true);
  });

  it("parses unquoted-placeholder fallout as text, never throws", () => {
    const parsed = parseTvAlertBody('{"price": , "symbol": "ES1!"}');
    expect(parsed.fields).toBeNull();
    expect(parsed.error).toMatch(/json/i);
  });

  it("non-numeric price stays NULL instead of NaN", () => {
    const parsed = parseTvAlertBody(JSON.stringify({ symbol: "SPX", price: "" }));
    expect(parsed.fields?.price).toBeNull();
    expect(parsed.fields?.symbol).toBe("SPX");
  });
});

describe("no order placement, ever", () => {
  it("route and lib import nothing from lib/order or broker routing", () => {
    for (const rel of ["app/api/webhooks/tradingview/[token]/route.ts", "lib/tvWebhook.ts"]) {
      const src = readFileSync(join(__dirname, "..", rel), "utf8");
      expect(src, rel).not.toMatch(/from\s+["'][^"']*\/order(\/|["'])/);
      expect(src, rel).not.toMatch(/radonFetch|placeOrder|\/api\/orders/);
    }
  });
});

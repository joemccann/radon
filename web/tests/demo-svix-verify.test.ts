import { describe, it, expect } from "vitest";
import { verifySvixSignature } from "@/lib/demo/svixVerify";

// Dummy HMAC key built from fixed bytes at runtime — no secret-looking literal
// in source (keeps the repo secret-scanner quiet). The base64 form is what a
// real whsec_ secret carries.
const KEY_BYTES = new Uint8Array(
  Array.from({ length: 24 }, (_, i) => (i * 7 + 13) % 256),
);
const SECRET_BODY = btoa(String.fromCharCode(...KEY_BYTES));
const SECRET = `whsec_${SECRET_BODY}`;
const ID = "msg_2abc";
const NOW = 1_750_000_000_000; // fixed ms
const TS = String(Math.floor(NOW / 1000));

function bytesToBase64(buf: ArrayBuffer): string {
  const view = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < view.length; i++) s += String.fromCharCode(view[i]);
  return btoa(s);
}

// Re-implement the signing side to produce a valid header for the test.
async function sign(body: string, ts = TS, id = ID): Promise<string> {
  const keyBytes = KEY_BYTES;
  const key = await crypto.subtle.importKey(
    "raw",
    keyBytes,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(`${id}.${ts}.${body}`),
  );
  return `v1,${bytesToBase64(mac)}`;
}

describe("verifySvixSignature", () => {
  it("accepts a correctly signed payload", async () => {
    const body = JSON.stringify({ type: "user.created" });
    const signature = await sign(body);
    const res = await verifySvixSignature({
      secret: SECRET,
      headers: { id: ID, timestamp: TS, signature },
      body,
      now: NOW,
    });
    expect(res.valid).toBe(true);
  });

  it("accepts a multi-signature header where one entry matches", async () => {
    const body = "{}";
    const good = await sign(body);
    const res = await verifySvixSignature({
      secret: SECRET,
      headers: { id: ID, timestamp: TS, signature: `v1,deadbeef ${good}` },
      body,
      now: NOW,
    });
    expect(res.valid).toBe(true);
  });

  it("rejects a tampered body", async () => {
    const signature = await sign(JSON.stringify({ type: "user.created" }));
    const res = await verifySvixSignature({
      secret: SECRET,
      headers: { id: ID, timestamp: TS, signature },
      body: JSON.stringify({ type: "user.deleted" }), // changed after signing
      now: NOW,
    });
    expect(res.valid).toBe(false);
    expect(res.reason).toBe("no signature match");
  });

  it("rejects a stale timestamp (replay)", async () => {
    const body = "{}";
    const signature = await sign(body);
    const res = await verifySvixSignature({
      secret: SECRET,
      headers: { id: ID, timestamp: TS, signature },
      body,
      now: NOW + 10 * 60 * 1000, // 10 min later, tolerance is 5
    });
    expect(res.valid).toBe(false);
    expect(res.reason).toBe("timestamp outside tolerance");
  });

  it("rejects missing headers / secret", async () => {
    expect(
      (await verifySvixSignature({ secret: SECRET, headers: { id: null, timestamp: TS, signature: "v1,x" }, body: "{}", now: NOW })).valid,
    ).toBe(false);
    expect(
      (await verifySvixSignature({ secret: "", headers: { id: ID, timestamp: TS, signature: "v1,x" }, body: "{}", now: NOW })).valid,
    ).toBe(false);
  });

  it("works with the whsec_ prefix omitted", async () => {
    const body = "{}";
    const signature = await sign(body);
    const res = await verifySvixSignature({
      secret: SECRET_BODY, // no whsec_ prefix
      headers: { id: ID, timestamp: TS, signature },
      body,
      now: NOW,
    });
    expect(res.valid).toBe(true);
  });
});

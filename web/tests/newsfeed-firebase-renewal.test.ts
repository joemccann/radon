import { mkdtemp, readFile, rm, stat, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, expect, it, vi } from "vitest";

let root: string;
afterEach(async () => { if (root) await rm(root, { recursive: true, force: true }); });
const seed = "PRIVATE-SEED";
const rotated = "PRIVATE-ROTATED";
const email = "reader@example.invalid";
function token(claims = {}) {
  return `header.${Buffer.from(JSON.stringify({ email, aud: "zerohedge-prod", iss: "https://securetoken.google.com/zerohedge-prod", exp: Date.now() / 1000 + 3600, ...claims })).toString("base64url")}.signature`;
}
async function setup({ claims = {}, refreshStatus = 200, loginStatus = 200, configured = true } = {}) {
  root = await mkdtemp(path.join(tmpdir(), "tme-renew-"));
  const tokenPath = path.join(root, "firebase.json");
  const post = vi.fn<(url: string, options?: { form?: { refresh_token: string } } & Record<string, unknown>) => Promise<{ status: () => number; json?: () => Promise<unknown> }>>(async (url: string) => url.includes("securetoken.googleapis.com")
    ? { status: () => refreshStatus, json: async () => ({ id_token: token(claims), refresh_token: rotated }) }
    : { status: () => loginStatus });
  const env = { THEMARKETEAR_EMAIL: email, ...(configured ? { MARKETDESK_FIREBASE_REFRESH_TOKEN: seed } : {}) };
  return { tokenPath, context: { request: { post }, cookies: vi.fn().mockResolvedValueOnce([{ name: "U", expires: Date.now() / 1000 + 100 }]).mockResolvedValue([{ name: "U", expires: Date.now() / 1000 + 14 * 86400 }]) }, post, env };
}
it("renews a Google-only account using its existing same-project grant and stores rotation privately", async () => {
  const { renewFirebaseSession } = await import("../../scripts/newsfeed/firebaseSession.js");
  const x = await setup();
  expect(await renewFirebaseSession(x)).toBe(true);
  expect(x.post).toHaveBeenNthCalledWith(1, expect.stringMatching(/^https:\/\/securetoken.googleapis.com\/v1\/token\?key=/), expect.objectContaining({ form: { grant_type: "refresh_token", refresh_token: seed }, maxRedirects: 0, timeout: 20_000 }));
  expect(x.post).toHaveBeenNthCalledWith(2, "https://themarketear.com/api/token", expect.objectContaining({ data: { token: expect.any(String) }, maxRedirects: 0 }));
  expect(JSON.parse(await readFile(x.tokenPath, "utf8")).refresh_token).toBe(rotated);
  expect((await stat(x.tokenPath)).mode & 0o777).toBe(0o600);
});
it.each([{ email: "someone-else@example.invalid" }, { aud: "other-project" }, { iss: "https://bad.invalid" }, { exp: 0 }])("rejects identity/project/expired-token mismatch before sending the token to TME: %j", async (claims) => {
  const { renewFirebaseSession } = await import("../../scripts/newsfeed/firebaseSession.js");
  const x = await setup({ claims });
  await expect(renewFirebaseSession(x)).rejects.toThrow(/identity/);
  expect(x.post).toHaveBeenCalledTimes(1);
});
it("uses its own rotated grant before the seed and falls back to a replaced seed", async () => {
  const { renewFirebaseSession } = await import("../../scripts/newsfeed/firebaseSession.js");
  const x = await setup();
  await writeFile(x.tokenPath, JSON.stringify({ refresh_token: "STALE" }));
  x.post.mockImplementationOnce(async () => ({ status: () => 400, json: async () => ({ error: { message: seed } }) }));
  expect(await renewFirebaseSession(x)).toBe(true);
  expect(x.post.mock.calls[0][1]?.form?.refresh_token).toBe("STALE");
  expect(x.post.mock.calls[1][1]?.form?.refresh_token).toBe(seed);
});
it("does not expose provider bodies or secrets on failure", async () => {
  const { renewFirebaseSession } = await import("../../scripts/newsfeed/firebaseSession.js");
  const x = await setup({ refreshStatus: 400 });
  await expect(renewFirebaseSession(x)).rejects.toThrow("Firebase renewal rejected");
  expect(x.post).toHaveBeenCalledTimes(1);
});
it("returns false without a grant and never sends a password", async () => {
  const { renewFirebaseSession } = await import("../../scripts/newsfeed/firebaseSession.js");
  const x = await setup({ configured: false });
  expect(await renewFirebaseSession(x)).toBe(false);
  expect(x.post).not.toHaveBeenCalled();
});
it("preserves the rotated grant when TME rejects the new cookie", async () => {
  const { renewFirebaseSession } = await import("../../scripts/newsfeed/firebaseSession.js");
  const x = await setup({ loginStatus: 403 });
  await expect(renewFirebaseSession(x)).rejects.toThrow(/TME session renewal rejected/);
  expect(JSON.parse(await readFile(x.tokenPath, "utf8")).refresh_token).toBe(rotated);
});
it("refuses a symlink grant file", async () => {
  const { renewFirebaseSession } = await import("../../scripts/newsfeed/firebaseSession.js");
  const x = await setup();
  await writeFile(path.join(root, "target"), "{}");
  await symlink(path.join(root, "target"), x.tokenPath);
  await expect(renewFirebaseSession(x)).rejects.toThrow(/symlink/);
  expect(x.post).not.toHaveBeenCalled();
});

it("requires the product to advance its cookie expiry, not just return HTTP200", async () => {
  const { renewFirebaseSession } = await import("../../scripts/newsfeed/firebaseSession.js");
  const x = await setup();
  x.context.cookies.mockReset().mockResolvedValue([{ name: "U", expires: Date.now() / 1000 + 8 * 86400 }]);
  await expect(renewFirebaseSession(x)).rejects.toThrow(/TME session renewal rejected/);
});
it("redacts network exceptions", async () => {
  const { renewFirebaseSession } = await import("../../scripts/newsfeed/firebaseSession.js");
  const x = await setup();
  x.post.mockRejectedValue(new Error(seed));
  await expect(renewFirebaseSession(x)).rejects.toThrow("Firebase renewal rejected");
});

it.each(["{bad json", "null", "{}"])("recovers from unusable private state using the configured seed: %s", async (contents) => {
  const { renewFirebaseSession } = await import("../../scripts/newsfeed/firebaseSession.js");
  const x = await setup(); await writeFile(x.tokenPath, contents);
  expect(await renewFirebaseSession(x)).toBe(true);
});
it("can replace a stored grant for a previous account without logging it into TME", async () => {
  const { renewFirebaseSession } = await import("../../scripts/newsfeed/firebaseSession.js");
  const x = await setup(); await writeFile(x.tokenPath, JSON.stringify({ refresh_token: "PREVIOUS" }));
  x.post.mockImplementationOnce(async () => ({ status: () => 200, json: async () => ({ id_token: token({ email: "previous@example.invalid" }), refresh_token: "PREVIOUS-ROTATED" }) }));
  expect(await renewFirebaseSession(x)).toBe(true);
  expect(x.post.mock.calls.filter(([url]) => url === "https://themarketear.com/api/token")).toHaveLength(1);
});
it("requires an explicit account identity before sending a configured grant", async () => {
  const { renewFirebaseSession } = await import("../../scripts/newsfeed/firebaseSession.js");
  const x = await setup(); x.env.THEMARKETEAR_EMAIL = "";
  await expect(renewFirebaseSession(x)).rejects.toThrow(/configured account identity/);
  expect(x.post).not.toHaveBeenCalled();
});
it("rejects invalid rotated-grant types and absent product cookies", async () => {
  const { renewFirebaseSession } = await import("../../scripts/newsfeed/firebaseSession.js");
  const x = await setup();
  x.post.mockImplementationOnce(async () => ({ status: () => 200, json: async () => ({ id_token: token(), refresh_token: 42 }) }));
  await expect(renewFirebaseSession(x)).rejects.toThrow(/invalid grant/);
  expect(x.post).toHaveBeenCalledTimes(1);
  x.context.cookies.mockReset().mockResolvedValue([]);
  await expect(renewFirebaseSession(x)).rejects.toThrow(/TME session renewal rejected/);
});
it("bootstraps a new browser with no existing product cookie", async () => {
  const { renewFirebaseSession } = await import("../../scripts/newsfeed/firebaseSession.js");
  const x = await setup();
  x.context.cookies.mockReset().mockResolvedValueOnce([]).mockResolvedValue([{ name: "U", expires: Date.now() / 1000 + 14 * 86400 }]);
  expect(await renewFirebaseSession(x)).toBe(true);
});
it("preserves rotation and redacts product transport failures", async () => {
  const { renewFirebaseSession } = await import("../../scripts/newsfeed/firebaseSession.js");
  const x = await setup();
  x.post.mockImplementationOnce(async () => ({ status: () => 200, json: async () => ({ id_token: token(), refresh_token: rotated }) }));
  x.post.mockRejectedValue(new Error(seed));
  await expect(renewFirebaseSession(x)).rejects.toThrow("TME session renewal rejected");
  expect(JSON.parse(await readFile(x.tokenPath, "utf8")).refresh_token).toBe(rotated);
});
it("rejects malformed tokens before the product login", async () => {
  const { renewFirebaseSession } = await import("../../scripts/newsfeed/firebaseSession.js");
  const x = await setup();
  x.post.mockImplementationOnce(async () => ({ status: () => 200, json: async () => ({ id_token: "bad-jwt", refresh_token: rotated }) }));
  await expect(renewFirebaseSession(x)).rejects.toThrow(/identity mismatch/);
  expect(x.post).toHaveBeenCalledTimes(1);
});
it("rejects non-file grant storage before contacting the provider", async () => {
  const { renewFirebaseSession } = await import("../../scripts/newsfeed/firebaseSession.js");
  const x = await setup();
  await expect(renewFirebaseSession({ ...x, tokenPath: root })).rejects.toThrow("Invalid Firebase grant file");
  expect(x.post).not.toHaveBeenCalled();
});

import path from "node:path";
import { fileURLToPath } from "node:url";
import fs from "fs-extra";
import { atomicWriteJson } from "./atomic.js";

// Public Firebase web configuration served by themarketear.com/js/global-firebase.js.
// MarketDesk uses this same project; the configured account email binds the grant.
const FIREBASE_KEY = "AIzaSyCf8fXJkGM9Jgtzl6sdaMLe_ipwtmWdvNc"; // gitleaks:allow
const REFRESH_URL = `https://securetoken.googleapis.com/v1/token?key=${FIREBASE_KEY}`;
const TOKEN_URL = "https://themarketear.com/api/token";
const DEFAULT_TOKEN_PATH = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../data/newsfeed-firebase.json");
const HEADERS = { Origin: "https://themarketear.com", Referer: "https://themarketear.com/" };

async function readGrant(tokenPath) {
  try {
    const info = await fs.lstat(tokenPath);
    if (info.isSymbolicLink()) throw new Error("Firebase grant file must not be a symlink");
    if (!info.isFile() || info.size > 64 * 1024) throw new Error("Invalid Firebase grant file");
    const value = JSON.parse(await fs.readFile(tokenPath, "utf8"))?.refresh_token;
    return typeof value === "string" ? value.trim() : "";
  } catch (error) {
    if (error.code === "ENOENT" || error instanceof SyntaxError) return "";
    throw error;
  }
}

function identityMatches(idToken, expectedEmail) {
  try {
    // This is a projection of a token received directly from Google's fixed TLS
    // endpoint, not verification of an arbitrary caller-supplied JWT.
    const claims = JSON.parse(Buffer.from(idToken.split(".")[1], "base64url").toString("utf8"));
    return claims.email?.toLowerCase() === expectedEmail.toLowerCase()
      && claims.aud === "zerohedge-prod"
      && claims.iss === "https://securetoken.google.com/zerohedge-prod"
      && Number.isFinite(claims.exp) && claims.exp > Date.now() / 1000;
  } catch { return false; }
}

export async function renewFirebaseSession({ context, env = process.env, tokenPath = DEFAULT_TOKEN_PATH } = {}) {
  const stored = await readGrant(tokenPath);
  const seed = env.MARKETDESK_FIREBASE_REFRESH_TOKEN?.trim();
  const candidates = [...new Set([stored, seed].filter(Boolean))];
  if (!candidates.length) return false;
  const email = env.THEMARKETEAR_EMAIL?.trim();
  if (!email) throw new Error("Firebase renewal requires the configured account identity");

  let payload;
  let identityFailure = false;
  for (const refreshToken of candidates) {
    try {
      const response = await context.request.post(REFRESH_URL, {
        form: { grant_type: "refresh_token", refresh_token: refreshToken },
        headers: HEADERS, timeout: 20_000, maxRedirects: 0,
      });
      const body = await response.json();
      if (response.status() !== 200 || typeof body.id_token !== "string" || !body.id_token) continue;
      if (!identityMatches(body.id_token, email)) { identityFailure = true; continue; }
      payload = { ...body, refresh_token: body.refresh_token || refreshToken };
      break;
    } catch { /* Retry only the independently configured seed; never log provider bodies. */ }
  }
  if (!payload) throw new Error(identityFailure ? "Firebase renewal identity mismatch" : "Firebase renewal rejected");
  if (typeof payload.refresh_token !== "string" || !payload.refresh_token.trim()) {
    throw new Error("Firebase renewal returned an invalid grant");
  }
  // Preserve rotation before the product-cookie request, including if that request fails.
  await atomicWriteJson(tokenPath, { refresh_token: payload.refresh_token, saved_at: new Date().toISOString() }, { mode: 0o600 });
  try {
    const previous = (await context.cookies(TOKEN_URL)).find(cookie => cookie.name === "U");
    const response = await context.request.post(TOKEN_URL, {
      data: { token: payload.id_token }, headers: HEADERS, timeout: 20_000, maxRedirects: 0,
    });
    if (response.status() !== 200) throw new Error();
    const fresh = (await context.cookies(TOKEN_URL)).find(cookie => cookie.name === "U");
    if (!fresh || fresh.expires <= Date.now() / 1000 + 6 * 3600
      || (previous && fresh.expires <= previous.expires)) throw new Error();
  } catch { throw new Error("TME session renewal rejected"); }
  return true;
}

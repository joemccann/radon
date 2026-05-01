import { spawn } from "child_process";
import path from "path";
import fs from "fs-extra";

const PAGES_CACHE = "/tmp/cdp-pages.json";

export const DEFAULT_CDP_PATH = path.join(
  process.env.HOME || "",
  ".claude/skills/chrome-cdp/scripts/cdp.mjs",
);

export function resolveCdpPath() {
  return process.env.CDP_CLI || DEFAULT_CDP_PATH;
}

export async function runCdpCommand(command, ...args) {
  const cdpPath = resolveCdpPath();
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [cdpPath, command, ...args], {
      env: process.env,
    });
    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) {
        resolve(stdout.trim());
      } else {
        reject(new Error(`cdp ${command} failed (${code}): ${stderr || stdout}`));
      }
    });
  });
}

export async function listTargets() {
  await runCdpCommand("list");
  if (!(await fs.pathExists(PAGES_CACHE))) {
    throw new Error("Chrome CDP cache missing after list command.");
  }
  const raw = await fs.readFile(PAGES_CACHE, "utf8");
  return JSON.parse(raw);
}

export function selectMarketEarTab(pages) {
  const candidates = pages.filter((page) => page.url.includes("themarketear.com"));
  if (candidates.length === 0) {
    throw new Error("No Chrome tab with themarketear.com is currently open.");
  }
  return candidates.find((page) => page.url.includes("/newsfeed")) || candidates[0];
}

export function formatCookieHeader(cookies) {
  if (!Array.isArray(cookies)) return "";
  return cookies
    .filter((c) => c && typeof c.name === "string" && typeof c.value === "string")
    .map((c) => `${c.name}=${c.value}`)
    .join("; ");
}

export async function fetchCookieHeader(targetId, urls) {
  const params = JSON.stringify({ urls });
  const raw = await runCdpCommand("evalraw", targetId, "Network.getCookies", params);
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return "";
  }
  return formatCookieHeader(parsed?.cookies);
}

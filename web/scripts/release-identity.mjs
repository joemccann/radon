import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const WEB_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

export function resolveReleaseIdentity({ version, sha, nodeEnv, now }) {
  const clean = /^[0-9a-f]{7,40}$/.test(sha || "") ? sha.slice(0, 12) : "unknown";
  const channel = nodeEnv === "production" && clean !== "unknown" ? "production" : "local";
  return {
    version: version || "0.7.0",
    sha: clean,
    builtAt: channel === "production" ? now : "",
    channel,
  };
}

export function readReleaseIdentity(nodeEnv = process.env.NODE_ENV, now = new Date().toISOString().replace(/\.\d{3}Z$/, "Z")) {
  const version = JSON.parse(readFileSync(resolve(WEB_ROOT, "package.json"), "utf8")).version;
  let sha = "unknown";
  try {
    sha = execFileSync("git", ["rev-parse", "--short=12", "HEAD"], {
      cwd: resolve(WEB_ROOT, ".."),
      encoding: "utf8",
    }).trim();
  } catch {
    sha = "unknown";
  }
  const identity = resolveReleaseIdentity({ version, sha, nodeEnv, now });
  if (nodeEnv === "production" && identity.sha === "unknown") {
    throw new Error("production build has no git SHA for the release identity");
  }
  return identity;
}

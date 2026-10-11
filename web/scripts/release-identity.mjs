import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const WEB_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

export function pickReleaseSha({ override, gitSha, githubSha }) {
  for (const candidate of [override, gitSha, githubSha]) {
    const clean = String(candidate || "").trim().toLowerCase();
    if (/^[0-9a-f]{7,40}$/.test(clean)) return clean.slice(0, 12);
  }
  return "unknown";
}

const SEMVER = /^\d+\.\d+\.\d+$/;

// The version is derived from commit history, never committed by a PR. The
// image build has no .git, so it receives the derived value as an override.
export function pickReleaseVersion({ override, history, packageVersion }) {
  const pinned = String(override || "").trim();
  if (SEMVER.test(pinned)) return pinned;
  try {
    const derived = String(history() || "").trim();
    if (SEMVER.test(derived)) return derived;
  } catch {
    // no history available; fall through to the package field
  }
  return packageVersion;
}

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
  const version = pickReleaseVersion({
    override: process.env.RADON_RELEASE_VERSION,
    history: () => execFileSync("python3", [resolve(WEB_ROOT, "..", "scripts", "release_version.py")], {
      cwd: resolve(WEB_ROOT, ".."),
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }),
    packageVersion: JSON.parse(readFileSync(resolve(WEB_ROOT, "package.json"), "utf8")).version,
  });
  let gitSha = "";
  try {
    gitSha = execFileSync("git", ["rev-parse", "--short=12", "HEAD"], {
      cwd: resolve(WEB_ROOT, ".."),
      encoding: "utf8",
    }).trim();
  } catch {
    gitSha = "";
  }
  const sha = pickReleaseSha({
    override: process.env.RADON_RELEASE_SHA,
    gitSha,
    githubSha: process.env.GITHUB_SHA,
  });
  const identity = resolveReleaseIdentity({ version, sha, nodeEnv, now });
  if (nodeEnv === "production" && identity.sha === "unknown") {
    throw new Error("production build has no git SHA for the release identity");
  }
  return identity;
}

#!/usr/bin/env node

/**
 * Vercel Ignored Build Step for the radon-demo project (demo.radon.run).
 *
 * Vercel Root Directory is `web/`. Skip the build when the commit touches
 * neither the Next app nor the `@tools` alias target (`lib/tools`), which
 * next.config.mjs resolves into the webpack graph.
 *
 * Exit codes (Vercel convention):
 *   0 → skip build
 *   1 → continue build
 *
 * Defaults to CONTINUE when the diff cannot be determined (safer than
 * silently skipping a real app change).
 */

import { execFileSync } from "node:child_process";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

/** Paths relative to the repo root that can change the demo frontend build. */
export const DEMO_WATCH_REL_PATHS = ["web", path.join("lib", "tools")];

export function git(args, opts = {}) {
  return execFileSync("git", args, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    ...opts,
  }).trim();
}

export function safeRevParse(ref) {
  try {
    return git(["rev-parse", ref]);
  } catch {
    return null;
  }
}

/**
 * Map watch dirs to pathspecs relative to `cwd` (Vercel runs ignoreCommand
 * from the project Root Directory, usually `web/`).
 */
export function resolveWatchDiffPaths({
  repoRoot,
  cwd,
  watchRelPaths = DEMO_WATCH_REL_PATHS,
}) {
  return watchRelPaths.map((rel) => {
    const abs = path.join(repoRoot, rel);
    if (path.resolve(cwd) === path.resolve(abs)) return ".";
    return path.relative(cwd, abs) || ".";
  });
}

/**
 * Interpret `git diff --quiet` status for the Vercel ignoreCommand.
 * Returns the process exit code to use.
 */
export function ignoreExitFromDiffStatus(status) {
  if (status === 0) return 0; // no changes → skip
  if (status === 1) return 1; // changes → build
  return 1; // unknown → build
}

export function defaultResolveRepoRoot() {
  return git(["rev-parse", "--show-toplevel"]);
}

export function defaultResolveShas(env) {
  return {
    headSha: env.VERCEL_GIT_COMMIT_SHA?.trim() || safeRevParse("HEAD"),
    baseSha: env.VERCEL_GIT_PREVIOUS_SHA?.trim() || safeRevParse("HEAD^"),
  };
}

export function defaultExecDiffQuiet(base, head, paths) {
  try {
    execFileSync("git", ["diff", "--quiet", base, head, "--", ...paths], {
      stdio: "ignore",
    });
    return 0;
  } catch (error) {
    if (error && typeof error === "object" && "status" in error) {
      return Number(error.status);
    }
    return 128;
  }
}

export function main({
  env = process.env,
  cwd = process.cwd(),
  resolveRepoRoot = defaultResolveRepoRoot,
  resolveShas = defaultResolveShas,
  execDiffQuiet = defaultExecDiffQuiet,
  log = console.log,
} = {}) {
  let repoRoot;
  try {
    repoRoot = resolveRepoRoot();
  } catch {
    log("Not running inside a git checkout. Continuing build.");
    return 1;
  }

  const { headSha, baseSha } = resolveShas(env);

  if (!headSha || !baseSha) {
    log("No previous commit available. Continuing build.");
    return 1;
  }

  const diffPaths = resolveWatchDiffPaths({ repoRoot, cwd });
  const logPaths = DEMO_WATCH_REL_PATHS.map((p) => `${p}/`).join(" + ");

  const status = execDiffQuiet(baseSha, headSha, diffPaths);
  const exitCode = ignoreExitFromDiffStatus(status);
  if (exitCode === 0) {
    log(`No changes detected under ${logPaths}. Skipping Vercel build.`);
  } else if (status === 1) {
    log(`Changes detected under ${logPaths}. Continuing Vercel build.`);
  } else {
    log("Unable to determine changed files. Continuing build.");
  }
  return exitCode;
}

const isCli =
  typeof process.argv[1] === "string" &&
  import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href;

if (isCli) {
  process.exit(main());
}

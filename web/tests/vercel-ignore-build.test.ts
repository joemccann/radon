/**
 * radon-demo Vercel Ignored Build Step — skip when neither web/ nor
 * lib/tools changed (mirrors site/scripts/vercel-ignore-build.mjs).
 */
import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import {
  DEMO_WATCH_REL_PATHS,
  ignoreExitFromDiffStatus,
  main,
  resolveWatchDiffPaths,
} from "../scripts/vercel-ignore-build.mjs";

const REPO = "/repo";

describe("resolveWatchDiffPaths", () => {
  it("maps watch dirs relative to web/ Root Directory cwd", () => {
    expect(
      resolveWatchDiffPaths({
        repoRoot: REPO,
        cwd: path.join(REPO, "web"),
      }),
    ).toEqual([".", path.join("..", "lib", "tools")]);
  });

  it("maps watch dirs relative to repo-root cwd", () => {
    expect(
      resolveWatchDiffPaths({
        repoRoot: REPO,
        cwd: REPO,
      }),
    ).toEqual(["web", path.join("lib", "tools")]);
  });

  it("exports the demo watch set used by radon-demo", () => {
    expect(DEMO_WATCH_REL_PATHS).toEqual(["web", path.join("lib", "tools")]);
  });
});

describe("ignoreExitFromDiffStatus", () => {
  it("skips (0) when git diff --quiet reports no changes", () => {
    expect(ignoreExitFromDiffStatus(0)).toBe(0);
  });

  it("continues (1) when git diff --quiet reports changes", () => {
    expect(ignoreExitFromDiffStatus(1)).toBe(1);
  });

  it("continues (1) on unknown status (fail open to build)", () => {
    expect(ignoreExitFromDiffStatus(128)).toBe(1);
  });
});

describe("main", () => {
  const shas = {
    headSha: "head",
    baseSha: "base",
  };

  it("skips when the watched paths are unchanged", () => {
    const log = vi.fn();
    const execDiffQuiet = vi.fn(() => 0);
    const code = main({
      env: {},
      cwd: path.join(REPO, "web"),
      resolveRepoRoot: () => REPO,
      resolveShas: () => shas,
      execDiffQuiet,
      log,
    });
    expect(code).toBe(0);
    expect(execDiffQuiet).toHaveBeenCalledWith("base", "head", [
      ".",
      path.join("..", "lib", "tools"),
    ]);
    expect(log).toHaveBeenCalledWith(
      expect.stringContaining("Skipping Vercel build"),
    );
  });

  it("continues when watched paths changed", () => {
    const log = vi.fn();
    const code = main({
      env: {},
      cwd: path.join(REPO, "web"),
      resolveRepoRoot: () => REPO,
      resolveShas: () => shas,
      execDiffQuiet: () => 1,
      log,
    });
    expect(code).toBe(1);
    expect(log).toHaveBeenCalledWith(
      expect.stringContaining("Continuing Vercel build"),
    );
  });

  it("continues when commit SHAs are missing", () => {
    const log = vi.fn();
    const code = main({
      env: {},
      cwd: path.join(REPO, "web"),
      resolveRepoRoot: () => REPO,
      resolveShas: () => ({ headSha: null, baseSha: null }),
      execDiffQuiet: () => 0,
      log,
    });
    expect(code).toBe(1);
    expect(log).toHaveBeenCalledWith(
      expect.stringContaining("No previous commit available"),
    );
  });

  it("continues when not in a git checkout", () => {
    const log = vi.fn();
    const code = main({
      env: {},
      cwd: path.join(REPO, "web"),
      resolveRepoRoot: () => {
        throw new Error("not a git repo");
      },
      resolveShas: () => shas,
      execDiffQuiet: () => 0,
      log,
    });
    expect(code).toBe(1);
    expect(log).toHaveBeenCalledWith(
      expect.stringContaining("Not running inside a git checkout"),
    );
  });
});

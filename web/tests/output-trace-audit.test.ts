import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, relative, resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  auditOutputTraces,
  DEFAULT_MAX_BYTES,
  DEFAULT_MAX_FILES,
} from "../scripts/audit-output-traces.mjs";

const tempRoots: string[] = [];

async function makeRepo(): Promise<{ repoRoot: string; webRoot: string }> {
  const repoRoot = await mkdtemp(join(tmpdir(), "radon-output-trace-"));
  const webRoot = join(repoRoot, "web");
  tempRoots.push(repoRoot);
  return { repoRoot, webRoot };
}

async function writeTrace(
  webRoot: string,
  route: string,
  files: Array<{ path: string; contents: string }>,
): Promise<void> {
  const manifest = join(
    webRoot,
    ".next",
    "server",
    "app",
    route,
    "route.js.nft.json",
  );
  await mkdir(dirname(manifest), { recursive: true });
  for (const file of files) {
    await mkdir(dirname(file.path), { recursive: true });
    await writeFile(file.path, file.contents);
  }
  await writeFile(
    manifest,
    JSON.stringify({
      version: 1,
      files: files.map((file) => relative(dirname(manifest), file.path)),
    }),
  );
}

afterEach(async () => {
  await Promise.all(tempRoots.splice(0).map((root) => rm(root, {
    recursive: true,
    force: true,
  })));
});

describe("production output trace audit", () => {
  it("pins deployment ceilings below the previous global-trace footprint", () => {
    expect(DEFAULT_MAX_FILES).toBe(4_000);
    expect(DEFAULT_MAX_BYTES).toBe(128 * 1024 * 1024);
  });

  it("allows a bounded route-owned data fallback", async () => {
    const { repoRoot, webRoot } = await makeRepo();
    await writeTrace(webRoot, "api/example", [
      { path: join(webRoot, "server-runtime.js"), contents: "runtime" },
      {
        path: join(repoRoot, "data", "menthorq_cache", "cta.json"),
        contents: "cache",
      },
    ]);

    const result = await auditOutputTraces({ webRoot, maxFiles: 10, maxBytes: 100 });

    expect(result.manifestCount).toBe(1);
    expect(result.worst.count).toBe(2);
    expect(result.worst.bytes).toBe(12);
  });

  it.each(["db_backups", "journal_archive"])(
    "rejects the catastrophic data/%s tree",
    async (directory) => {
      const { repoRoot, webRoot } = await makeRepo();
      await writeTrace(webRoot, "api/example", [{
        path: join(repoRoot, "data", directory, "snapshot.bin"),
        contents: "archive",
      }]);

      await expect(auditOutputTraces({ webRoot })).rejects.toThrow(
        `data/${directory}`,
      );
    },
  );

  it("rejects unreasonable per-route file counts and bytes", async () => {
    const { webRoot } = await makeRepo();
    await writeTrace(webRoot, "api/example", [
      { path: join(webRoot, "one.js"), contents: "12345" },
      { path: join(webRoot, "two.js"), contents: "67890" },
    ]);

    await expect(
      auditOutputTraces({ webRoot, maxFiles: 1, maxBytes: 100 }),
    ).rejects.toThrow("2 files");
    await expect(
      auditOutputTraces({ webRoot, maxFiles: 10, maxBytes: 9 }),
    ).rejects.toThrow("10 bytes");
  });

  it("runs the auditor after every production build", async () => {
    const packageJson = JSON.parse(
      await readFile(resolve(__dirname, "../package.json"), "utf8"),
    ) as { scripts: Record<string, string> };

    expect(packageJson.scripts.build).toBe(
      "next build --experimental-build-mode=compile && "
      + "node scripts/audit-output-traces.mjs",
    );
  });

  it("keeps the opt-in replica path runtime-correct but opaque to Turbopack", async () => {
    const source = await readFile(resolve(__dirname, "../lib/db.ts"), "utf8");

    expect(source).toContain(
      'path.resolve(/* turbopackIgnore: true */ process.cwd(), "..", "data")',
    );
    expect(source).toContain('path.join(projectRoot(), "replica.db")');
  });
});

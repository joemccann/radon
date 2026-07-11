import { readdir, readFile, stat } from "node:fs/promises";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

export const DEFAULT_MAX_FILES = 4_000;
export const DEFAULT_MAX_BYTES = 128 * 1024 * 1024;

const FORBIDDEN_DATA_TREES = ["data/db_backups", "data/journal_archive"];

function normalizePath(value) {
  return value.split(sep).join("/");
}

function isWithin(relativePath, directory) {
  return relativePath === directory || relativePath.startsWith(`${directory}/`);
}

async function findTraceManifests(root) {
  const manifests = [];

  async function walk(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) {
        await walk(path);
      } else if (entry.name.endsWith(".js.nft.json")) {
        manifests.push(path);
      }
    }
  }

  await walk(root);
  return manifests.sort();
}

function formatMiB(bytes) {
  return `${(bytes / 1024 / 1024).toFixed(2)} MiB`;
}

export async function auditOutputTraces({
  webRoot = process.cwd(),
  maxFiles = DEFAULT_MAX_FILES,
  maxBytes = DEFAULT_MAX_BYTES,
} = {}) {
  const resolvedWebRoot = resolve(webRoot);
  const repoRoot = resolve(resolvedWebRoot, "..");
  const traceRoot = join(resolvedWebRoot, ".next", "server", "app");
  const manifests = await findTraceManifests(traceRoot);
  if (manifests.length === 0) {
    throw new Error(`output trace audit found no app manifests under ${traceRoot}`);
  }

  const statCache = new Map();
  const failures = [];
  const rows = [];

  for (const manifest of manifests) {
    const parsed = JSON.parse(await readFile(manifest, "utf8"));
    if (!Array.isArray(parsed.files) || parsed.files.some((file) => typeof file !== "string")) {
      failures.push(`${normalizePath(relative(traceRoot, manifest))}: invalid NFT files array`);
      continue;
    }

    const route = normalizePath(
      relative(traceRoot, manifest).replace(/\.js\.nft\.json$/, ""),
    );
    let bytes = 0;
    let dataCount = 0;
    let dataBytes = 0;
    const forbidden = new Set();

    for (const tracedFile of parsed.files) {
      const absolutePath = resolve(dirname(manifest), tracedFile);
      let pendingStat = statCache.get(absolutePath);
      if (!pendingStat) {
        pendingStat = stat(absolutePath);
        statCache.set(absolutePath, pendingStat);
      }

      let fileStat;
      try {
        fileStat = await pendingStat;
      } catch {
        failures.push(`${route}: missing traced file ${absolutePath}`);
        continue;
      }

      bytes += fileStat.size;
      const repoRelative = normalizePath(relative(repoRoot, absolutePath));
      if (isWithin(repoRelative, "data")) {
        dataCount += 1;
        dataBytes += fileStat.size;
      }
      for (const directory of FORBIDDEN_DATA_TREES) {
        if (isWithin(repoRelative, directory)) forbidden.add(directory);
      }
    }

    const row = {
      route,
      count: parsed.files.length,
      bytes,
      dataCount,
      dataBytes,
    };
    rows.push(row);

    if (forbidden.size > 0) {
      failures.push(`${route}: contains ${[...forbidden].join(", ")}`);
    }
    if (row.count > maxFiles) {
      failures.push(`${route}: ${row.count} files exceeds limit ${maxFiles}`);
    }
    if (row.bytes > maxBytes) {
      failures.push(
        `${route}: ${row.bytes} bytes (${formatMiB(row.bytes)}) exceeds limit ${maxBytes}`,
      );
    }
  }

  if (failures.length > 0) {
    throw new Error(`output trace audit failed:\n- ${failures.join("\n- ")}`);
  }

  const worst = rows.reduce((current, row) => row.bytes > current.bytes ? row : current);
  const largestFileCount = rows.reduce(
    (current, row) => row.count > current.count ? row : current,
  );
  const routesWithData = rows.filter((row) => row.dataCount > 0);

  return {
    manifestCount: manifests.length,
    worst,
    largestFileCount,
    routesWithData,
  };
}

const invokedAsScript = process.argv[1]
  && resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (invokedAsScript) {
  auditOutputTraces()
    .then((result) => {
      console.log(
        `Output trace audit passed: ${result.manifestCount} manifests; `
        + `max ${result.largestFileCount.count} files (${result.largestFileCount.route}); `
        + `max ${formatMiB(result.worst.bytes)} (${result.worst.route}); `
        + `${result.routesWithData.length} routes retain explicit data files.`,
      );
    })
    .catch((error) => {
      console.error(error instanceof Error ? error.message : error);
      process.exitCode = 1;
    });
}

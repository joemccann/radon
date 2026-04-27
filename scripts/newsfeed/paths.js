import path from "path";
import { fileURLToPath } from "url";
import fs from "fs-extra";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PROJECT_ROOT = path.resolve(__dirname, "..", "..");
const WEB_ROOT = path.join(PROJECT_ROOT, "web");
const PUBLIC_ROOT = path.join(WEB_ROOT, "public");

function resolveCandidate(candidate, fallback) {
  if (!candidate) return fallback;
  if (path.isAbsolute(candidate)) return candidate;
  return path.resolve(PROJECT_ROOT, candidate);
}

export function resolveScraperPaths(overrides = {}) {
  const dataDir = resolveCandidate(
    overrides.dataDir ?? process.env.RADON_NEWSFEED_DATA_DIR,
    path.join(PUBLIC_ROOT, "data"),
  );
  const postsFile = resolveCandidate(
    overrides.postsFile ?? process.env.RADON_NEWSFEED_POSTS_FILE,
    path.join(dataDir, "posts.json"),
  );
  const archiveDir = resolveCandidate(
    overrides.archiveDir ?? process.env.RADON_NEWSFEED_ARCHIVE_DIR,
    path.join(dataDir, "archive"),
  );
  const mediaDir = resolveCandidate(
    overrides.mediaDir ?? process.env.RADON_NEWSFEED_MEDIA_DIR,
    path.join(PUBLIC_ROOT, "media"),
  );
  const publicRoot = resolveCandidate(
    overrides.publicRoot ?? process.env.RADON_NEWSFEED_PUBLIC_ROOT,
    PUBLIC_ROOT,
  );

  return {
    projectRoot: PROJECT_ROOT,
    webRoot: WEB_ROOT,
    publicRoot,
    dataDir,
    archiveDir,
    mediaDir,
    postsFile,
  };
}

export async function seedPostsFileIfMissing(overrides = {}) {
  const { dataDir, postsFile } = resolveScraperPaths(overrides);
  await fs.ensureDir(dataDir);

  const exists = await fs.pathExists(postsFile);
  if (exists) {
    const contents = await fs.readFile(postsFile, "utf8").catch(() => "");
    if (contents.trim().length === 0) {
      await fs.writeFile(postsFile, JSON.stringify([], null, 2));
      return true;
    }
    return false;
  }

  await fs.writeFile(postsFile, JSON.stringify([], null, 2));
  return true;
}

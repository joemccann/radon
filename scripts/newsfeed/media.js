import path from "path";
import fs from "fs-extra";
import https from "node:https";
import axios from "axios";

const BASE_URL = new URL("https://themarketear.com");

// Force IPv4 — themarketear.com's CDN advertises AAAA but those routes are
// frequently unreachable from residential IPv6, causing EHOSTUNREACH timeouts
// while curl-style IPv4 succeeds.
const ipv4Agent = new https.Agent({ family: 4, keepAlive: true });

const defaultClient = axios.create({
  timeout: 20000,
  responseType: "arraybuffer",
  maxRedirects: 5,
  httpsAgent: ipv4Agent,
});

function slugify(value) {
  return (
    value
      .toString()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60) || "post"
  );
}

export function createImageDownloader({ mediaDir, client = defaultClient, getCookieHeader } = {}) {
  if (!mediaDir) throw new Error("createImageDownloader requires mediaDir");
  const cache = new Map();

  async function resolveCookieHeader() {
    if (typeof getCookieHeader !== "function") return null;
    try {
      const value = await getCookieHeader();
      return typeof value === "string" && value.length > 0 ? value : null;
    } catch (err) {
      console.warn(`[newsfeed] cookie lookup failed: ${err.message}`);
      return null;
    }
  }

  async function download(postId, urls) {
    if (!Array.isArray(urls) || urls.length === 0) return [];

    const cookieHeader = await resolveCookieHeader();
    const requestOptions = cookieHeader ? { headers: { Cookie: cookieHeader } } : undefined;

    const tasks = urls.map(async (remoteUrl, index) => {
      const absoluteUrl = new URL(remoteUrl, BASE_URL).toString();
      if (cache.has(absoluteUrl)) return cache.get(absoluteUrl);

      const urlObj = new URL(absoluteUrl);
      const rawName = path.basename(urlObj.pathname) || `${slugify(postId)}-${index}`;
      const [, maybeExt] = rawName.split(/(?=\.[^.]+$)/);
      const ext = maybeExt && maybeExt.length <= 6 ? maybeExt : ".png";
      const filename = `${slugify(postId)}-${String(index + 1).padStart(2, "0")}${ext}`;
      const destPath = path.join(mediaDir, filename);
      const publicPath = `/media/${filename}`;

      if (!(await fs.pathExists(destPath))) {
        try {
          const response = await client.get(absoluteUrl, requestOptions);
          await fs.writeFile(destPath, response.data);
        } catch (err) {
          console.warn(`[newsfeed] image download failed ${absoluteUrl}: ${err.message}`);
          return null;
        }
      }

      cache.set(absoluteUrl, publicPath);
      return publicPath;
    });

    const results = await Promise.all(tasks);
    return results.filter(Boolean);
  }

  return { download };
}

export async function hydrateLocalImages(posts, downloader) {
  let updated = false;
  for (const post of posts) {
    if (!Array.isArray(post.rawImages) || post.rawImages.length === 0) continue;
    const localImages = await downloader.download(post.id, post.rawImages);
    if (localImages.length > 0 && JSON.stringify(localImages) !== JSON.stringify(post.images || [])) {
      post.images = localImages;
      updated = true;
    }
  }
  return updated;
}

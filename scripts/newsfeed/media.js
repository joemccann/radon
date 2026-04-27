import path from "path";
import fs from "fs-extra";
import axios from "axios";

const BASE_URL = new URL("https://themarketear.com");

const defaultClient = axios.create({
  timeout: 20000,
  responseType: "arraybuffer",
  maxRedirects: 3,
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

export function createImageDownloader({ mediaDir, client = defaultClient } = {}) {
  if (!mediaDir) throw new Error("createImageDownloader requires mediaDir");
  const cache = new Map();

  async function download(postId, urls) {
    if (!Array.isArray(urls) || urls.length === 0) return [];

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
          const response = await client.get(absoluteUrl);
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

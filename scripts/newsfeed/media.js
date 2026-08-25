import path from "path";
import crypto from "node:crypto";
import https from "node:https";
import dns from "node:dns";
import net from "node:net";
import axios from "axios";
import sharp from "sharp";
import { writePublicMediaFile } from "./mediaPermissions.js";

const BASE_URL = new URL("https://themarketear.com");

// Single source of truth for the public media host. Posts written to disk,
// to Turso, and rendered by the dashboard ALL carry absolute URLs rooted
// here. The Hetzner peer has no /media/<file> static route — only Caddy at
// media.radon.run serves these — so a relative path produces a 400 from
// Next.js's image optimiser on app.radon.run.
export const MEDIA_ORIGIN = "https://media.radon.run";

// Idempotent rewrite: filenames, relative `/media/<f>`, and already-absolute
// `https://media.radon.run/<f>` all collapse to a single absolute form.
// Foreign absolute URLs (e.g. third-party CDN images we haven't downloaded
// yet) pass through unchanged so the contract stays additive.
export function absolutizeMediaUrl(src) {
  if (typeof src !== "string" || src.length === 0) return src;
  if (src.startsWith(`${MEDIA_ORIGIN}/`)) return src;
  if (src.startsWith("/media/")) return `${MEDIA_ORIGIN}/${src.slice("/media/".length)}`;
  if (src.startsWith("https://") || src.startsWith("http://")) return src;
  return src;
}

// post.rawImages comes verbatim from third-party article markup (extract.js
// reads every <img> src/data-src). The scraper runs on the production VPS and
// everything it downloads is rsync'd to the public media host, so an unfiltered
// fetch turns a planted <img src="http://127.0.0.1:8321/health"> into a proxy
// for the trusted loopback perimeter with the response republished worldwide.
// Only these origins may be fetched; everything else is refused before a socket
// opens. themarketear.com serves /images/<hash>.png, which 301s to its
// digitaloceanspaces CDN.
const ALLOWED_IMAGE_DOMAINS = ["themarketear.com", "digitaloceanspaces.com"];

// The operator's authenticated themarketear.com session cookie must never ride
// along to any other host: an attacker whose <img> lands in a feed article
// would read the premium session out of their own access log and replay it.
const COOKIE_SCOPED_DOMAINS = ["themarketear.com"];

function matchesDomain(hostname, domain) {
  return hostname === domain || hostname.endsWith(`.${domain}`);
}

export function isAllowedImageUrl(url) {
  if (url.protocol !== "https:") return false;
  return ALLOWED_IMAGE_DOMAINS.some((domain) => matchesDomain(url.hostname, domain));
}

export function isCookieScopedHost(hostname) {
  return COOKIE_SCOPED_DOMAINS.some((domain) => matchesDomain(hostname, domain));
}

function isPrivateIpv4(address) {
  const [a, b] = address.split(".").map(Number);
  if (a === 0 || a === 10 || a === 127) return true;
  if (a === 169 && b === 254) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  if (a === 100 && b >= 64 && b <= 127) return true;
  if (a === 198 && (b === 18 || b === 19)) return true;
  return a >= 224;
}

export function isPublicAddress(address) {
  const version = net.isIP(address);
  if (version === 4) return !isPrivateIpv4(address);
  if (version !== 6) return false;

  const normalised = address.toLowerCase().split("%")[0];
  const mapped = normalised.match(/^::ffff:(\d+\.\d+\.\d+\.\d+)$/);
  if (mapped) return !isPrivateIpv4(mapped[1]);
  if (normalised === "::1" || normalised === "::") return false;
  if (/^f[cd]/.test(normalised)) return false;
  if (/^fe[89ab]/.test(normalised)) return false;
  return true;
}

// A hostname allowlist alone does not stop a DNS answer that points at a
// private address (rebinding, or a CDN record poisoned to 127.0.0.1). Checking
// at connect time also covers every redirect hop, which URL-level checks miss.
export function createGuardedLookup(baseLookup = dns.lookup) {
  return function guardedLookup(hostname, options, callback) {
    const done = typeof options === "function" ? options : callback;
    const lookupOptions = typeof options === "function" ? {} : options;
    baseLookup(hostname, lookupOptions, (err, address, family) => {
      if (err) return done(err);
      const resolved = Array.isArray(address) ? address : [{ address, family }];
      const blocked = resolved.find((entry) => !isPublicAddress(entry.address));
      if (blocked) {
        return done(new Error(`${hostname} resolved to non-public address ${blocked.address}`));
      }
      return done(null, address, family);
    });
  };
}

// Force IPv4 — themarketear.com's CDN advertises AAAA but those routes are
// frequently unreachable from residential IPv6, causing EHOSTUNREACH timeouts
// while curl-style IPv4 succeeds.
const ipv4Agent = new https.Agent({ family: 4, keepAlive: true, lookup: createGuardedLookup() });

const defaultClient = axios.create({
  timeout: 20000,
  responseType: "arraybuffer",
  // Redirects are followed manually so every hop receives the same URL,
  // hostname, DNS, and cookie-scope checks as the initial request.
  maxRedirects: 0,
  httpsAgent: ipv4Agent,
});

const RASTER_FORMATS = [
  { format: "png", ext: ".png", mime: "image/png", signature: [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a] },
  { format: "jpeg", ext: ".jpg", mime: "image/jpeg", signature: [0xff, 0xd8, 0xff] },
  { format: "gif", ext: ".gif", mime: "image/gif", signature: [0x47, 0x49, 0x46, 0x38] },
  { format: "bmp", ext: ".bmp", mime: "image/bmp", signature: [0x42, 0x4d] },
];
const MAX_IMAGE_BYTES = 20 * 1024 * 1024;
const MAX_IMAGE_PIXELS = 16_000_000;
const SAFE_OUTPUT = { format: "png", ext: ".png", mime: "image/png" };

// Bytes that land here are served unauthenticated from media.radon.run with
// Access-Control-Allow-Origin *, so only real raster images may be written —
// never an HTML/JSON body from a misrouted fetch, never an SVG (scriptable).
export function looksLikeImage(data) {
  return detectRasterFormat(data) !== null;
}

export function detectRasterFormat(data) {
  const bytes = Buffer.isBuffer(data) ? data : Buffer.from(data ?? []);
  if (bytes.length < 12) return null;
  if (bytes.slice(0, 4).toString("latin1") === "RIFF" && bytes.slice(8, 12).toString("latin1") === "WEBP") {
    return { format: "webp", ext: ".webp", mime: "image/webp" };
  }
  return RASTER_FORMATS.find(({ signature }) =>
    signature.every((byte, offset) => bytes[offset] === byte),
  ) ?? null;
}

function readContentType(headers) {
  if (!headers || typeof headers !== "object") return "";
  const raw = headers["content-type"] ?? headers["Content-Type"];
  return typeof raw === "string" ? raw.toLowerCase() : "";
}

async function sanitizeImagePayload(response) {
  const encoded = Buffer.isBuffer(response?.data) ? response.data : Buffer.from(response?.data ?? []);
  if (encoded.length === 0 || encoded.length > MAX_IMAGE_BYTES) {
    throw new Error("response body exceeds raster input bounds");
  }
  const detected = detectRasterFormat(encoded);
  if (!detected) {
    throw new Error("response body is not a supported raster image");
  }
  const contentType = readContentType(response?.headers).split(";", 1)[0].trim();
  if (contentType && contentType !== detected.mime) {
    throw new Error(`unexpected content-type ${contentType}`);
  }

  const image = sharp(encoded, {
    failOn: "error",
    limitInputPixels: MAX_IMAGE_PIXELS,
    sequentialRead: true,
  });
  const metadata = await image.metadata();
  if (metadata.format !== detected.format || !metadata.width || !metadata.height) {
    throw new Error("raster decoder format mismatch");
  }
  if (metadata.width * metadata.height > MAX_IMAGE_PIXELS) {
    throw new Error("decoded raster exceeds pixel bounds");
  }

  // Decode and publish a newly encoded, single-frame PNG. This strips trailing
  // polyglot content, metadata, profiles, and any source-format active payload.
  const data = await image
    .rotate()
    .png({ compressionLevel: 9, palette: false })
    .toBuffer();
  return { ...SAFE_OUTPUT, data };
}

const MAX_IMAGE_REDIRECTS = 5;

async function fetchAllowedImage(client, initialUrl, cookieHeader) {
  let current = new URL(initialUrl);
  for (let redirects = 0; redirects <= MAX_IMAGE_REDIRECTS; redirects += 1) {
    if (!isAllowedImageUrl(current)) {
      throw new Error(`redirected to untrusted origin ${current.toString()}`);
    }
    const headers = cookieHeader && isCookieScopedHost(current.hostname)
      ? { Cookie: cookieHeader }
      : undefined;
    const response = await client.get(current.toString(), {
      ...(headers ? { headers } : {}),
      maxRedirects: 0,
      validateStatus: (status) => status >= 200 && status < 400,
    });
    const status = Number(response?.status ?? 200);
    if (status >= 300 && status < 400) {
      if (redirects === MAX_IMAGE_REDIRECTS) throw new Error("too many image redirects");
      const location = response?.headers?.location ?? response?.headers?.Location;
      if (typeof location !== "string" || !location) throw new Error("image redirect missing location");
      current = new URL(location, current);
      continue;
    }
    if (status < 200 || status >= 300) throw new Error(`unexpected image status ${status}`);
    return response;
  }
  throw new Error("too many image redirects");
}

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

// R-175: `download` fanned out one concurrent request per <img> with no
// pool, and `post.rawImages` comes verbatim from third-party article markup
// (see the module comment above) — so the fan-out width was
// attacker-influenced, and each in-flight task holds a decoded buffer. The
// URL map was also never evicted in a process that cycles every 120 s.
export const IMAGE_DOWNLOAD_CONCURRENCY = 4;
export const MAX_IMAGES_PER_POST = 12;
export const MAX_URL_CACHE_ENTRIES = 2000;

export function createImageDownloader({ mediaDir, client = defaultClient, getCookieHeader } = {}) {
  if (!mediaDir) throw new Error("createImageDownloader requires mediaDir");
  const cache = new Map();

  function remember(key, value) {
    // Insertion-ordered Map: the oldest key is the first one iterated.
    if (cache.size >= MAX_URL_CACHE_ENTRIES) {
      const oldest = cache.keys().next().value;
      if (oldest !== undefined) cache.delete(oldest);
    }
    cache.set(key, value);
  }

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
    const wanted = urls.slice(0, MAX_IMAGES_PER_POST);
    if (urls.length > wanted.length) {
      console.warn(
        `[newsfeed] ${postId}: ${urls.length} images offered, downloading the first ${wanted.length}`,
      );
    }

    const fetchOne = async (remoteUrl, index) => {
      const urlObj = new URL(remoteUrl, BASE_URL);
      const absoluteUrl = urlObj.toString();
      if (cache.has(absoluteUrl)) return cache.get(absoluteUrl);

      if (!isAllowedImageUrl(urlObj)) {
        console.warn(`[newsfeed] refusing image from untrusted origin: ${absoluteUrl}`);
        return null;
      }

      try {
        const response = await fetchAllowedImage(client, absoluteUrl, cookieHeader);
        const format = await sanitizeImagePayload(response);
        const contentDigest = crypto.createHash("sha256").update(format.data).digest("hex").slice(0, 12);
        const filename = `${slugify(postId)}-${String(index + 1).padStart(2, "0")}-${contentDigest}${format.ext}`;
        const destPath = path.join(mediaDir, filename);
        // Absolute URL — see MEDIA_ORIGIN above. The dashboard never gets a
        // chance to optimise `/media/<f>` because that path 404s on Hetzner.
        const publicPath = `${MEDIA_ORIGIN}/${filename}`;

        // Always replace a legacy file after validation so an older raw or
        // extension-derived publication cannot survive the hardened ingest.
        // chmod 0644 after write — UMask=0077 would otherwise leave 0600 and
        // Caddy 403s media.radon.run (see mediaPermissions.js).
        await writePublicMediaFile(destPath, format.data);
        remember(absoluteUrl, publicPath);
        return publicPath;
      } catch (err) {
        console.warn(`[newsfeed] image download failed ${absoluteUrl}: ${err.message}`);
        return null;
      }
    };

    // Fixed-size worker pool over a shared index: order-preserving results,
    // never more than IMAGE_DOWNLOAD_CONCURRENCY requests (or decoded
    // buffers) in flight regardless of how many <img> tags the article had.
    const results = new Array(wanted.length).fill(null);
    let next = 0;
    const workers = Array.from(
      { length: Math.min(IMAGE_DOWNLOAD_CONCURRENCY, wanted.length) },
      async () => {
        while (true) {
          const index = next++;
          if (index >= wanted.length) return;
          results[index] = await fetchOne(wanted[index], index);
        }
      },
    );
    await Promise.all(workers);
    return results.filter(Boolean);
  }

  return { download };
}

export async function hydrateLocalImages(posts, downloader) {
  let updated = false;
  for (const post of posts) {
    const rawImages = Array.isArray(post.rawImages) ? post.rawImages : [];

    // Scraped state is the source of truth. If a post previously had an
    // image but the latest scrape returns no <img>, the persisted `images`
    // array MUST drop the stale entry — never preserve it from a prior
    // cycle. The earlier short-circuit (skip when rawImages is empty) left
    // stale attributions in place forever, which is how four text-only
    // themarketear posts ended up sharing the same EMB chart on 2026-05-21.
    if (rawImages.length === 0) {
      const existing = Array.isArray(post.images) ? post.images : [];
      if (existing.length > 0) {
        post.images = [];
        updated = true;
      }
      continue;
    }

    const localImages = await downloader.download(post.id, rawImages);
    if (JSON.stringify(localImages) !== JSON.stringify(post.images || [])) {
      post.images = localImages;
      updated = true;
    }
  }
  return updated;
}

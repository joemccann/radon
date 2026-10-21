import { isAllowedImageUrl } from "@/lib/imageHosts";
import { radonFetchResponse } from "@/lib/radonApi";

export type ExplainImageBlock = {
  type: "image";
  source: { type: "base64"; media_type: string; data: string };
};

const MAX_IMAGES = 2;
const MAX_BYTES = 2 * 1024 * 1024;
const RESEARCH_FILE = /^\/api\/newsfeed\/research\/files\/([a-f0-9]{64})\.png$/i;
const ALLOWED_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);

type FetchTarget =
  | { kind: "media"; url: string }
  | { kind: "research"; path: string };

function mediaTypeFrom(header: string | null, bytes: Uint8Array): string | null {
  const named = (header ?? "").split(";")[0]?.trim().toLowerCase();
  const fromHeader = named === "image/jpg" ? "image/jpeg" : named;
  if (fromHeader && ALLOWED_TYPES.has(fromHeader)) return fromHeader;
  if (bytes.length >= 8 && bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47) {
    return "image/png";
  }
  if (bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) {
    return "image/jpeg";
  }
  if (
    bytes.length >= 12
    && bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x46
    && bytes[8] === 0x57 && bytes[9] === 0x45 && bytes[10] === 0x42 && bytes[11] === 0x50
  ) {
    return "image/webp";
  }
  if (bytes.length >= 6 && bytes[0] === 0x47 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x38) {
    return "image/gif";
  }
  return fromHeader && ALLOWED_TYPES.has(fromHeader) ? fromHeader : null;
}

function admitUrl(raw: string): FetchTarget | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const research = trimmed.match(RESEARCH_FILE);
  if (research) {
    return { kind: "research", path: `/newsfeed/research/files/${research[1].toLowerCase()}.png` };
  }
  if (!isAllowedImageUrl(trimmed)) return null;
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== "https:" || parsed.hostname !== "media.radon.run") return null;
    return { kind: "media", url: parsed.toString() };
  } catch {
    return null;
  }
}

async function readBytes(response: Response): Promise<Uint8Array | null> {
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.length === 0 || bytes.length > MAX_BYTES) return null;
  const mediaType = mediaTypeFrom(response.headers.get("content-type"), bytes);
  if (!mediaType) return null;
  return bytes;
}

function toBlock(bytes: Uint8Array, mediaType: string): ExplainImageBlock {
  return {
    type: "image",
    source: { type: "base64", media_type: mediaType, data: Buffer.from(bytes).toString("base64") },
  };
}

async function fetchTarget(
  target: FetchTarget,
  opts?: { token?: string; signal?: AbortSignal },
): Promise<ExplainImageBlock | null> {
  const response = target.kind === "research"
    ? await radonFetchResponse(target.path, { token: opts?.token, signal: opts?.signal, cache: "no-store" })
    : await fetch(target.url, { signal: opts?.signal, cache: "no-store", redirect: "error" });
  if (!response.ok) return null;
  const bytes = await readBytes(response);
  if (!bytes) return null;
  const mediaType = mediaTypeFrom(response.headers.get("content-type"), bytes);
  if (!mediaType) return null;
  return toBlock(bytes, mediaType);
}

export async function loadExplainImages(
  urls: string[],
  opts?: { token?: string; signal?: AbortSignal },
): Promise<ExplainImageBlock[]> {
  const admitted: FetchTarget[] = [];
  for (const url of urls) {
    const target = admitUrl(url);
    if (target) admitted.push(target);
  }
  const images: ExplainImageBlock[] = [];
  let index = 0;
  while (images.length < MAX_IMAGES && index < admitted.length) {
    const batch = admitted.slice(index, index + (MAX_IMAGES - images.length));
    index += batch.length;
    const fetched = await Promise.all(batch.map(async (target) => {
      try {
        return await fetchTarget(target, opts);
      } catch {
        return null;
      }
    }));
    for (const image of fetched) {
      if (image && images.length < MAX_IMAGES) images.push(image);
    }
  }
  return images;
}

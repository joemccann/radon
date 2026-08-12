import { describe, expect, it } from "vitest";
import { access, mkdir, mkdtemp, readdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

// post.rawImages is third-party markup: extract.js reads src/data-src off every
// <img> in a scraped article verbatim. The scraper runs on the production VPS
// and its downloads are rsync'd to the public media host, so an attacker who
// lands an <img> in a feed article could (a) make the scraper GET the trusted
// loopback perimeter (127.0.0.1:8321) and republish the payload publicly and
// (b) read the operator's themarketear.com session cookie out of their own
// access log. Both are refused before a socket opens.

const PNG_BYTES = Buffer.from([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d,
]);

async function tempMediaDir() {
  const root = await mkdtemp(path.join(tmpdir(), "radon-newsfeed-ssrf-"));
  const mediaDir = path.join(root, "media");
  await mkdir(mediaDir, { recursive: true });
  return mediaDir;
}

function recordingClient(response = { status: 200, headers: { "content-type": "image/png" }, data: PNG_BYTES }) {
  const requests: Array<{ url: string; cookie: string | undefined }> = [];
  const client = {
    get: async (url: string, options: { headers?: Record<string, string> } = {}) => {
      requests.push({ url, cookie: options?.headers?.Cookie });
      return response;
    },
  };
  return { client, requests };
}

describe("media.js — image download origin allowlist (SSRF)", () => {
  it("issues no request for loopback or off-allowlist hosts", async () => {
    const mediaDir = await tempMediaDir();
    const { createImageDownloader } = await import("../../scripts/newsfeed/media.js");
    const { client, requests } = recordingClient();

    const downloader = createImageDownloader({ mediaDir, client });
    const result = await downloader.download("p1", [
      "http://127.0.0.1:8321/health",
      "https://attacker.example/x.png",
      "https://themarketear.com/images/real.png",
    ]);

    expect(requests.map((r) => r.url)).toEqual([
      "https://themarketear.com/images/real.png",
    ]);
    expect(result).toEqual(["https://media.radon.run/p1-03.png"]);
  });

  it("refuses a plain-http URL even on an allowlisted host", async () => {
    const mediaDir = await tempMediaDir();
    const { createImageDownloader } = await import("../../scripts/newsfeed/media.js");
    const { client, requests } = recordingClient();

    const downloader = createImageDownloader({ mediaDir, client });
    const result = await downloader.download("p2", ["http://themarketear.com/images/x.png"]);

    expect(requests).toEqual([]);
    expect(result).toEqual([]);
  });

  it("refuses a loopback URL that a relative src absolutises into", async () => {
    const mediaDir = await tempMediaDir();
    const { createImageDownloader } = await import("../../scripts/newsfeed/media.js");
    const { client, requests } = recordingClient();

    const downloader = createImageDownloader({ mediaDir, client });
    await downloader.download("p3", ["http://[::1]:8321/health", "https://169.254.169.254/latest/meta-data"]);

    expect(requests).toEqual([]);
  });
});

describe("media.js — session cookie scoping", () => {
  it("attaches the Cookie header only on themarketear.com hosts", async () => {
    const mediaDir = await tempMediaDir();
    const { createImageDownloader } = await import("../../scripts/newsfeed/media.js");
    const { client, requests } = recordingClient();

    const downloader = createImageDownloader({
      mediaDir,
      client,
      getCookieHeader: async () => "P=session-token; U=user-token",
    });

    await downloader.download("p4", [
      "https://themarketear.com/images/a.png",
      "https://tme.cdn.digitaloceanspaces.com/images/b.png",
    ]);

    expect(requests).toHaveLength(2);
    expect(requests[0].cookie).toBe("P=session-token; U=user-token");
    expect(requests[1].cookie).toBeUndefined();
  });
});

describe("media.js — response must actually be an image", () => {
  it("does not write a non-image content-type into the public media dir", async () => {
    const mediaDir = await tempMediaDir();
    const { createImageDownloader } = await import("../../scripts/newsfeed/media.js");
    const { client } = recordingClient({
      status: 200,
      headers: { "content-type": "application/json" },
      data: Buffer.from('{"ib_gateway":{"port_listening":true}}'),
    });

    const downloader = createImageDownloader({ mediaDir, client });
    const result = await downloader.download("p5", ["https://themarketear.com/images/x.png"]);

    expect(result).toEqual([]);
    expect(await readdir(mediaDir)).toEqual([]);
  });

  it("does not write a body whose bytes are not an image", async () => {
    const mediaDir = await tempMediaDir();
    const { createImageDownloader } = await import("../../scripts/newsfeed/media.js");
    const { client } = recordingClient({
      status: 200,
      headers: { "content-type": "image/png" },
      data: Buffer.from("<svg onload=alert(1)></svg>"),
    });

    const downloader = createImageDownloader({ mediaDir, client });
    const result = await downloader.download("p6", ["https://themarketear.com/images/x.png"]);

    expect(result).toEqual([]);
    await expect(access(path.join(mediaDir, "p6-01.png"))).rejects.toThrow();
  });
});

describe("media.js — DNS rebinding guard", () => {
  it("blocks a hostname that resolves to a private or loopback address", async () => {
    const { createGuardedLookup } = await import("../../scripts/newsfeed/media.js");

    const lookup = createGuardedLookup((_host, _opts, cb) => cb(null, "127.0.0.1", 4));
    const err = await new Promise<Error | null>((resolve) => {
      lookup("rebind.attacker.example", {}, (e: Error | null) => resolve(e));
    });

    expect(err).toBeInstanceOf(Error);
    expect(String(err?.message)).toContain("127.0.0.1");
  });

  it("blocks when any address in an all:true resolution is private", async () => {
    const { createGuardedLookup } = await import("../../scripts/newsfeed/media.js");

    const lookup = createGuardedLookup((_host, _opts, cb) =>
      cb(null, [
        { address: "93.184.216.34", family: 4 },
        { address: "169.254.169.254", family: 4 },
      ]),
    );
    const err = await new Promise<Error | null>((resolve) => {
      lookup("mixed.example", { all: true }, (e: Error | null) => resolve(e));
    });

    expect(err).toBeInstanceOf(Error);
    expect(String(err?.message)).toContain("169.254.169.254");
  });

  it("passes a public address through untouched", async () => {
    const { createGuardedLookup } = await import("../../scripts/newsfeed/media.js");

    const lookup = createGuardedLookup((_host, _opts, cb) => cb(null, "93.184.216.34", 4));
    const resolved = await new Promise<{ address: string; family: number }>((resolve, reject) => {
      lookup("themarketear.com", {}, (e: Error | null, address: string, family: number) =>
        e ? reject(e) : resolve({ address, family }),
      );
    });

    expect(resolved).toEqual({ address: "93.184.216.34", family: 4 });
  });
});

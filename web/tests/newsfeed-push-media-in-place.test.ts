// The newsfeed's image delivery hop assumes two distinct trees: the scraper
// downloads into <repo>/web/public/media on the laptop and rsyncs to the
// Hetzner media volume that Caddy serves.
//
// Under the P3 container runtime there is only ONE tree. The served volume is
// bind-mounted into the container at /var/lib/radon/media, so the scraper
// writes its images directly where Caddy reads them and there is no hop left
// to make. rsync is not even installed in the node image, so spawning it
// fails, pushMedia returns ok:false, and mediaDirty never clears — the
// 2026-08-29 regression where every scraped image 404'd on media.radon.run.
//
// Contract: when local and remote resolve to the same directory, pushMedia
// must not spawn rsync at all and must still report success.

import fs from "fs-extra";
import os from "os";
import path from "path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

let workdir: string;

beforeEach(async () => {
  workdir = await fs.mkdtemp(path.join(os.tmpdir(), "radon-media-"));
});

afterEach(async () => {
  await fs.remove(workdir);
});

describe("pushMedia — the container's single media tree", () => {
  it("skips rsync when local and remote are the same directory", async () => {
    const { pushMedia } = await import("../../scripts/newsfeed/push_media.js");
    const media = path.join(workdir, "media");
    await fs.ensureDir(media);
    await fs.writeFile(path.join(media, "a-01.png"), "png", { mode: 0o600 });

    const result = await pushMedia({ local: `${media}/`, remote: media });

    expect(result.ok).toBe(true);
    expect(result.transferred).toBe(0);
    expect(result.repairMode).toBe("in-place");
    expect(result.reason).toBeUndefined();
    // The file the scraper just wrote is still there, now Caddy-readable.
    expect(await fs.pathExists(path.join(media, "a-01.png"))).toBe(true);
    expect((await fs.stat(path.join(media, "a-01.png"))).mode & 0o777).toBe(0o644);
  });

  it("treats a trailing-slash and symlinked remote as the same tree", async () => {
    const { pushMedia } = await import("../../scripts/newsfeed/push_media.js");
    const media = path.join(workdir, "media");
    await fs.ensureDir(media);
    const link = path.join(workdir, "media-link");
    await fs.symlink(media, link);

    const result = await pushMedia({ local: `${media}/`, remote: `${link}/` });

    expect(result.ok).toBe(true);
    // The hourly permission sweep is throttled across calls, so only the
    // prefix is stable here; the sweep itself is pinned by the test above.
    expect(result.repairMode).toMatch(/^in-place/);
  });

  it("still rsyncs when the remote is a genuinely different tree", async () => {
    const { pushMedia } = await import("../../scripts/newsfeed/push_media.js");
    const local = path.join(workdir, "local");
    const remote = path.join(workdir, "remote");
    await fs.ensureDir(local);
    await fs.ensureDir(remote);
    await fs.writeFile(path.join(local, "b-01.png"), "png");

    const result = await pushMedia({ local: `${local}/`, remote: `${remote}/` });

    // Whether rsync itself succeeds depends on the host binary (macOS ships
    // 2.6.9, which rejects --chmod), so pin only the branch: two distinct
    // trees must never take the in-place shortcut.
    expect(String(result.repairMode)).not.toMatch(/^in-place/);
  });
});

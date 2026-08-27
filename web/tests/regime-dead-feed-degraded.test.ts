/**
 * R-200: a dead crash-risk feed must not render as the calmest possible reading.
 *
 * Both source readers swallow every failure into null, and GET then substituted
 * the literal EMPTY_CRI — score 0, level "LOW", four zeroed components,
 * crash_trigger.triggered false — overwrote market_open with the live session
 * so the payload looked current, and returned HTTP 200 with cacheState HIT.
 * "CRI is genuinely 0" and "CRI is unknown" were byte-identical to the client
 * at the exact moment the regime feed was dead.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const mockReadFile = vi.fn();
const mockReaddir = vi.fn();
const mockWriteFile = vi.fn();
const mockStat = vi.fn();
const mockMkdir = vi.fn();
const mockSpawn = vi.fn();
const mockGetDb = vi.fn();

vi.mock("fs/promises", () => ({
  readFile: mockReadFile,
  readdir: mockReaddir,
  writeFile: mockWriteFile,
  stat: mockStat,
  mkdir: mockMkdir,
}));

vi.mock("child_process", () => ({ spawn: mockSpawn }));

vi.mock("@/lib/db", () => ({
  resetDb: () => {},
  getDb: () => mockGetDb(),
}));

describe("/api/regime with every source dead", () => {
  beforeEach(() => {
    vi.resetModules();
    for (const m of [mockReadFile, mockReaddir, mockWriteFile, mockStat, mockMkdir, mockSpawn, mockGetDb]) {
      m.mockReset();
    }
    mockWriteFile.mockResolvedValue(undefined);
    mockMkdir.mockResolvedValue(undefined);
    // Turso down.
    mockGetDb.mockImplementation(() => {
      throw new Error("turso unreachable");
    });
    // No scheduled files and no legacy cache on disk.
    mockReaddir.mockResolvedValue([]);
    mockStat.mockRejectedValue(new Error("ENOENT"));
    mockReadFile.mockRejectedValue(new Error("ENOENT"));
  });

  async function deadFeedBody() {
    const { GET } = await import("../app/api/regime/route");
    const response = await GET();
    expect(response.status).toBe(200);
    return response.json();
  }

  it("does not report a crash-risk score of 0 at level LOW", async () => {
    const body = await deadFeedBody();
    expect(body.cri?.score).not.toBe(0);
    expect(body.cri?.level).not.toBe("LOW");
  });

  it("does not assert that the crash trigger is untriggered", async () => {
    const body = await deadFeedBody();
    expect(body.crash_trigger?.triggered).not.toBe(false);
  });

  it("does not publish four zeroed component scores", async () => {
    const body = await deadFeedBody();
    expect(body.cri?.components).toBeFalsy();
  });

  it("marks the payload as missing so the client can tell the two apart", async () => {
    const body = await deadFeedBody();
    expect(body.missing).toBe(true);
  });

  it("does not fabricate a CTA exposure of 200pct", async () => {
    const body = await deadFeedBody();
    expect(body.cta?.exposure_pct).not.toBe(200);
  });
});

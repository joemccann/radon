/**
 * R-228: the same fabrication shape as R-200, on the vol-credit gap.
 *
 * `readCachedVcg` returns null when both the Turso row and data/vcg.json are
 * unreadable, and `normalizeVcgPayload(cached ?? {})` then fabricated
 * EMPTY_VCG: regime "DIVERGENCE", interpretation "NORMAL", vix/vvix/ro/edr 0,
 * sign_ok true — with market_open forced to the live value and HTTP 200.
 * VcgPanel's only guard is `if (!data) return null`, which never fires, so it
 * paints the "DIVERGENCE" pill and a "NORMAL" interpretation and, because
 * ro === 1 and edr === 1 are false, suppresses the RISK-OFF and EDR pills. A
 * trader reads an affirmative "no risk-off signal" out of a dead feed.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const mockReadFile = vi.fn();
const mockGetDb = vi.fn();

vi.mock("fs/promises", () => ({ readFile: mockReadFile }));
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: () => mockGetDb() }));
vi.mock("@/lib/radonApi", () => ({ radonFetch: vi.fn(async () => ({})) }));

describe("/api/vcg with every source dead", () => {
  beforeEach(() => {
    vi.resetModules();
    mockReadFile.mockReset();
    mockGetDb.mockReset();
    mockGetDb.mockImplementation(() => {
      throw new Error("turso unreachable");
    });
    mockReadFile.mockRejectedValue(new Error("ENOENT"));
  });

  async function deadFeedBody() {
    const { GET } = await import("../app/api/vcg/route");
    const response = await GET();
    expect(response.status).toBe(200);
    return response.json();
  }

  it("does not assert a DIVERGENCE regime", async () => {
    const body = await deadFeedBody();
    expect(body.signal?.regime).not.toBe("DIVERGENCE");
  });

  it("does not assert a NORMAL interpretation", async () => {
    const body = await deadFeedBody();
    expect(body.signal?.interpretation).not.toBe("NORMAL");
  });

  it("does not publish zeroed vix, vvix, ro and edr as readings", async () => {
    const body = await deadFeedBody();
    expect(body.signal).toBeFalsy();
  });

  it("marks the payload as missing", async () => {
    const body = await deadFeedBody();
    expect(body.missing).toBe(true);
  });
});

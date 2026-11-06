// Newsfeed delivery health + auth renewal backoff.
//
// Audit implicit-state-machines-20261103 §3.4 "Newsfeed delivery + auth":
//   - pass 1 heartbeated `ok` right after the DB upsert while the media push
//     was still pending or failing (mediaDirty), although the nochange branch
//     suppressed `ok` for the very same state;
//   - a failed auth left lastAuthAt untouched, so every 2-minute cycle retried
//     the Firebase renewal with no backoff.

import { describe, expect, it } from "vitest";

function makeDeps(overrides: Record<string, unknown> = {}) {
  return {
    cycleStartIso: "2026-11-04T12:00:00Z",
    loadExistingPosts: async () => [],
    scrapePosts: async () => ({
      items: [{ id: "fresh-1", title: "Fresh", content: "body", timestamp: "2026-11-04T12:00:00Z", images: [] }],
    }),
    mergePosts: (_existing: unknown[], items: unknown[]) => ({
      merged: items.map((item) => ({ ...(item as object) })),
      changed: true,
    }),
    hydrateLocalImages: async () => true,
    persistPosts: async () => ({ archived: false }),
    pushMedia: async () => ({ ok: true, transferred: 1 }),
    upsertPosts: async () => {},
    hydrateTagsDual: async () => false,
    recordServiceHealth: async () => {},
    buildTextTagger: () => null,
    buildVisionTagger: () => null,
    onNewTags: async () => [],
    requestReauth: () => {},
    paths: {
      dataDir: "/tmp/x",
      archiveDir: "/tmp/x/archive",
      mediaDir: "/tmp/x/media",
      postsFile: "/tmp/x/posts.json",
      projectRoot: "/tmp/x",
      publicRoot: "/tmp/x/public",
    },
    ...overrides,
  };
}

describe("runScrapeCycle — ok means every delivery landed", () => {
  it("does not heartbeat ok while the media push failed (mediaDirty)", async () => {
    const { runScrapeCycle } = await import("../../scripts/newsfeed/cycle.js");
    const states: string[] = [];
    await runScrapeCycle(
      makeDeps({
        pushMedia: async () => ({ ok: false, reason: "tailscale down" }),
        recordServiceHealth: async (_svc: string, state: string) => {
          states.push(state);
        },
      }),
    );
    expect(states).not.toContain("ok");
  });

  it("heartbeats ok once both the DB write and the media push land", async () => {
    const { runScrapeCycle } = await import("../../scripts/newsfeed/cycle.js");
    const states: string[] = [];
    await runScrapeCycle(
      makeDeps({
        recordServiceHealth: async (_svc: string, state: string) => {
          states.push(state);
        },
      }),
    );
    expect(states).toEqual(["ok"]);
  });

  it("does not heartbeat ok while a previous cycle's media is still dirty", async () => {
    const { runScrapeCycle } = await import("../../scripts/newsfeed/cycle.js");
    const states: string[] = [];
    await runScrapeCycle(
      makeDeps({
        hydrateLocalImages: async () => false,
        loadDeliveryState: async () => ({ dbDirty: false, mediaDirty: true }),
        saveDeliveryState: async () => {},
        pushMedia: async () => ({ ok: false, reason: "rsync timeout" }),
        recordServiceHealth: async (_svc: string, state: string) => {
          states.push(state);
        },
      }),
    );
    expect(states).not.toContain("ok");
  });
});

describe("createAuthGate — bounded renewal backoff", () => {
  const MIN = 60_000;

  it("attempts on first use and not again inside the re-auth window", async () => {
    const { createAuthGate } = await import("../../scripts/newsfeed/authGate.js");
    let now = 0;
    const gate = createAuthGate({ now: () => now, reauthIntervalMs: 360 * MIN });
    expect(gate.decide().attempt).toBe(true);
    gate.succeeded();
    now += 2 * MIN;
    expect(gate.decide().attempt).toBe(false);
    now += 360 * MIN;
    expect(gate.decide().attempt).toBe(true);
  });

  it("backs off after a failure instead of retrying every cycle", async () => {
    const { createAuthGate } = await import("../../scripts/newsfeed/authGate.js");
    let now = 0;
    const gate = createAuthGate({ now: () => now, reauthIntervalMs: 360 * MIN, baseBackoffMs: 5 * MIN, maxBackoffMs: 60 * MIN });
    gate.decide();
    gate.failed();
    now += 2 * MIN;
    const blocked = gate.decide();
    expect(blocked.attempt).toBe(false);
    expect(blocked.backoffUntil).toBe(5 * MIN);
    now = 5 * MIN;
    expect(gate.decide().attempt).toBe(true);
    gate.failed();
    expect(gate.state()).toEqual({ kind: "backoff", failures: 2, until: 15 * MIN });
  });

  it("caps the backoff and resets on success", async () => {
    const { createAuthGate } = await import("../../scripts/newsfeed/authGate.js");
    let now = 0;
    const gate = createAuthGate({ now: () => now, reauthIntervalMs: 360 * MIN, baseBackoffMs: 5 * MIN, maxBackoffMs: 60 * MIN });
    for (let i = 0; i < 10; i += 1) {
      gate.failed();
    }
    expect(gate.state()).toMatchObject({ kind: "backoff", until: 60 * MIN });
    now = 60 * MIN;
    expect(gate.decide().attempt).toBe(true);
    gate.succeeded();
    expect(gate.state()).toEqual({ kind: "fresh", at: 60 * MIN });
  });

  it("a forced re-auth (paywall) cannot bypass an active backoff", async () => {
    const { createAuthGate } = await import("../../scripts/newsfeed/authGate.js");
    let now = 0;
    const gate = createAuthGate({ now: () => now, reauthIntervalMs: 360 * MIN, baseBackoffMs: 5 * MIN });
    gate.failed();
    gate.requestReauth();
    now += MIN;
    expect(gate.decide().attempt).toBe(false);
  });

  it("a forced re-auth inside a fresh window attempts on the next cycle", async () => {
    const { createAuthGate } = await import("../../scripts/newsfeed/authGate.js");
    let now = 0;
    const gate = createAuthGate({ now: () => now, reauthIntervalMs: 360 * MIN });
    gate.succeeded();
    now += MIN;
    gate.requestReauth();
    expect(gate.decide().attempt).toBe(true);
  });
});

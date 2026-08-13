// Freshness contract for the newsfeed scrape cycle.
//
// Bug being prevented: tag hydration was running BEFORE persistence, so a
// fresh post on TheMarketEar would sit in memory while every other untagged
// post in the buffer got re-classified through Cerebras + Anthropic
// (rate-limited at ~24 req/min). On a feed with several novel posts, the
// per-cycle delay between scrape and DB-write ballooned past the 120s poll
// interval — a fresh post that should have appeared in <2 min on the
// dashboard instead waited for an entire tagging pass.
//
// The fix: persist freshly-merged posts to disk + DB BEFORE invoking the
// tagger. Tags are nice-to-have; freshness is the user-facing contract.
// Tags trickle in on a second persist after hydrateTagsDual completes.
//
// This test pins the ordering: upsertPosts MUST be invoked for fresh posts
// before hydrateTagsDual is awaited.

import { describe, expect, it } from "vitest";

describe("runScrapeCycle ordering — persistence before tagging", () => {
  it("calls upsertPosts BEFORE awaiting hydrateTagsDual", async () => {
    const { runScrapeCycle } = await import("../../scripts/newsfeed/cycle.js");

    const order: string[] = [];

    // The scraper produces one new post on this cycle.
    const scraped = [
      {
        id: "fresh-1",
        title: "Fresh post",
        content: "body",
        timestamp: "2026-05-09T12:00:00Z",
        images: [],
      },
    ];

    let tagResolveOrder = -1;

    const result = await runScrapeCycle({
      cycleStartIso: "2026-05-09T12:00:00Z",
      loadExistingPosts: async () => [],
      scrapePosts: async () => ({ items: scraped }),
      mergePosts: (existing: unknown[], items: unknown[]) => ({
        merged: items.map((item) => ({ ...(item as object) })),
        changed: true,
      }),
      hydrateLocalImages: async () => false,
      persistPosts: async () => {
        order.push("persistPosts");
        return { archived: false };
      },
      pushMedia: async () => ({ ok: true, transferred: 0 }),
      upsertPosts: async () => {
        order.push("upsertPosts");
      },
      hydrateTagsDual: async () => {
        // Simulate a slow tagging pass — the API rate-limits us to ~24 req/min,
        // so several novel posts can take many seconds.
        await new Promise((resolve) => setTimeout(resolve, 25));
        tagResolveOrder = order.length;
        order.push("hydrateTagsDual");
        return false;
      },
      recordServiceHealth: async () => {
        order.push("recordServiceHealth");
      },
      buildTextTagger: () => ({ tagPost: async () => null }),
      buildVisionTagger: () => ({ tagPost: async () => null }),
      onNewTags: async () => {},
      paths: {
        dataDir: "/tmp/x",
        archiveDir: "/tmp/x/archive",
        mediaDir: "/tmp/x/media",
        postsFile: "/tmp/x/posts.json",
        projectRoot: "/tmp/x",
        publicRoot: "/tmp/x/public",
      },
    });

    expect(result.changed).toBe(true);

    // The persistPosts AND upsertPosts calls for fresh posts must happen
    // BEFORE hydrateTagsDual resolves. Otherwise the dashboard waits for
    // the slow tagger before seeing the fresh post.
    const firstPersist = order.indexOf("persistPosts");
    const firstUpsert = order.indexOf("upsertPosts");
    const tagIndex = order.indexOf("hydrateTagsDual");

    expect(firstPersist).toBeGreaterThanOrEqual(0);
    expect(firstUpsert).toBeGreaterThanOrEqual(0);
    expect(tagIndex).toBeGreaterThanOrEqual(0);
    expect(firstPersist).toBeLessThan(tagIndex);
    expect(firstUpsert).toBeLessThan(tagIndex);

    // Sanity: the tagger really did resolve last among the three primary
    // signals (otherwise the timing was meaningless).
    expect(tagResolveOrder).toBeGreaterThan(firstUpsert);
  });

  it("re-persists after tagging when tags update", async () => {
    const { runScrapeCycle } = await import("../../scripts/newsfeed/cycle.js");

    const persistCalls: string[] = [];
    const upsertCalls: string[] = [];

    await runScrapeCycle({
      cycleStartIso: "2026-05-09T12:00:00Z",
      loadExistingPosts: async () => [],
      scrapePosts: async () => ({
        items: [
          {
            id: "p1",
            title: "Fresh",
            content: "",
            timestamp: "2026-05-09T12:00:00Z",
            images: [],
          },
        ],
      }),
      mergePosts: (existing: unknown[], items: unknown[]) => ({
        merged: items.map((item) => ({ ...(item as object) })),
        changed: true,
      }),
      hydrateLocalImages: async () => false,
      persistPosts: async (posts: unknown[]) => {
        persistCalls.push(`persist(N=${posts.length})`);
        return { archived: false };
      },
      pushMedia: async () => ({ ok: true, transferred: 0 }),
      upsertPosts: async (posts: unknown[]) => {
        upsertCalls.push(`upsert(N=${posts.length})`);
      },
      hydrateTagsDual: async (posts: unknown[]) => {
        // Pretend the tagger added tags to the first post.
        const arr = posts as { tags?: string[] }[];
        if (arr.length > 0) arr[0].tags = ["BTC", "VOL", "MACRO"];
        return true;
      },
      recordServiceHealth: async () => {},
      buildTextTagger: () => ({ tagPost: async () => null }),
      buildVisionTagger: () => ({ tagPost: async () => null }),
      onNewTags: async () => {},
      paths: {
        dataDir: "/tmp/x",
        archiveDir: "/tmp/x/archive",
        mediaDir: "/tmp/x/media",
        postsFile: "/tmp/x/posts.json",
        projectRoot: "/tmp/x",
        publicRoot: "/tmp/x/public",
      },
    });

    // Persist + upsert MUST happen twice when tags get added: once for the
    // fresh-post pre-tag pass, once after the tagger adds the tags.
    expect(persistCalls.length).toBe(2);
    expect(upsertCalls.length).toBe(2);
  });

  it("only persists once when tagging produces no changes", async () => {
    const { runScrapeCycle } = await import("../../scripts/newsfeed/cycle.js");

    const persistCalls: string[] = [];
    const upsertCalls: string[] = [];

    await runScrapeCycle({
      cycleStartIso: "2026-05-09T12:00:00Z",
      loadExistingPosts: async () => [],
      scrapePosts: async () => ({
        items: [
          {
            id: "p1",
            title: "Fresh",
            content: "",
            timestamp: "2026-05-09T12:00:00Z",
            images: [],
          },
        ],
      }),
      mergePosts: (existing: unknown[], items: unknown[]) => ({
        merged: items.map((item) => ({ ...(item as object) })),
        changed: true,
      }),
      hydrateLocalImages: async () => false,
      persistPosts: async (posts: unknown[]) => {
        persistCalls.push(`persist(N=${posts.length})`);
        return { archived: false };
      },
      pushMedia: async () => ({ ok: true, transferred: 0 }),
      upsertPosts: async (posts: unknown[]) => {
        upsertCalls.push(`upsert(N=${posts.length})`);
      },
      hydrateTagsDual: async () => false, // tagger ran but nothing changed
      recordServiceHealth: async () => {},
      buildTextTagger: () => ({ tagPost: async () => null }),
      buildVisionTagger: () => ({ tagPost: async () => null }),
      onNewTags: async () => {},
      paths: {
        dataDir: "/tmp/x",
        archiveDir: "/tmp/x/archive",
        mediaDir: "/tmp/x/media",
        postsFile: "/tmp/x/posts.json",
        projectRoot: "/tmp/x",
        publicRoot: "/tmp/x/public",
      },
    });

    expect(persistCalls.length).toBe(1);
    expect(upsertCalls.length).toBe(1);
  });

  it("calls upsertPosts BEFORE pushMedia resolves on slow rsync", async () => {
    // pushMedia (rsync over Tailscale) is independent of the DB upsert. When
    // Tailscale is degraded, the rsync delays the DB write up to 30s, so
    // routes preferring DB see stale data even though disk is fresh.
    //
    // Fix: upsertPosts must NOT wait on pushMedia. Either reorder so the
    // upsert runs first, or Promise.all them — either way the upsert
    // completes before a slow pushMedia resolves.
    const { runScrapeCycle } = await import("../../scripts/newsfeed/cycle.js");

    const events: { name: string; ts: number }[] = [];

    await runScrapeCycle({
      cycleStartIso: "2026-05-09T12:00:00Z",
      loadExistingPosts: async () => [],
      scrapePosts: async () => ({
        items: [
          {
            id: "p-fresh",
            title: "Fresh",
            content: "",
            timestamp: "2026-05-09T12:00:00Z",
            images: ["https://media.example/img.png"],
          },
        ],
      }),
      mergePosts: (existing: unknown[], items: unknown[]) => ({
        merged: items.map((item) => ({ ...(item as object) })),
        changed: true,
      }),
      // Force the imagesUpdated branch so pushMedia is invoked.
      hydrateLocalImages: async () => true,
      persistPosts: async () => {
        events.push({ name: "persistPosts:done", ts: Date.now() });
        return { archived: false };
      },
      pushMedia: async () => {
        events.push({ name: "pushMedia:start", ts: Date.now() });
        // Slow rsync — simulate a degraded Tailscale link.
        await new Promise((resolve) => setTimeout(resolve, 100));
        events.push({ name: "pushMedia:done", ts: Date.now() });
        return { ok: true, transferred: 1 };
      },
      upsertPosts: async () => {
        events.push({ name: "upsertPosts:done", ts: Date.now() });
      },
      hydrateTagsDual: async () => false,
      recordServiceHealth: async () => {},
      buildTextTagger: () => ({ tagPost: async () => null }),
      buildVisionTagger: () => ({ tagPost: async () => null }),
      onNewTags: async () => {},
      paths: {
        dataDir: "/tmp/x",
        archiveDir: "/tmp/x/archive",
        mediaDir: "/tmp/x/media",
        postsFile: "/tmp/x/posts.json",
        projectRoot: "/tmp/x",
        publicRoot: "/tmp/x/public",
      },
    });

    const upsertDone = events.findIndex((e) => e.name === "upsertPosts:done");
    const pushDone = events.findIndex((e) => e.name === "pushMedia:done");

    expect(upsertDone).toBeGreaterThanOrEqual(0);
    expect(pushDone).toBeGreaterThanOrEqual(0);

    // The DB write must complete before the slow rsync resolves. Either
    // ordering (upsert-first sequential) or parallel scheduling satisfies
    // this — both keep DB freshness independent of Tailscale latency.
    expect(upsertDone).toBeLessThan(pushDone);
  });

  it("returns early on empty scrape without persisting or tagging", async () => {
    const { runScrapeCycle } = await import("../../scripts/newsfeed/cycle.js");

    const order: string[] = [];

    const result = await runScrapeCycle({
      cycleStartIso: "2026-05-09T12:00:00Z",
      loadExistingPosts: async () => [],
      scrapePosts: async () => ({ items: [] }),
      mergePosts: () => {
        order.push("merge");
        return { merged: [], changed: false };
      },
      hydrateLocalImages: async () => {
        order.push("images");
        return false;
      },
      persistPosts: async () => {
        order.push("persist");
        return { archived: false };
      },
      pushMedia: async () => ({ ok: true, transferred: 0 }),
      upsertPosts: async () => {
        order.push("upsert");
      },
      hydrateTagsDual: async () => {
        order.push("tag");
        return false;
      },
      recordServiceHealth: async () => {
        order.push("health");
      },
      buildTextTagger: () => ({ tagPost: async () => null }),
      buildVisionTagger: () => ({ tagPost: async () => null }),
      onNewTags: async () => {},
      paths: {
        dataDir: "/tmp/x",
        archiveDir: "/tmp/x/archive",
        mediaDir: "/tmp/x/media",
        postsFile: "/tmp/x/posts.json",
        projectRoot: "/tmp/x",
        publicRoot: "/tmp/x/public",
      },
    });

    expect(result).toMatchObject({ changed: false, count: 0 });
    expect(order).not.toContain("persist");
    expect(order).not.toContain("upsert");
    expect(order).not.toContain("tag");
  });

  it("heartbeats service_health=ok on truly-empty cycles to clear stale error rows", async () => {
    // Bug we're pinning: 819fe14 added the heartbeat for the
    // nochange-after-changes branch but missed the earlier short-circuit
    // when the scrape returns zero items. Without a heartbeat here, a
    // prior `error` row from yesterday's WalConflict latches the banner
    // red through a quiet weekend.
    const { runScrapeCycle } = await import("../../scripts/newsfeed/cycle.js");

    const healthCalls: Array<{ service: string; state: string }> = [];

    const result = await runScrapeCycle({
      cycleStartIso: "2026-05-09T12:00:00Z",
      loadExistingPosts: async () => [],
      scrapePosts: async () => ({ items: [] }),
      mergePosts: () => ({ merged: [], changed: false }),
      hydrateLocalImages: async () => false,
      persistPosts: async () => ({ archived: false }),
      pushMedia: async () => ({ ok: true, transferred: 0 }),
      upsertPosts: async () => {},
      hydrateTagsDual: async () => false,
      recordServiceHealth: async (service: string, state: string) => {
        healthCalls.push({ service, state });
      },
      buildTextTagger: () => ({ tagPost: async () => null }),
      buildVisionTagger: () => ({ tagPost: async () => null }),
      onNewTags: async () => {},
      paths: {
        dataDir: "/tmp/x",
        archiveDir: "/tmp/x/archive",
        mediaDir: "/tmp/x/media",
        postsFile: "/tmp/x/posts.json",
        projectRoot: "/tmp/x",
        publicRoot: "/tmp/x/public",
      },
    });

    expect(result).toMatchObject({ changed: false, count: 0 });
    expect(healthCalls.length).toBe(1);
    expect(healthCalls[0]).toEqual({ service: "newsfeed-scraper", state: "ok" });
  });
});

describe("runScrapeCycle durable delivery retries", () => {
  const paths = {
    dataDir: "/tmp/newsfeed-dirty-test",
    archiveDir: "/tmp/newsfeed-dirty-test/archive",
    mediaDir: "/tmp/newsfeed-dirty-test/media",
    postsFile: "/tmp/newsfeed-dirty-test/posts.json",
    projectRoot: "/tmp/newsfeed-dirty-test",
    publicRoot: "/tmp/newsfeed-dirty-test/public",
  };
  const item = { id: "p1", title: "same", content: "body", timestamp: "2026-08-13T12:00:00Z", images: [] };

  function deps(state: { dbDirty: boolean; mediaDirty: boolean }) {
    return {
      cycleStartIso: "2026-08-13T12:00:00Z",
      loadExistingPosts: async () => [item],
      scrapePosts: async () => ({ items: [item] }),
      mergePosts: (_existing: unknown[], items: unknown[]) => ({ merged: items, changed: false }),
      hydrateLocalImages: async () => false,
      persistPosts: async () => ({ archived: false }),
      pushMedia: async () => ({ ok: true, transferred: 0 }),
      upsertPosts: async () => {},
      hydrateTagsDual: async () => false,
      recordServiceHealth: async () => {},
      buildTextTagger: () => null,
      buildVisionTagger: () => null,
      onNewTags: async () => [],
      loadDeliveryState: async () => ({ ...state }),
      saveDeliveryState: async (next: typeof state) => Object.assign(state, next),
      paths,
    };
  }

  it("retries a dirty database write even when posts are unchanged", async () => {
    const { runScrapeCycle } = await import("../../scripts/newsfeed/cycle.js");
    const state = { dbDirty: true, mediaDirty: false };
    let writes = 0;
    await runScrapeCycle({
      ...deps(state),
      upsertPosts: async () => { writes += 1; },
    });
    expect(writes).toBe(1);
    expect(state.dbDirty).toBe(false);
  });

  it("retries a dirty media upload without a new image change", async () => {
    const { runScrapeCycle } = await import("../../scripts/newsfeed/cycle.js");
    const state = { dbDirty: false, mediaDirty: true };
    let pushes = 0;
    await runScrapeCycle({
      ...deps(state),
      pushMedia: async () => { pushes += 1; return { ok: true, transferred: 1 }; },
    });
    expect(pushes).toBe(1);
    expect(state.mediaDirty).toBe(false);
  });

  it("does not report healthy while a media delivery retry remains dirty", async () => {
    const { runScrapeCycle } = await import("../../scripts/newsfeed/cycle.js");
    const state = { dbDirty: false, mediaDirty: true };
    const healthStates: string[] = [];
    await runScrapeCycle({
      ...deps(state),
      pushMedia: async () => ({ ok: false, reason: "unavailable" }),
      recordServiceHealth: async (_service: string, healthState: string) => {
        healthStates.push(healthState);
      },
    });
    expect(state.mediaDirty).toBe(true);
    expect(healthStates).not.toContain("ok");
  });
});

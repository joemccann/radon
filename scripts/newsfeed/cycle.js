// Newsfeed scrape cycle orchestrator (DI-friendly).
//
// Freshness contract: persist freshly-merged posts to disk + DB BEFORE
// invoking the rate-limited tagger. Otherwise a cycle that scrapes one
// fresh post plus several novel-but-untagged posts in the buffer can
// stall the write past the 120s poll interval, because the tagger runs
// at ~24 req/min and serialises through every untagged post in the feed
// before persistence happens.
//
// Tags are nice-to-have. Freshness is the user-facing contract. We
// re-persist after tagging if the tagger added anything.

import { PAYWALL_FULL_MESSAGE, PAYWALL_STUB_MARKER } from "./auth.js";

// Cycle-level paywall guard. The 6h re-auth gate in index.js means a session
// flipped to free-tier mid-window has no runtime guard — themarketear.com
// keeps serving the /newsfeed URL but each article body is replaced with
// the canonical paywall message. 2026-05-16: a server-side downgrade at
// 17:45 UTC went undetected for ~21h, polluting posts.json with stub
// content. Fix: scan each extracted body for the exact paywall string
// and force re-auth on the next cycle while filtering the stubs out of
// the current write.
export function isPaywalledItem(item) {
  if (!item || typeof item !== "object") return false;
  const body = typeof item.content === "string" ? item.content : "";
  if (!body) return false;
  // Canonical full-string check first — that is the user-quoted message.
  if (body.includes(PAYWALL_FULL_MESSAGE)) return true;
  // Looser secondary check for prose variants that still embed the
  // recognisable substring (handles whitespace / punctuation drift).
  return body.includes(PAYWALL_STUB_MARKER);
}

export async function runScrapeCycle(deps) {
  const {
    cycleStartIso,
    loadExistingPosts,
    scrapePosts,
    mergePosts,
    hydrateLocalImages,
    persistPosts,
    pushMedia,
    upsertPosts,
    hydrateTagsDual,
    recordServiceHealth,
    buildTextTagger,
    buildVisionTagger,
    onNewTags,
    requestReauth,
    loadDeliveryState,
    saveDeliveryState,
    paths,
  } = deps;

  const cycleStart = Date.now();

  const scraped = await scrapePosts();
  const rawItems = scraped?.items ?? [];

  // Partition before doing anything else — paywalled stubs must never reach
  // mergePosts (which would persist them into posts.json) and must trigger
  // re-auth even if the cycle returns zero clean items afterwards.
  const paywalledItems = rawItems.filter(isPaywalledItem);
  const items = rawItems.filter((item) => !isPaywalledItem(item));
  const paywallDetected = paywalledItems.length > 0;

  if (paywallDetected) {
    console.warn(
      `[newsfeed] paywall stubs detected in ${paywalledItems.length}/${rawItems.length} posts — forcing re-auth on next cycle`,
    );
    if (typeof requestReauth === "function") {
      try {
        await requestReauth();
      } catch (err) {
        console.warn(`[newsfeed] requestReauth failed: ${err.message}`);
      }
    }
    try {
      await recordServiceHealth("newsfeed-scraper", "error", {
        startedAt: cycleStartIso,
        finishedAt: new Date().toISOString(),
        error: {
          message: `paywall stubs detected in ${paywalledItems.length}/${rawItems.length} posts — re-auth scheduled`,
        },
      });
    } catch (err) {
      console.warn(`[newsfeed] paywall health write failed: ${err.message}`);
    }
  }

  let deliveryState = typeof loadDeliveryState === "function"
    ? await loadDeliveryState()
    : { dbDirty: false, mediaDirty: false };
  const persistDeliveryState = async (updates) => {
    deliveryState = { ...deliveryState, ...updates };
    if (typeof saveDeliveryState === "function") {
      await saveDeliveryState(deliveryState);
    }
  };

  if (items.length === 0) {
    // Heartbeat even on truly-empty cycles. Without this, a stale `error`
    // row in service_health (e.g. yesterday's WalConflict) latches the
    // banner red across quiet weekend periods. The 819fe14 fix added a
    // heartbeat for the nochange-after-changes branch but missed this
    // earlier short-circuit.
    //
    // Exception: if every scraped item was a paywall stub, we already
    // wrote a service_health `error` row above. Overwriting it with `ok`
    // here would hide the regression from the banner — let the error
    // stand until the next successful cycle resolves it.
    if (deliveryState.dbDirty || deliveryState.mediaDirty) {
      const retained = await loadExistingPosts(paths.postsFile);
      if (deliveryState.dbDirty) {
        try {
          await upsertPosts(retained);
          await persistDeliveryState({ dbDirty: false });
        } catch (err) {
          try {
            await recordServiceHealth("newsfeed-scraper", "error", {
              startedAt: cycleStartIso,
              finishedAt: new Date().toISOString(),
              error: { message: err.message },
            });
          } catch {
            // Delivery state remains the durable failure signal.
          }
        }
      }
      if (deliveryState.mediaDirty) {
        try {
          const pushed = await pushMedia({ local: `${paths.mediaDir}/` });
          if (pushed?.ok) await persistDeliveryState({ mediaDirty: false });
        } catch {
          // The dirty marker remains durable for the next bounded cycle.
        }
      }
    }
    if (!paywallDetected && !deliveryState.dbDirty && !deliveryState.mediaDirty) {
      try {
        await recordServiceHealth("newsfeed-scraper", "ok", {
          startedAt: cycleStartIso,
          finishedAt: new Date().toISOString(),
        });
      } catch (err) {
        console.warn(`[newsfeed] heartbeat write failed: ${err.message}`);
      }
    }
    console.info(
      `[newsfeed] cycle empty paywalled=${paywalledItems.length} ms=${Date.now() - cycleStart}`,
    );
    return { changed: false, count: 0, paywalled: paywalledItems.length };
  }

  const existing = await loadExistingPosts(paths.postsFile);
  const { merged, changed } = mergePosts(existing, items);
  const imagesUpdated = await hydrateLocalImages(merged);

  const persistDirs = {
    dataDir: paths.dataDir,
    archiveDir: paths.archiveDir,
    mediaDir: paths.mediaDir,
    postsFile: paths.postsFile,
  };

  let dbWrites = 0;
  let pushedToHetzner = 0;

  // Pass 1 — freshness-first persist + DB upsert. Skip only on a true
  // nochange cycle (no new posts AND no image hydration), since otherwise
  // the dashboard waits behind the tagger.
  if (changed || imagesUpdated) {
    await persistPosts(merged, persistDirs);
    await persistDeliveryState({
      dbDirty: true,
      mediaDirty: deliveryState.mediaDirty || imagesUpdated,
    });
  }

  if (changed || imagesUpdated || deliveryState.dbDirty || deliveryState.mediaDirty) {
    // upsertPosts (DB) and pushMedia (rsync over Tailscale) are
    // independent of each other. Run them in parallel so a degraded
    // Tailscale link can't delay the DB write — routes preferring DB
    // would otherwise see stale data for up to 30s while rsync timed out.
    const upsertTask = deliveryState.dbDirty ? (async () => {
      try {
        await upsertPosts(merged);
        dbWrites += 1;
        await persistDeliveryState({ dbDirty: false });
        // Preserve the paywall `error` row written upstream — overwriting
        // it with `ok` here would hide a partial-paywall regression from
        // the banner. The next clean cycle resolves it back to ok.
        if (!paywallDetected) {
          await recordServiceHealth("newsfeed-scraper", "ok", {
            startedAt: cycleStartIso,
            finishedAt: new Date().toISOString(),
          });
        }
      } catch (err) {
        console.warn(`[newsfeed] db dual-write non-fatal: ${err.message}`);
        try {
          await recordServiceHealth("newsfeed-scraper", "error", {
            startedAt: cycleStartIso,
            finishedAt: new Date().toISOString(),
            error: { message: err.message },
          });
        } catch (_inner) {
          /* health write best-effort */
        }
      }
    })() : Promise.resolve();

    const pushTask = deliveryState.mediaDirty
      ? (async () => {
          const pushResult = await pushMedia({ local: `${paths.mediaDir}/` });
          if (pushResult?.ok) {
            pushedToHetzner = pushResult.transferred ?? 0;
            await persistDeliveryState({ mediaDirty: false });
          } else {
            console.warn(`[newsfeed] media push non-fatal: ${pushResult?.reason ?? "unknown"}`);
          }
        })()
      : Promise.resolve();

    await Promise.all([upsertTask, pushTask]);
  }

  // Pass 2 — slow tag hydration. Runs after the dashboard has already
  // seen the fresh post.
  let tagsUpdated = false;
  let newTagsAdded = 0;
  const textTagger = buildTextTagger();
  const visionTagger = buildVisionTagger();
  if (textTagger || visionTagger) {
    try {
      tagsUpdated = await hydrateTagsDual(merged, {
        textTagger,
        visionTagger,
        onNewTags: async (tags) => {
          const additions = await onNewTags(tags);
          newTagsAdded += additions?.length ?? 0;
        },
      });
    } catch (err) {
      console.warn(`[newsfeed] tag hydration failed: ${err.message}`);
    }
  }

  // Pass 3 — re-persist if tags actually changed. Skipped when the tagger
  // ran but nothing was updated.
  if (tagsUpdated) {
    await persistPosts(merged, persistDirs);
    await persistDeliveryState({ dbDirty: true });
    try {
      await upsertPosts(merged);
      dbWrites += 1;
      await persistDeliveryState({ dbDirty: false });
    } catch (err) {
      console.warn(`[newsfeed] db re-write after tagging non-fatal: ${err.message}`);
    }
  }

  // Heartbeat for nochange cycles so a stale `error` row in service_health
  // doesn't latch the banner red during quiet periods. Skip when this
  // cycle already wrote an `error` row for a paywall detection — we don't
  // want the heartbeat to mask the regression.
  if (!changed && !imagesUpdated && !tagsUpdated) {
    if (!paywallDetected && !deliveryState.dbDirty && !deliveryState.mediaDirty) {
      try {
        await recordServiceHealth("newsfeed-scraper", "ok", {
          startedAt: cycleStartIso,
          finishedAt: new Date().toISOString(),
        });
      } catch (err) {
        console.warn(`[newsfeed] heartbeat write failed: ${err.message}`);
      }
    }
    console.info(
      `[newsfeed] cycle nochange N=${merged.length} paywalled=${paywalledItems.length} ms=${Date.now() - cycleStart}`,
    );
    return { changed: false, count: merged.length, paywalled: paywalledItems.length };
  }

  console.info(
    `[newsfeed] cycle ok N=${merged.length} changed=${changed} imagesUpdated=${imagesUpdated} pushedToHetzner=${pushedToHetzner} tagsUpdated=${tagsUpdated} newTags=${newTagsAdded} paywalled=${paywalledItems.length} dbWrites=${dbWrites} ms=${Date.now() - cycleStart}`,
  );
  return { changed: true, count: merged.length, paywalled: paywalledItems.length };
}

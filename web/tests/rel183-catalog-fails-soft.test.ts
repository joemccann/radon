/**
 * REL-183 (R-514): the assistant catalog fails soft when its pin sources are
 * absent — one cached "unavailable" outcome, an empty catalog with a marker,
 * never a throw per turn.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const loadSpy = vi.fn();
vi.mock("../lib/assistant/pinSources", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/assistant/pinSources")>();
  return {
    ...actual,
    loadPinSourcesFromDisk: (...args: unknown[]) => loadSpy(...args),
  };
});

import {
  authorize,
  catalogUnavailable,
  resetCatalogCache,
  search,
} from "../lib/assistant/catalog";

beforeEach(() => {
  loadSpy.mockReset();
  resetCatalogCache();
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  resetCatalogCache();
  vi.restoreAllMocks();
});

describe("REL-183 — absent pin sources fail soft", () => {
  it("returns an empty catalog with the marker instead of throwing, once", () => {
    loadSpy.mockImplementation(() => {
      throw new Error("assistant catalog pin sources not found");
    });
    expect(search("regime")).toEqual([]);
    expect(catalogUnavailable()).toBe(true);
    const result = authorize("GET", "/api/regime");
    expect(result.ok).toBe(false);
    // The failure is CACHED: a second turn must not re-read the disk.
    search("gex");
    authorize("GET", "/api/gex");
    expect(loadSpy).toHaveBeenCalledTimes(1);
  });

  it("resetCatalogCache clears the unavailable latch", () => {
    loadSpy.mockImplementationOnce(() => {
      throw new Error("boom");
    });
    search("x");
    expect(catalogUnavailable()).toBe(true);
    resetCatalogCache();
    loadSpy.mockImplementation(() => {
      throw new Error("boom again");
    });
    search("x");
    expect(loadSpy).toHaveBeenCalledTimes(2);
  });
});

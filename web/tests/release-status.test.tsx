// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ReleaseStatus from "@/components/ReleaseStatus";
import { RELEASE_POLL_MS, RELEASE_VERSION_PATH, releaseState } from "@/lib/releaseStatus";

const RUNNING = {
  version: "0.8.0",
  sha: "aaaaaaaaaaaa",
  builtAt: "2026-10-09T18:02:00Z",
  channel: "production",
};

function jsonResponse(body: unknown, ok = true) {
  return {
    ok,
    json: async () => body,
  };
}

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_RADON_VERSION", RUNNING.version);
  vi.stubEnv("NEXT_PUBLIC_RADON_GIT_SHA", RUNNING.sha);
  vi.stubEnv("NEXT_PUBLIC_RADON_BUILT_AT", RUNNING.builtAt);
  vi.stubEnv("NEXT_PUBLIC_RADON_CHANNEL", RUNNING.channel);
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("releaseState", () => {
  const running = { version: "0.8.0", sha: "aaaaaaaaaaaa", builtAt: "", channel: "production" as const };

  it("treats a different sha as an update and a local bundle as local", () => {
    expect(releaseState(running, { ...running, sha: "bbbbbbbbbbbb", version: "0.8.1" }, false)).toBe("update");
    expect(releaseState(running, running, false)).toBe("current");
    expect(releaseState({ ...running, channel: "local" }, { ...running, sha: "bbbbbbbbbbbb" }, false)).toBe("local");
    expect(releaseState(running, null, true)).toBe("unknown");
  });
});

describe("ReleaseStatus", () => {
  it("asks /api/version with GET and no-store, then offers Reload only when the sha moved", async () => {
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      expect(url).toBe(RELEASE_VERSION_PATH);
      expect(init.method).toBe("GET");
      expect(init.cache).toBe("no-store");
      return jsonResponse({ version: "0.8.1", sha: "bbbbbbbbbbbb", builtAt: "2026-10-09T20:40:00Z", channel: "production" });
    });
    vi.stubGlobal("fetch", fetchMock);
    const reload = vi.fn();
    render(<ReleaseStatus reload={reload} />);

    expect(await screen.findByRole("button", { name: "Release 0.8.0, update available" })).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Reload" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Release 0.8.0, update available" }));
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(reload).toHaveBeenCalledTimes(1);
    expect(screen.getByText("0.8.1")).toBeTruthy();
    expect(screen.queryByText("Latest not read")).toBeNull();
  });

  it("does not fetch or reload while the gate is still the current deploy", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(RUNNING));
    vi.stubGlobal("fetch", fetchMock);
    const reload = vi.fn();
    render(<ReleaseStatus reload={reload} />);
    expect(await screen.findByRole("button", { name: "Release 0.8.0, current" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Release 0.8.0, current" }));
    expect(screen.queryByRole("button", { name: "Reload" })).toBeNull();
    expect(reload).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/version");
  });

  it("does not call the version endpoint from a local bundle", () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_CHANNEL", "local");
    vi.stubEnv("NEXT_PUBLIC_RADON_GIT_SHA", "dev");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<ReleaseStatus />);
    expect(screen.getByRole("button", { name: "Release 0.8.0, local build" })).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("says the latest was not read when the endpoint fails, and polls again on a visible tab", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const fetchMock = vi.fn(async () => jsonResponse({ error: "down" }, false));
    vi.stubGlobal("fetch", fetchMock);
    render(<ReleaseStatus />);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "Release 0.8.0, latest not read" })).toBeTruthy();

    await vi.advanceTimersByTimeAsync(RELEASE_POLL_MS);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});

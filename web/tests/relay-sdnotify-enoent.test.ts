/**
 * R-335 / REL-120: a missing `socat` cannot crash-loop the relay.
 *
 * `sdNotifyViaSocat` spawned a child with NO "error" listener and immediately
 * wrote to its stdin. A missing or unspawnable `socat` reports ENOENT
 * ASYNCHRONOUSLY via an `error` event, and `child.stdin.end(state)`
 * additionally errors on the never-opened pipe. Both fire after the
 * surrounding `try` has already returned, so neither could be caught: Node
 * raised `ERR_UNHANDLED_ERROR` and the realtime market-data process exited —
 * once at READY=1 and again every `heartbeatMs`. That is a crash loop that
 * drops every WebSocket quote subscriber.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { EventEmitter } from "node:events";

const spawnMock = vi.fn();
const execFileMock = vi.fn();

vi.mock("node:child_process", () => ({
  spawn: (...args: unknown[]) => spawnMock(...args),
  execFile: (...args: unknown[]) => execFileMock(...args),
}));

/** A child whose spawn fails asynchronously, exactly as ENOENT does. */
function enoentChild() {
  const child = new EventEmitter() as EventEmitter & { stdin: EventEmitter & { end: unknown } };
  const stdin = new EventEmitter() as EventEmitter & { end: unknown };
  stdin.end = vi.fn(() => {
    // The pipe was never opened; writing to it errors on the next tick.
    queueMicrotask(() => stdin.emit("error", Object.assign(new Error("EPIPE"), { code: "EPIPE" })));
  });
  child.stdin = stdin;
  queueMicrotask(() =>
    child.emit("error", Object.assign(new Error("spawn socat ENOENT"), { code: "ENOENT" })),
  );
  return child;
}

async function flush() {
  await new Promise((r) => setTimeout(r, 0));
}

beforeEach(() => {
  vi.resetModules();
  spawnMock.mockReset();
  execFileMock.mockReset();
});

describe("relay systemd notifier", () => {
  it("does not raise ERR_UNHANDLED_ERROR when socat is absent", async () => {
    const unhandled: unknown[] = [];
    const onUnhandled = (err: unknown) => unhandled.push(err);
    process.on("error", onUnhandled);

    execFileMock.mockImplementation((_cmd, _args, cb: (e: Error) => void) => {
      cb(Object.assign(new Error("systemd-notify not found"), { code: "ENOENT" }));
    });
    spawnMock.mockImplementation(() => enoentChild());

    const { createSdNotifier } = await import("../../scripts/lib/sdNotify.js");
    const notify = createSdNotifier({ socket: "/run/systemd/notify", watchdogUsec: 30_000_000 });

    expect(() => notify("READY=1")).not.toThrow();
    await flush();
    await flush();

    process.off("error", onUnhandled);
    expect(unhandled).toEqual([]);
  });

  it("stops re-spawning socat after the first ENOENT", async () => {
    execFileMock.mockImplementation((_cmd, _args, cb: (e: Error) => void) => {
      cb(Object.assign(new Error("systemd-notify not found"), { code: "ENOENT" }));
    });
    spawnMock.mockImplementation(() => enoentChild());

    const { createSdNotifier } = await import("../../scripts/lib/sdNotify.js");
    const notify = createSdNotifier({ socket: "/run/systemd/notify", watchdogUsec: 30_000_000 });

    notify("READY=1");
    await flush();
    await flush();
    const afterFirst = spawnMock.mock.calls.length;
    expect(afterFirst).toBe(1);

    notify("WATCHDOG=1");
    await flush();
    await flush();
    expect(spawnMock.mock.calls.length).toBe(afterFirst);
  });

  it("no-ops entirely when the watchdog is not configured", async () => {
    const { createSdNotifier } = await import("../../scripts/lib/sdNotify.js");
    const notify = createSdNotifier({ socket: "", watchdogUsec: 0 });
    notify("READY=1");
    expect(execFileMock).not.toHaveBeenCalled();
    expect(spawnMock).not.toHaveBeenCalled();
  });

  it("prefers systemd-notify and never reaches socat when it succeeds", async () => {
    execFileMock.mockImplementation((_cmd, _args, cb: (e: Error | null) => void) => cb(null));
    const { createSdNotifier } = await import("../../scripts/lib/sdNotify.js");
    const notify = createSdNotifier({ socket: "/run/systemd/notify", watchdogUsec: 30_000_000 });

    notify("WATCHDOG=1");
    await flush();
    expect(execFileMock).toHaveBeenCalledTimes(1);
    expect(spawnMock).not.toHaveBeenCalled();
  });

  it("falls back to socat when systemd-notify fails but socat works", async () => {
    execFileMock.mockImplementation((_cmd, _args, cb: (e: Error) => void) => {
      cb(new Error("no systemd-notify"));
    });
    const stdinEnd = vi.fn();
    spawnMock.mockImplementation(() => {
      const child = new EventEmitter() as EventEmitter & { stdin: unknown };
      const stdin = new EventEmitter() as EventEmitter & { end: unknown };
      stdin.end = stdinEnd;
      child.stdin = stdin;
      return child;
    });

    const { createSdNotifier } = await import("../../scripts/lib/sdNotify.js");
    const notify = createSdNotifier({ socket: "/run/systemd/notify", watchdogUsec: 30_000_000 });
    notify("WATCHDOG=1");
    await flush();

    expect(spawnMock).toHaveBeenCalledTimes(1);
    expect(stdinEnd).toHaveBeenCalledWith("WATCHDOG=1");
  });
});

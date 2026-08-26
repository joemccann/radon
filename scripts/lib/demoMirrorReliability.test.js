import { describe, expect, it, vi } from "vitest";

import {
  createBoundedFetch,
  runNewsfeedMirror,
} from "../db/mirror_newsfeed_to_demo.js";
import {
  HISTORY,
  LATEST_ONE,
  PER_KEY,
  PURGED_ACCOUNT_TABLES,
  isTransientTursoError,
  runMarketMirror,
} from "../db/mirror_market_snapshots_to_demo.js";


function post(id = "p1") {
  return {
    id,
    title: "Title",
    content: "Body",
    timestamp: "2026-07-11T12:00:00Z",
    images: "[]",
    raw_images: "[]",
    tags: "[]",
    tags_text: "[]",
    tags_vision: "[]",
    created_at: "2026-07-11T12:00:00Z",
    updated_at: "2026-07-11T12:00:00Z",
  };
}

function turso502() {
  const err = new Error("SERVER_ERROR: Server returned HTTP status 502");
  err.code = "SERVER_ERROR";
  err.status = 502;
  return err;
}

function scanRow(service = "gex") {
  return {
    service,
    scan_time: "2026-08-21T21:00:00Z",
    payload: "{}",
  };
}

describe("demo newsfeed mirror reliability", () => {
  it("retries an idempotent transient destination write and records recovery", async () => {
    const src = {
      execute: vi.fn()
        .mockResolvedValueOnce({ rows: [post()] })
        .mockResolvedValue({ rows: [] }),
    };
    const dst = {
      batch: vi.fn()
        .mockRejectedValueOnce(new TypeError("fetch failed"))
        .mockResolvedValueOnce(undefined),
      execute: vi.fn().mockResolvedValue({
        rows: [{ n: 1, newest: "2026-07-11T12:00:00Z" }],
      }),
    };
    const sleep = vi.fn().mockResolvedValue(undefined);
    const logs = [];

    const result = await runNewsfeedMirror({
      src,
      dst,
      limit: 400,
      maxAttempts: 3,
      sleep,
      log: (entry) => logs.push(entry),
      now: () => "2026-07-11T12:00:00.000Z",
      runId: "run-1",
    });

    expect(result).toEqual({ mirrored: 1, total: 1 });
    expect(dst.batch).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenCalledTimes(1);
    expect(src.execute).toHaveBeenLastCalledWith(expect.objectContaining({
      args: expect.arrayContaining(["demo-newsfeed-mirror", "ok"]),
    }));
    expect(logs).toContainEqual(expect.objectContaining({
      run_id: "run-1",
      phase: "destination_write",
      event: "retry",
      attempt: 1,
    }));
  });

  it("bounds persistent failures and records an error heartbeat", async () => {
    const src = {
      execute: vi.fn()
        .mockResolvedValueOnce({ rows: [post()] })
        .mockResolvedValue({ rows: [] }),
    };
    const dst = {
      batch: vi.fn().mockRejectedValue(new TypeError("fetch failed")),
      execute: vi.fn(),
    };

    await expect(runNewsfeedMirror({
      src,
      dst,
      limit: 400,
      maxAttempts: 3,
      sleep: vi.fn().mockResolvedValue(undefined),
      log: vi.fn(),
      now: () => "2026-07-11T12:00:00.000Z",
      runId: "run-2",
    })).rejects.toThrow("fetch failed");

    expect(dst.batch).toHaveBeenCalledTimes(3);
    expect(src.execute).toHaveBeenLastCalledWith(expect.objectContaining({
      args: expect.arrayContaining(["demo-newsfeed-mirror", "error"]),
    }));
  });

  it("adds an abort deadline without discarding a caller signal", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response("ok"));
    const caller = new AbortController();
    const boundedFetch = createBoundedFetch({ timeoutMs: 1000, fetchImpl });

    await boundedFetch("https://example.test", { signal: caller.signal });

    const init = fetchImpl.mock.calls[0][1];
    expect(init.signal).toBeInstanceOf(AbortSignal);
    caller.abort();
    expect(init.signal.aborted).toBe(true);
  });
});

describe("demo market mirror reliability", () => {
  it("classifies Turso HTTP 502 source reads as transient", () => {
    expect(isTransientTursoError(turso502())).toBe(true);
    expect(isTransientTursoError(new Error("no such table: scan_snapshots"))).toBe(false);
  });

  it("retries a transient scan_snapshots source 502 then mirrors", async () => {
    // Incident ba86fe0a: oneshot paged P1 after a single Turso 502 on the
    // scan_snapshots window read while sibling tables mirrored fine.
    const src = {
      execute: vi.fn(async (sql) => {
        const text = String(sql);
        if (text.includes("FROM scan_snapshots") && text.includes("ROW_NUMBER")) {
          if (src.execute.mock.calls.filter((c) => String(c[0]).includes("ROW_NUMBER")).length === 1) {
            throw turso502();
          }
          return { columns: ["service", "scan_time", "payload", "rn"], rows: [scanRow("gex")] };
        }
        if (text.includes("FROM gex_snapshots")) {
          return { columns: ["ticker", "scan_time", "payload", "rn"], rows: [{ ticker: "SPX", scan_time: "2026-08-21T21:00:00Z", payload: "{}" }] };
        }
        if (text.includes("ORDER BY") && text.includes("LIMIT")) {
          const table = text.match(/FROM (\w+)/)?.[1];
          return {
            columns: ["scan_time", "payload"],
            rows: [{ scan_time: "2026-08-21T21:00:00Z", payload: JSON.stringify({ table }) }],
          };
        }
        return { columns: [], rows: [] };
      }),
    };
    const dst = {
      execute: vi.fn().mockResolvedValue({ rows: [] }),
      batch: vi.fn().mockResolvedValue(undefined),
    };
    const sleep = vi.fn().mockResolvedValue(undefined);
    const logs = [];

    const result = await runMarketMirror({
      src,
      dst,
      tables: {
        latestOne: [{ table: "scanner_snapshots", orderCol: "scan_time" }],
        perKey: [{ table: "scan_snapshots", key: "service", orderCol: "scan_time" }],
        history: [],
        purgedAccountTables: [],
      },
      maxAttempts: 3,
      sleep,
      log: (entry) => logs.push(entry),
      now: () => "2026-08-21T21:45:00.000Z",
      runId: "ba86",
    });

    expect(result.total).toBeGreaterThanOrEqual(1);
    expect(result.failures).toEqual([]);
    expect(sleep).toHaveBeenCalled();
    expect(logs).toContainEqual(expect.objectContaining({
      run_id: "ba86",
      phase: "scan_snapshots:source_read",
      event: "retry",
      attempt: 1,
    }));
  });

  it("still fails the unit when scan_snapshots 502 persists past the budget", async () => {
    const src = {
      execute: vi.fn(async (sql) => {
        if (String(sql).includes("FROM scan_snapshots")) throw turso502();
        return { columns: ["scan_time", "payload"], rows: [{ scan_time: "2026-08-21T21:00:00Z", payload: "{}" }] };
      }),
    };
    const dst = {
      execute: vi.fn().mockResolvedValue({ rows: [] }),
      batch: vi.fn().mockResolvedValue(undefined),
    };

    await expect(runMarketMirror({
      src,
      dst,
      tables: {
        latestOne: [],
        perKey: [{ table: "scan_snapshots", key: "service", orderCol: "scan_time" }],
        history: [],
        purgedAccountTables: [],
      },
      maxAttempts: 3,
      sleep: vi.fn().mockResolvedValue(undefined),
      log: vi.fn(),
      now: () => "2026-08-21T21:45:00.000Z",
      runId: "ba86-fail",
    })).rejects.toThrow(/required table failures: scan_snapshots/);

    expect(src.execute).toHaveBeenCalledTimes(3);
  });

  it("does not retry non-transient SQL errors", async () => {
    const src = {
      execute: vi.fn().mockRejectedValue(new Error("no such table: scan_snapshots")),
    };
    const dst = {
      execute: vi.fn().mockResolvedValue({ rows: [] }),
      batch: vi.fn(),
    };

    await expect(runMarketMirror({
      src,
      dst,
      tables: {
        latestOne: [],
        perKey: [{ table: "scan_snapshots", key: "service", orderCol: "scan_time" }],
        history: [],
        purgedAccountTables: [],
      },
      maxAttempts: 3,
      sleep: vi.fn().mockResolvedValue(undefined),
      log: vi.fn(),
      now: () => "2026-08-21T21:45:00.000Z",
      runId: "sql-err",
    })).rejects.toThrow(/required table failures: scan_snapshots/);

    expect(src.execute).toHaveBeenCalledTimes(1);
  });

  it("purges flow_analysis_snapshots from the demo DB by default (T-108)", async () => {
    // Account-derived rows must never exist in the public demo database; the
    // recurring job is the only enforcement, so the default purge list must
    // actually issue the DELETE.
    expect(PURGED_ACCOUNT_TABLES).toEqual(["flow_analysis_snapshots"]);
    const src = { execute: vi.fn() };
    const dst = {
      execute: vi.fn().mockResolvedValue({ rows: [] }),
      batch: vi.fn().mockResolvedValue(undefined),
    };

    const result = await runMarketMirror({
      src,
      dst,
      tables: { latestOne: [], perKey: [], history: [] },
      maxAttempts: 3,
      sleep: vi.fn().mockResolvedValue(undefined),
      log: vi.fn(),
      now: () => "2026-08-23T00:00:00.000Z",
      runId: "t108-purge",
    });

    expect(result.failures).toEqual([]);
    expect(dst.execute).toHaveBeenCalledWith("DELETE FROM flow_analysis_snapshots");
    expect(src.execute).not.toHaveBeenCalled();
  });

  it("retries a transient purge 502 then completes (T-108)", async () => {
    const dst = {
      execute: vi.fn()
        .mockRejectedValueOnce(turso502())
        .mockResolvedValue({ rows: [] }),
      batch: vi.fn().mockResolvedValue(undefined),
    };
    const sleep = vi.fn().mockResolvedValue(undefined);
    const logs = [];

    const result = await runMarketMirror({
      src: { execute: vi.fn() },
      dst,
      tables: { latestOne: [], perKey: [], history: [] },
      maxAttempts: 3,
      sleep,
      log: (entry) => logs.push(entry),
      now: () => "2026-08-23T00:00:00.000Z",
      runId: "t108-retry",
    });

    expect(result.failures).toEqual([]);
    expect(dst.execute).toHaveBeenCalledTimes(2);
    expect(dst.execute).toHaveBeenNthCalledWith(2, "DELETE FROM flow_analysis_snapshots");
    expect(sleep).toHaveBeenCalledTimes(1);
    expect(logs).toContainEqual(expect.objectContaining({
      run_id: "t108-retry",
      phase: "flow_analysis_snapshots:account_purge",
      event: "retry",
      attempt: 1,
    }));
  });

  it("fails the run when the purge 502 persists past the budget (T-108)", async () => {
    // The purge used to warn "[mirror] SKIP purge" and exit 0 with
    // account-derived rows still live on demo.radon.run.
    const dst = {
      execute: vi.fn().mockRejectedValue(turso502()),
      batch: vi.fn().mockResolvedValue(undefined),
    };
    const sleep = vi.fn().mockResolvedValue(undefined);
    const logs = [];

    await expect(runMarketMirror({
      src: { execute: vi.fn() },
      dst,
      tables: { latestOne: [], perKey: [], history: [] },
      maxAttempts: 3,
      sleep,
      log: (entry) => logs.push(entry),
      now: () => "2026-08-23T00:00:00.000Z",
      runId: "t108-fail",
    })).rejects.toThrow(/account-data purge failed for flow_analysis_snapshots/);

    expect(dst.execute).toHaveBeenCalledTimes(3);
    expect(dst.execute).toHaveBeenCalledWith("DELETE FROM flow_analysis_snapshots");
    expect(sleep).toHaveBeenCalledTimes(2);
    expect(logs).toContainEqual(expect.objectContaining({
      run_id: "t108-fail",
      phase: "flow_analysis_snapshots:account_purge",
      event: "failed",
      attempt: 3,
    }));
  });
});

describe("account-data purge (REL-049 / R-097)", () => {
  /**
   * `flow_analysis_snapshots` can carry portfolio-derived flow rows and must
   * never exist in the PUBLIC demo database. The purge ran outside the retry
   * ladder and outside `failures[]`, so the documented transient 502 was
   * console.warn'd, the mirror proceeded, reported done and exited 0 —
   * production account rows left in place, indistinguishable from a clean run.
   */
  it("retries a transient 502 on the purge instead of skipping it", async () => {
    const src = {
      execute: vi.fn().mockResolvedValue({ columns: [], rows: [] }),
    };
    let purgeAttempts = 0;
    const dst = {
      execute: vi.fn(async (sql) => {
        if (String(sql).includes("DELETE FROM flow_analysis_snapshots")) {
          purgeAttempts += 1;
          if (purgeAttempts === 1) throw turso502();
        }
        return { rows: [] };
      }),
      batch: vi.fn().mockResolvedValue(undefined),
    };

    const result = await runMarketMirror({
      src,
      dst,
      tables: { latestOne: [], perKey: [], history: [], purgedAccountTables: ["flow_analysis_snapshots"] },
      maxAttempts: 3,
      sleep: vi.fn().mockResolvedValue(undefined),
      log: () => {},
      now: () => "2026-08-23T12:00:00.000Z",
      runId: "purge-retry",
    });

    expect(purgeAttempts).toBe(2);
    expect(result.failures).toEqual([]);
  });

  it("refuses to mirror at all when the purge never succeeds", async () => {
    const src = {
      execute: vi.fn().mockResolvedValue({
        columns: ["scan_time", "payload"],
        rows: [{ scan_time: "2026-08-23T12:00:00Z", payload: "{}" }],
      }),
    };
    const dst = {
      execute: vi.fn(async (sql) => {
        if (String(sql).includes("DELETE FROM flow_analysis_snapshots")) throw turso502();
        return { rows: [] };
      }),
      batch: vi.fn().mockResolvedValue(undefined),
    };

    await expect(runMarketMirror({
      src,
      dst,
      tables: {
        latestOne: [{ table: "scanner_snapshots", orderCol: "scan_time" }],
        perKey: [],
        history: [],
        purgedAccountTables: ["flow_analysis_snapshots"],
      },
      maxAttempts: 2,
      sleep: vi.fn().mockResolvedValue(undefined),
      log: () => {},
      now: () => "2026-08-23T12:00:00.000Z",
      runId: "purge-fatal",
    })).rejects.toThrow(/account-data purge failed/);

    expect(dst.batch).not.toHaveBeenCalled();
  });
});

describe("equibles per-ticker decks reach the demo database", () => {
  // Both Equibles per-ticker surfaces (13F positioning, filing-forensics
  // dossier) were absent from every mirror list, so demo.radon.run could never
  // show them for any ticker no matter what production held.
  const byTable = Object.fromEntries(PER_KEY.map((entry) => [entry.table, entry]));

  it("mirrors the newest 13F vintage per ticker on the column the route reads", () => {
    // web/app/api/equibles-smart-money-13f/route.ts orders by report_date DESC.
    expect(byTable.equibles_13f_snapshots).toEqual({
      table: "equibles_13f_snapshots",
      key: "ticker",
      orderCol: "report_date",
    });
  });

  it("mirrors the filing-forensics dossier per ticker", () => {
    expect(byTable.equibles_filing_forensics).toEqual({
      table: "equibles_filing_forensics",
      key: "ticker",
      orderCol: "as_of",
    });
  });

  it("leaves equibles_13f_holders out — nothing reads it", () => {
    // The holder rows are write-only depth: the panel renders payload.holders
    // out of the snapshot JSON, and no route or component queries the table.
    const named = [...LATEST_ONE, ...PER_KEY, ...HISTORY].map((e) => e.table);
    expect(named).not.toContain("equibles_13f_holders");
  });

  it("issues the per-key query and prune for both equibles tables", async () => {
    const src = {
      execute: vi.fn(async (sql) => {
        const table = String(sql).match(/FROM (\w+)/)?.[1];
        return { columns: ["ticker", "payload", "rn"], rows: [{ ticker: "AAPL", payload: "{}" }] };
      }),
    };
    const dst = { execute: vi.fn().mockResolvedValue({ rows: [] }), batch: vi.fn().mockResolvedValue(undefined) };

    const result = await runMarketMirror({
      src,
      dst,
      tables: {
        latestOne: [],
        perKey: [byTable.equibles_13f_snapshots, byTable.equibles_filing_forensics],
        history: [],
        purgedAccountTables: [],
      },
      maxAttempts: 1,
      sleep: vi.fn().mockResolvedValue(undefined),
      log: () => {},
      now: () => "2026-08-25T12:00:00.000Z",
      runId: "equibles",
    });

    expect(result.failures).toEqual([]);
    expect(result.total).toBe(2);
    const read = src.execute.mock.calls.map((c) => String(c[0]));
    expect(read.some((s) => /PARTITION BY ticker ORDER BY report_date DESC/.test(s))).toBe(true);
    expect(read.some((s) => /PARTITION BY ticker ORDER BY as_of DESC/.test(s))).toBe(true);
  });
});

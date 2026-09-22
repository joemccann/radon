// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, renderHook, waitFor, cleanup } from "@testing-library/react";
import { useSyncHook } from "@/lib/useSyncHook";
const config = { endpoint: "/api/example", hasPost: false, extractTimestamp: (d: {scan_time:string}) => d.scan_time };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it("keeps last good rows and timestamp when HTTP 200 carries a failed empty scan", async () => {
  let failed = false;
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(failed
    ? { scan_time:"", results:[], scan_succeeded:false, error: JSON.stringify({error:"Radon API 502: Subprocess capacity exhausted"}) }
    : {scan_time:"2026-09-17T10:00:00Z",results:[{ticker:"AAPL"}]}),{headers:{"content-type":"application/json"}})));
  const { result } = renderHook(() => useSyncHook(config, true));
  await waitFor(() => expect(result.current.loading).toBe(false));
  const previous = result.current.data;
  failed = true;
  await act(async () => { await result.current.syncNow(); });
  await waitFor(() => expect(result.current.error).toContain("This service is busy"));
  expect(result.current.data).toEqual(previous);
  expect(result.current.lastSync).toBe("2026-09-17T10:00:00Z");
});

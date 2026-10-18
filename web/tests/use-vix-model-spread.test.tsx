/** @vitest-environment jsdom */
import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useVixModelSpread } from "@/lib/useVixModelSpread";
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals();});
it("uses hourly no-store GETs and a GET-only manual refresh",async()=>{
 vi.useFakeTimers();
 const stamp="2026-10-18T02:50:00Z";
 const fetchMock=vi.fn().mockImplementation(async()=>new Response(JSON.stringify({scan_time:stamp,status:"missing",missing:true,series:[],current:null,count:0,as_of:null,data_date:null}),{status:200}));
 vi.stubGlobal("fetch",fetchMock);
 const {result,unmount}=renderHook(()=>useVixModelSpread());
 await act(async()=>{await vi.advanceTimersByTimeAsync(0);});
 expect(result.current.lastSync).toBe(stamp);
 expect(fetchMock).toHaveBeenCalledTimes(1);
 await act(async()=>{await vi.advanceTimersByTimeAsync(3_600_000);});
 expect(fetchMock).toHaveBeenCalledTimes(2);
 act(()=>result.current.syncNow());
 await act(async()=>{await vi.advanceTimersByTimeAsync(0);});
 expect(fetchMock).toHaveBeenCalledTimes(3);
 for(const [url,options] of fetchMock.mock.calls){expect(url).toBe("/api/vix-model-spread");expect(options).toMatchObject({method:"GET",cache:"no-store"});}
 unmount();
});

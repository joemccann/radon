// @vitest-environment jsdom
import {render,screen,fireEvent} from "@testing-library/react";
import {describe,it,expect,vi} from "vitest";
import {researchRows,researchGroups,isResearchSnapshot} from "@/lib/uwResearch";
const {mock}=vi.hoisted(()=>({mock:{data:null as unknown,connected:false,refresh:vi.fn(async()=>{})}}));
vi.mock("@/lib/UwResearchContext",()=>({useUwResearch:()=>mock}));
import UwResearchPanel from "@/components/flow-analysis/UwResearchPanel";
const sample={schema_version:1,ticker:"AAPL",source:"unusual_whales",execution_source:"ib",oi_cadence:"daily",stream:{status:"degraded",connection:{gaps:2},latest:{}},research:{status:"partial",sections:{multi_leg:{state:"stale",as_of:"2026-10-20",data:{data:[{strategy:"call_vertical",net_premium:"-2400.50",max_loss:"1500"}]}}}}};
describe("advanced research observations",()=>{
 it("keeps unknown identity and missing payloads out of the panel",()=>{expect(isResearchSnapshot({},"AAPL")).toBe(false);expect(isResearchSnapshot(sample,"MSFT")).toBe(false);expect(isResearchSnapshot(sample,"AAPL")).toBe(true)});
 it("unwraps durable frame payloads and complete board rows",()=>{expect(researchRows({payload:{rows:[{gamma:1},{gamma:2}]}})).toEqual([{gamma:1},{gamma:2}])});
 it("labels gaps, daily OI, stale strategy data and broker source",()=>{mock.data=sample;render(<UwResearchPanel ticker="AAPL"/>);expect(screen.getByText("Execution quotes: IB")).toBeTruthy();expect(screen.getByText("OI confirmation: daily")).toBeTruthy();expect(screen.getByText(/Stream gaps recorded: 2/)).toBeTruthy();expect(screen.getByText("stale")).toBeTruthy();expect(screen.getByText("-2400.50")).toBeTruthy()});
 it("refreshes research explicitly",()=>{mock.data=sample;render(<UwResearchPanel ticker="AAPL"/>);fireEvent.click(screen.getByRole("button",{name:"Refresh research"}));expect(mock.refresh).toHaveBeenCalled()});
 it("preserves stream completeness, reconciliation basis and observation times",()=>{
  mock.data={...sample,stream:{...sample.stream,channels:{greek_flow:{status:"live",last_event_at:"2026-10-21T14:00:00Z"}},latest:{greek_flow:{minute:{complete:false,basis:"rest_confirmed",event_time:"2026-10-21T13:58:00Z",ingested_at:"2026-10-21T14:00:01Z",payload:{delta_flow:42}}}}}};
  render(<UwResearchPanel ticker="AAPL"/>);
  expect(screen.getByText(/Partial source observation/).textContent).toContain("Basis: rest_confirmed");
  expect(screen.getByText(/Partial source observation/).textContent).toContain("Source time: 2026-10-21T13:58:00Z");
  expect(screen.getByText(/Partial source observation/).textContent).toContain("Received: 2026-10-21T14:00:01Z");
  expect(screen.getByText(/Channel timestamps describe the shared feed/)).toBeTruthy();
 });
 it("preserves every labeled sibling dataset",()=>{
  for(const [left,right] of [["monthly","year_month"],["stats","blocks"],["fixed","sliding"],["dividends","splits"]]){
   const data={[left]:{data:[{price:11}]},[right]:{data:[{price:22}]}};
   expect(researchGroups(data)).toHaveLength(2);
   expect(researchGroups(data).every(group=>group.label.length>0)).toBe(true);
   expect(researchRows(data)).toEqual([{price:11},{price:22}]);
  }
 });
 it("shows sibling datasets and bounded source coverage with leg availability",()=>{
  mock.data={...sample,research:{status:"partial",sections:{
   multi_leg:{state:"ok",data:{coverage:{truncated:true,pages:2,page_limit:50,range_start:"2026-10-20T14:00:00Z",range_end:"2026-10-21T14:00:00Z"},trades:[{strategy:"vertical",legs_state:"unavailable"},{strategy:"calendar",legs_state:"not_requested"}]}},
   futures:{state:"ok",data:{stats:{data:[{price:11}]},blocks:{data:[{price:22}]}}},
   corporate_actions:{state:"ok",data:{dividends:{data:[{amount:3}]},splits:{data:[]}}}
  }}};
  render(<UwResearchPanel ticker="AAPL"/>);
  const coverage=screen.getByText(/Partial source sample: collection bound reached/);
  expect(coverage.textContent).toContain("Pages collected: 2");
  expect(coverage.textContent).toContain("Page limit: 50");
  expect(coverage.textContent).toContain("Range start: 2026-10-20T14:00:00Z");
  expect(coverage.textContent).toContain("Range end: 2026-10-21T14:00:00Z");
  expect(screen.getByText("Leg details: Unavailable")).toBeTruthy();
  expect(screen.getByText("Leg details: Not requested")).toBeTruthy();
  for(const label of ["Contract statistics","Block trades","Dividends","Splits"])expect(screen.getByText(label)).toBeTruthy();
  expect(screen.getByText("11")).toBeTruthy();expect(screen.getByText("22")).toBeTruthy();
 });

});

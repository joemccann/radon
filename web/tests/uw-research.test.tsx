import {render,screen,fireEvent} from "@testing-library/react";
import {describe,it,expect,vi} from "vitest";
import {researchRows,isResearchSnapshot} from "@/lib/uwResearch";
const {mock}=vi.hoisted(()=>({mock:{data:null as unknown,connected:false,refresh:vi.fn(async()=>{})}}));
vi.mock("@/lib/UwResearchContext",()=>({useUwResearch:()=>mock}));
import UwResearchPanel from "@/components/flow-analysis/UwResearchPanel";
const sample={schema_version:1,ticker:"AAPL",source:"unusual_whales",execution_source:"ib",oi_cadence:"daily",stream:{status:"degraded",connection:{gaps:2},latest:{}},research:{status:"partial",sections:{multi_leg:{state:"stale",as_of:"2026-10-20",data:{data:[{strategy:"call_vertical",net_premium:"-2400.50",max_loss:"1500"}]}}}}};
describe("advanced research observations",()=>{
 it("keeps unknown identity and missing payloads out of the panel",()=>{expect(isResearchSnapshot({},"AAPL")).toBe(false);expect(isResearchSnapshot(sample,"MSFT")).toBe(false);expect(isResearchSnapshot(sample,"AAPL")).toBe(true)});
 it("unwraps durable frame payloads and complete board rows",()=>{expect(researchRows({payload:{rows:[{gamma:1},{gamma:2}]}})).toEqual([{gamma:1},{gamma:2}])});
 it("labels gaps, daily OI, stale strategy data and broker source",()=>{mock.data=sample;render(<UwResearchPanel ticker="AAPL"/>);expect(screen.getByText("Execution quotes: IB")).toBeTruthy();expect(screen.getByText("OI confirmation: daily")).toBeTruthy();expect(screen.getByText(/Stream gaps recorded: 2/)).toBeTruthy();expect(screen.getByText("stale")).toBeTruthy();expect(screen.getByText("-2400.50")).toBeTruthy()});
 it("refreshes research explicitly",()=>{mock.data=sample;render(<UwResearchPanel ticker="AAPL"/>);fireEvent.click(screen.getByRole("button",{name:"Refresh research"}));expect(mock.refresh).toHaveBeenCalled()});
});

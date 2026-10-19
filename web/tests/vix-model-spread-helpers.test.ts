import { describe, expect, it } from "vitest";
import { formatSpreadZ, formatVolPoints, spreadBandState, spreadWindowStats, type VixModelSpreadEntry } from "@/lib/vixModelSpread";
const entry: VixModelSpreadEntry = {
  date:"2026-10-16",vix:20,vixeq:40,cor1m:10,model_vix:13,spread:7,
  mean_20:4,std_20:1,upper_20:6,lower_20:2,z_20:3,
  mean_252:5,std_252:2,upper_252:9,lower_252:1,z_252:1,
};
describe("precomputed VIX model window helpers",()=>{
 it("selects the stored window without recalculating it",()=>{expect(spreadWindowStats(entry,20)).toEqual({mean:4,std:1,upper:6,lower:2,z:3});expect(spreadWindowStats(entry,252)).toEqual({mean:5,std:2,upper:9,lower:1,z:1});});
 it("classifies strictly outside the selected 2-SD band",()=>{expect(spreadBandState(entry,20)).toBe("ABOVE BAND");expect(spreadBandState(entry,252)).toBe("WITHIN BAND");expect(spreadBandState({...entry,spread:1.99},20)).toBe("BELOW BAND");});
 it.each([2,6])("keeps exact band boundary %s within-band",spread=>{expect(spreadBandState({...entry,spread},20)).toBe("WITHIN BAND");});
 it("treats an ordinary positive basis within bands as normal",()=>{expect(spreadBandState({...entry,spread:4},20)).toBe("WITHIN BAND");});
 it("distinguishes warmup from no variation",()=>{expect(spreadBandState(null,20)).toBe("WARMUP");expect(spreadBandState({...entry,std_20:null},20)).toBe("WARMUP");expect(spreadBandState({...entry,std_20:0,z_20:null,upper_20:4,lower_20:4},20)).toBe("NO VARIATION");});
 it.each([NaN,Infinity,-Infinity])("never displays nonfinite %s",value=>{expect(formatVolPoints(value)).toBe("---");expect(formatSpreadZ(value)).toBe("---");expect(spreadBandState({...entry,spread:value},20)).toBe("WARMUP");expect(spreadWindowStats({...entry,z_20:value},20).z).toBeNull();});
 it("formats signed volatility points and z consistently",()=>{expect(formatVolPoints(6.754055875)).toBe("6.75");expect(formatVolPoints(-1.25)).toBe("-1.25");expect(formatSpreadZ(-2.129)).toBe("-2.13");expect(formatVolPoints(null)).toBe("---");expect(formatSpreadZ(undefined)).toBe("---");});
});

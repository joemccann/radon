import { describe, expect, it } from 'vitest';
import { calculateValuation, valuationErrors, type ValuationInputs } from '../lib/researchWorkbench/valuation';
const base: ValuationInputs = { ticker: 'Example', cashFlow: 100, growth: 0, discountRate: .1, terminalGrowth: 0, years: 5, netDebt: 200, shares: 100, ebitda: 200, entryMultiple: 10, exitMultiple: 10, debtFraction: .5, debtPaydown: 100 };
describe('research scenario valuation', () => {
  it('values a constant perpetuity and subtracts debt once', () => {
    const r = calculateValuation(base);
    expect(r.enterpriseValue).toBeCloseTo(1000); expect(r.equityValue).toBeCloseTo(800); expect(r.perShare).toBeCloseTo(8);
    expect(r.sponsorEquity).toBe(1000); expect(r.exitDebt).toBe(500); expect(r.moic).toBe(1.5); expect(r.irr).toBeCloseTo(1.5 ** .2 - 1);
  });
  it('retains negative cash flows and net cash rather than absolute values', () => {
    expect(calculateValuation({...base, cashFlow:-100}).enterpriseValue).toBeCloseTo(-1000);
    expect(calculateValuation({...base, netDebt:-200}).equityValue).toBeCloseTo(1200);
  });
  it('floors debt at zero and sponsor losses at invested equity', () => {
    expect(calculateValuation({...base, debtPaydown:1000}).exitDebt).toBe(0);
    const r=calculateValuation({...base, exitMultiple:1, debtPaydown:0}); expect(r.exitEquity).toBe(0); expect(r.irr).toBe(-1);
  });
  it.each([{shares:0},{years:0},{years:1.5},{years:31},{discountRate:.02,terminalGrowth:.02},{growth:-1},{debtFraction:1},{debtFraction:-.1},{ebitda:0},{cashFlow:NaN},{netDebt:Infinity},{entryMultiple:0},{debtPaydown:-1}])('rejects invalid scenario %j', patch => {
    expect(valuationErrors({...base,...patch}).length).toBeGreaterThan(0);
    expect(()=>calculateValuation({...base,...patch})).toThrow();
  });
  it('preserves scale and per-share invariance', () => {
    const r=calculateValuation(base); const s=calculateValuation({...base,cashFlow:1000,netDebt:2000,shares:1000,ebitda:2000,debtPaydown:1000});
    expect(s.enterpriseValue).toBeCloseTo(r.enterpriseValue*10); expect(s.perShare).toBeCloseTo(r.perShare); expect(s.irr).toBeCloseTo(r.irr);
  });
});

import {comparableMultiples,medianMultiple} from '../lib/researchWorkbench/valuation';
describe('operator comparable set',()=>{
 it('uses median rather than outlier-sensitive mean',()=>expect(medianMultiple([{name:'A',enterpriseValue:100,ebitda:10},{name:'B',enterpriseValue:120,ebitda:10},{name:'C',enterpriseValue:1000,ebitda:10}])).toBe(12));
 it('handles empty and even peer counts',()=>{expect(medianMultiple([])).toBeNull();expect(medianMultiple([{name:'A',enterpriseValue:100,ebitda:10},{name:'B',enterpriseValue:120,ebitda:10}])).toBe(11);});
 it('rejects negative earnings and duplicate companies',()=>{expect(()=>comparableMultiples([{name:'A',enterpriseValue:100,ebitda:-10}])).toThrow();expect(()=>comparableMultiples([{name:'A',enterpriseValue:100,ebitda:10},{name:'a',enterpriseValue:100,ebitda:10}])).toThrow();});
});

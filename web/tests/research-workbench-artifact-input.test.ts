import { describe, expect, it } from 'vitest';
import { buildInvestorArtifactInput, createInvestorWorkbook } from '../lib/researchWorkbench/artifacts';
import { createSourceDocument, extractDocumentFacts, type ResearchWorkspace } from '../lib/researchWorkbench';
import type { PortfolioData, PortfolioPosition } from '../lib/types';
function workspace():ResearchWorkspace {
  const documents=[
    createSourceDocument({id:'plain',title:'Authorized email',url:null,publishedAt:'2026-09-09',kind:'filing',ticker:'ACME',text:'2026-Q2 Net income: $-12 million'}),
    createSourceDocument({id:'linked',title:'Filing',url:'https://example.com/filing',publishedAt:'2026-09-10',kind:'filing',ticker:'ACME',text:'2026-Q2 Revenue: $10 million'}),
    createSourceDocument({id:'future',title:'Future report',url:'https://example.com/future',publishedAt:'2026-10-01',kind:'filing',ticker:'ACME',text:'2026-Q3 Revenue: $999 million'}),
  ];
  return {version:1,documents,facts:documents.flatMap(extractDocumentFacts)};
}
function portfolio(last_sync='2026-09-10T12:00:00Z'):PortfolioData {
  const position:PortfolioPosition={id:1,ticker:'ACME',structure:'Short put',structure_type:'single',risk_profile:'undefined',expiry:'2026-10-16',contracts:2,direction:'SHORT',entry_cost:-1234.5,max_risk:null,market_value:-1000.25,legs:[],kelly_optimal:null,target:null,stop:null,entry_date:'2026-09-09'};
  return {last_sync,bankroll:-20,peak_value:100,positions:[position],position_count:1,total_deployed_pct:0,total_deployed_dollars:0,remaining_capacity_pct:100,defined_risk_count:0,undefined_risk_count:1,avg_kelly_optimal:null};
}
describe('investor artifact point-in-time provenance',()=>{
  it('retains null-URL evidence with publication date and operator attribution',()=>{
    const input=buildInvestorArtifactInput({workspace:workspace(),asOf:'2026-09-10'});
    expect(input.sources[0]).toEqual({title:'Authorized email · 2026-09-09 · operator-supplied text',url:'',passage:'2026-Q2 Net income: $-12 million'});
    expect(input.sources[1]).toMatchObject({title:'Filing · 2026-09-10',url:'https://example.com/filing'});
    expect(input.snapshot).toContain('2026-Q2 Net income: $-12 million');
  });
  it('uses the selected date for all source and analysis projections',()=>{
    const input=buildInvestorArtifactInput({workspace:workspace(),asOf:'2026-09-09',ticker:'ACME'});
    expect(input.asOf).toBe('2026-09-09');expect(input.sources).toHaveLength(1);
    expect(JSON.stringify(input)).not.toContain('999');expect(JSON.stringify(input)).not.toContain('https://example.com/future');
    expect(input.snapshot).not.toContain('2026-Q2 Revenue: $10 million');
    expect(input.title).toBe('Radon research update · ACME');
  });
  it.each(['2026-09-11T00:00:00Z','2026-09-10T23:30:00-07:00','invalid'])('excludes portfolio snapshots after the UTC analysis date or invalid timestamps: %s',(time)=>{
    const input=buildInvestorArtifactInput({workspace:workspace(),asOf:'2026-09-10',portfolio:portfolio(time)});
    expect(input.snapshot[0]).toContain('No IB portfolio snapshot');
    expect(input.snapshot.join(' ')).not.toContain('Bankroll');
  });
  it('preserves signed financial values and the original snapshot timestamp',()=>{
    const input=buildInvestorArtifactInput({workspace:workspace(),asOf:'2026-09-10',portfolio:portfolio()});
    expect(input.snapshot[0]).toContain('2026-09-10T12:00:00Z');
    expect(input.snapshot[1]).toContain('Bankroll: -20');
    expect(input.snapshot[2]).toContain('recorded market value -1,000.25; entry cost -1,234.5');
  });
  it('keeps null values unavailable and zero distinct from missing',()=>{
    const data=portfolio();data.positions[0].entry_cost=null;data.positions[0].market_value=0;
    const input=buildInvestorArtifactInput({workspace:workspace(),asOf:'2026-09-10',portfolio:data});
    expect(input.snapshot[2]).toContain('recorded market value 0; entry cost unavailable');
  });
  it('labels operator assessments separately and validates the selected date',()=>{
    const input=buildInvestorArtifactInput({workspace:workspace(),asOf:'2026-09-10',notes:'Concentration requires review\n\nFunding terms unverified'});
    expect(input.headwinds).toContain('Operator assessment: Concentration requires review');
    expect(input.headwinds).toContain('Operator assessment: Funding terms unverified');
    expect(() => buildInvestorArtifactInput({workspace:workspace(),asOf:'2026-02-30'})).toThrow(/real YYYY-MM-DD/);
  });
  it('preserves source budget enforcement at the workbook export boundary',async()=>{
    const input=buildInvestorArtifactInput({workspace:workspace(),asOf:'2026-09-10'});
    input.sources[0].passage='a'.repeat(500001);
    await expect(createInvestorWorkbook(input)).rejects.toThrow(/500,000-character budget/);
  });
});

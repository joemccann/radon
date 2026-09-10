"use client";
import {useState} from 'react';
import type {PortfolioData} from '@/lib/types';
import type {ResearchWorkspace,ResearchAnalysis} from '@/lib/researchWorkbench';
import {calculateValuation,valuationErrors,medianMultiple,type ComparableCompany,type ValuationInputs} from '@/lib/researchWorkbench/valuation';
import {downloadInvestorArtifacts,downloadValuation,buildInvestorArtifactInput} from '@/lib/researchWorkbench/artifacts';
const fields: {key:Exclude<keyof ValuationInputs,'ticker'>;label:string;initial:string}[]=[
  {key:'cashFlow',label:'Unlevered free cash flow',initial:''},{key:'growth',label:'Annual growth (decimal)',initial:'0.05'},
  {key:'discountRate',label:'Discount rate (decimal)',initial:'0.1'},{key:'terminalGrowth',label:'Terminal growth (decimal)',initial:'0.02'},
  {key:'years',label:'Forecast years',initial:'5'},{key:'netDebt',label:'Net debt (negative for net cash)',initial:''},
  {key:'shares',label:'Diluted shares',initial:''},{key:'ebitda',label:'EBITDA',initial:''},
  {key:'entryMultiple',label:'Entry EV / EBITDA',initial:'10'},{key:'exitMultiple',label:'Exit EV / EBITDA',initial:'10'},
  {key:'debtFraction',label:'Acquisition debt fraction',initial:'0.5'},{key:'debtPaydown',label:'Annual debt paydown',initial:'0'},
];
const number=(value:number)=>new Intl.NumberFormat('en-US',{maximumFractionDigits:2}).format(value);
export default function ResearchLabs({workspace,portfolio,asOf}:{workspace:ResearchWorkspace;analysis:ResearchAnalysis;portfolio?:PortfolioData|null;asOf?:string}) {
  const [ticker,setTicker]=useState('');
  const [values,setValues]=useState<Record<string,string>>(()=>Object.fromEntries(fields.map(f=>[f.key,f.initial])));
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const [notes,setNotes]=useState('');
  const [peerText,setPeerText]=useState('');
  let peerMedian:number|null=null;let peerError='';let peers:ComparableCompany[]=[];
  if(peerText.trim()){try{peers=peerText.trim().split('\n').map(line=>{const parts=line.split('|').map(v=>v.trim());if(parts.length!==3)throw new Error('Use Company | Enterprise value | EBITDA, one company per line.');return {name:parts[0],enterpriseValue:Number(parts[1]),ebitda:Number(parts[2])};});peerMedian=medianMultiple(peers);}catch(e){peerError=e instanceof Error?e.message:'Invalid comparables.';}}
  const complete=fields.every(f=>values[f.key].trim()!=='')&&ticker.trim()!=='';
  const input={ticker,...Object.fromEntries(fields.map(f=>[f.key,Number(values[f.key])]))} as ValuationInputs;
  const errors=complete?valuationErrors(input):[];
  let result:ReturnType<typeof calculateValuation>|null=null;
  if(complete&&!errors.length){try{result=calculateValuation(input);}catch(e){errors.push(e instanceof Error?e.message:'Invalid scenario.');}}
  async function exportFile(kind:'scenario'|'xlsx'|'pptx') {
    setBusy(true);setError('');
    try {
      const artifact=buildInvestorArtifactInput({workspace,asOf:asOf??new Date().toISOString().slice(0,10),ticker,notes,portfolio});
      if(kind==='scenario')await downloadValuation(input,artifact.sources,peers);else await downloadInvestorArtifacts(artifact,kind);
    }catch(e){setError(e instanceof Error?e.message:'Export failed. Retry the download.');}finally{setBusy(false);}
  }
  return <div className="research-labs">
    <section aria-labelledby="valuation-heading">
      <h2 id="valuation-heading">Valuation scenarios</h2>
      <p className="research-muted">Enter a company and consistent units: if cash flow is in millions, enter debt, EBITDA and shares in millions. Rates use decimals: 0.10 means 10%. Defaults are editable assumptions.</p>
      <label className="research-field">Ticker or company<input value={ticker} onChange={e=>setTicker(e.target.value)} maxLength={100}/></label>
      <div className="research-form-grid">{fields.map(f=><label key={f.key} className="research-field">{f.label}<input type="number" step="any" value={values[f.key]} onChange={e=>setValues(v=>({...v,[f.key]:e.target.value}))}/></label>)}</div>
      {errors.length?<div role="alert">{errors.join(' ')}</div>:null}
      {!complete?<p className="research-muted">Enter all financial inputs to calculate a scenario.</p>:null}
      {result?<dl className="research-valuation-results">
        <div><dt>DCF enterprise value</dt><dd>{number(result.enterpriseValue)}</dd></div>
        <div><dt>DCF equity value</dt><dd>{number(result.equityValue)}</dd></div>
        <div><dt>DCF per share</dt><dd>{number(result.perShare)}</dd></div>
        <div><dt>Sponsor MOIC</dt><dd>{number(result.moic)}×</dd></div>
        <div><dt>Sponsor annualized return</dt><dd>{number(result.irr*100)}%</dd></div>
        <div><dt>Multiple-based equity value</dt><dd>{number(result.compsEquityValue)}</dd></div>
      </dl>:null}
      <h3 style={{marginTop:24}}>Comparable companies</h3>
      <label className="research-field">Operator-supplied peers: Company | Enterprise value | EBITDA<textarea value={peerText} onChange={e=>setPeerText(e.target.value)} rows={4} maxLength={6000}/></label>
      <p className="research-muted">Use consistent units and comparable fiscal periods. Peer selection and values are operator assumptions; source passages remain in the workbook.</p>
      {peerError?<p role="alert">{peerError}</p>:null}
      {peerMedian!==null?<p>Median EV / EBITDA: {number(peerMedian)}× <button type="button" className="btn-secondary" onClick={()=>setValues(v=>({...v,exitMultiple:String(peerMedian)}))}>Use peer median as exit multiple</button></p>:null}
      <details><summary>Model assumptions and limits</summary><p>DCF uses annual unlevered cash flow and a perpetual terminal value. The simplified LBO assumes a debt-free acquisition, explicit annual debt repayment and no interim distributions, fees, interest schedule or tax shield. One growth assumption applies to cash flow and EBITDA. The multiple view uses the entered exit multiple; it does not claim a retrieved peer set.</p></details>
      <button type="button" className="btn-secondary" disabled={!result||Boolean(peerError)||busy} onClick={()=>void exportFile('scenario')}>Download scenario XLSX</button>
    </section>
    <section aria-labelledby="artifact-heading" style={{marginTop:32}}>
      <h2 id="artifact-heading">Investor update</h2>
      <p className="research-muted">Snapshot includes the available IB portfolio snapshot with its recorded time. What’s moving, Pipeline and Headwinds use this workspace’s cited research. Review the source passages before sharing.</p>
      <label className="research-field">Headwinds and operator assessment<textarea value={notes} onChange={e=>setNotes(e.target.value)} rows={4} maxLength={12000}/></label>
      <div className="research-actions"><button type="button" className="btn-secondary" disabled={!workspace.facts.length||busy} onClick={()=>void exportFile('xlsx')}>Download update XLSX</button><button type="button" className="btn-secondary" disabled={!workspace.facts.length||busy} onClick={()=>void exportFile('pptx')}>Download update PPTX</button></div>
    </section>
    {busy?<p role="status">Preparing artifact…</p>:null}{error?<p role="alert">{error}</p>:null}
  </div>;
}

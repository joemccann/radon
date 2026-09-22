import type { Workbook, Worksheet } from 'exceljs';
import type { PortfolioData } from '../types';
import { analyzeResearch, type ResearchWorkspace } from './index';
import { calculateValuation, comparableMultiples, type ComparableCompany, type ValuationInputs } from './valuation';

export interface InvestorArtifactInput {
  title: string; asOf: string; snapshot: string[]; whatsMoving: string[];
  pipeline: string[]; headwinds: string[];
  sources: {title: string; url: string; passage?: string}[];
}
/** Rebuild analysis at the export boundary so stale UI projections cannot leak future evidence. */
export function buildInvestorArtifactInput({workspace,asOf,ticker='',notes='',portfolio}:{
  workspace:ResearchWorkspace;asOf:string;ticker?:string;notes?:string;portfolio?:PortfolioData|null;
}):InvestorArtifactInput {
  const analysis=analyzeResearch(workspace,asOf);
  const syncedAt=portfolio?Date.parse(portfolio.last_sync):NaN;
  const eligiblePortfolio=portfolio&&Number.isFinite(syncedAt)&&new Date(syncedAt).toISOString().slice(0,10)<=asOf?portfolio:null;
  const number=(value:number)=>new Intl.NumberFormat('en-US',{maximumFractionDigits:2}).format(value);
  const sources=workspace.documents.filter(d=>d.publishedAt<=asOf).map(d=>({
    title:`${d.title} · ${d.publishedAt}${d.url?'':' · operator-supplied text'}`,url:d.url??'',
    passage:workspace.facts.filter(f=>f.documentId===d.id).map(f=>f.quote).join('\n'),
  }));
  return {title:`Radon research update${ticker?' · '+ticker:''}`,asOf,
    snapshot:[...(eligiblePortfolio ? [
      `IB portfolio snapshot as of ${eligiblePortfolio.last_sync}; refresh in Portfolio before external distribution.`,
      `Positions: ${eligiblePortfolio.position_count}. Bankroll: ${number(eligiblePortfolio.bankroll)}.`,
      ...eligiblePortfolio.positions.map(p=>`${p.ticker} · ${p.structure} · ${p.direction} ${p.contracts}; recorded market value ${p.market_value===null?'unavailable':number(p.market_value)}; entry cost ${p.entry_cost===null?'unavailable':number(p.entry_cost)}.`),
    ] : ['No IB portfolio snapshot at or before the analysis date is available in this workspace.']),...analysis.briefs.map(b=>b.text)],
    whatsMoving:analysis.themes.map(t=>`${t.theme}: ${t.interpretation}`),
    pipeline:analysis.deals.map(d=>`${d.company}: ${d.diligence.join(' ')}`),
    headwinds:[...analysis.warnings,...notes.split('\n').filter(Boolean).map(n=>`Operator assessment: ${n}`)],sources,
  };
}
function validateArtifactSize(input: InvestorArtifactInput) {
  const lines=[input.title,input.asOf,...input.snapshot,...input.whatsMoving,...input.pipeline,...input.headwinds,...input.sources.flatMap(s=>[s.title,s.url,s.passage??''])];
  if(input.title.length>500||lines.length>10000||lines.reduce((sum,line)=>sum+line.length,0)>500000)throw new Error('Export exceeds the 500,000-character budget. Export a smaller reviewed research selection.');
}
// Export template colors are defined here; exported artifacts do not inherit browser theme.
const TEMPLATE = { ink: '172624', green: '087F53', muted: '62716D', paper: 'FFFFFF' };
const sections = (input: InvestorArtifactInput): [string, string[]][] => [
  ['Snapshot', input.snapshot], ["What’s moving", input.whatsMoving],
  ['Pipeline', input.pipeline], ['Headwinds', input.headwinds],
];
function sheetStyle(sheet: Worksheet) {
  sheet.columns = [{width: 36}, {width: 96}];
  sheet.views = [{state:'frozen', ySplit:1}];
  sheet.eachRow(row => { row.alignment = {vertical:'top',wrapText:true}; row.font={name:'Inter',size:11,color:{argb:TEMPLATE.ink}}; });
  sheet.getRow(1).font={name:'Inter',size:14,bold:true,color:{argb:TEMPLATE.paper}};
  sheet.getRow(1).fill={type:'pattern',pattern:'solid',fgColor:{argb:TEMPLATE.green}};
  sheet.getRow(1).height=28;
}
async function workbook(): Promise<Workbook> {
  const ExcelJS = await import('exceljs');
  const book = new ExcelJS.default.Workbook();
  book.creator='Radon'; book.calcProperties.fullCalcOnLoad=true;
  return book;
}
function safeFileName(title:string) { return title.replace(/[^a-z0-9_-]+/gi,'-').slice(0,80) || 'radon-research'; }
export function saveArtifact(blob: Blob, name: string) {
  const url=URL.createObjectURL(blob); const anchor=document.createElement('a');
  anchor.href=url; anchor.download=name; document.body.appendChild(anchor); anchor.click(); anchor.remove();
  setTimeout(()=>URL.revokeObjectURL(url), 1000);
}
export async function createInvestorWorkbook(input: InvestorArtifactInput) {
  validateArtifactSize(input);
  const book=await workbook();
  for (const [title, lines] of sections(input)) {
    const sheet=book.addWorksheet(title); sheet.addRow([input.title,title]); sheet.addRow(['As of',input.asOf]);
    for (const line of lines.length ? lines : ['No sourced observations supplied.']) sheet.addRow(['Observation',line]);
    sheetStyle(sheet);
  }
  const sources=book.addWorksheet('Sources'); sources.addRow(['Source','URL','Passage']);
  for (const source of input.sources) sources.addRow([source.title,source.url,source.passage ?? '']);
  sheetStyle(sources); sources.getColumn(3).width=96;
  return book;
}
export async function createValuationWorkbook(input: ValuationInputs, sources: InvestorArtifactInput['sources']=[], peers:ComparableCompany[]=[]) {
  validateArtifactSize({title:input.ticker,asOf:'',snapshot:[],whatsMoving:[],pipeline:[],headwinds:[],sources});
  const result=calculateValuation(input); const multiples=comparableMultiples(peers); const book=await workbook();
  const sheet=book.addWorksheet('Scenario');
  sheet.addRow(['Radon valuation scenario',input.ticker]);
  const rows: [string,number|string][] = [
    ['Base unlevered free cash flow',input.cashFlow], ['Annual growth',input.growth], ['Discount rate',input.discountRate],
    ['Terminal growth',input.terminalGrowth],['Years',input.years],['Net debt',input.netDebt],['Diluted shares',input.shares],
    ['EBITDA',input.ebitda],['Entry EV / EBITDA',input.entryMultiple],['Exit EV / EBITDA',input.exitMultiple],
    ['Acquisition debt fraction',input.debtFraction],['Annual debt paydown',input.debtPaydown],
  ];
  rows.forEach(row=>sheet.addRow(row));
  sheet.addRow(['Assumptions','Operator-entered scenario; money and shares must use consistent units.']);
  sheet.addRow(['LBO method','Debt-free acquisition basis; annual paydown is an explicit assumption. No fees, tax shields, interest schedule or interim distributions. Growth applies to both EBITDA and cash flow.']);
  sheet.addRow(['Year','Free cash flow','Present value']);
  for (let i=0;i<input.years;i++) {
    const row=17+i;
    sheet.addRow([i+1,{formula:`$B$2*(1+$B$3)^A${row}`,result:result.cashFlows[i]}, {formula:`B${row}/(1+$B$4)^A${row}`,result:result.presentValues[i]}]);
  }
  const terminalRow=17+input.years;
  sheet.addRow(['Terminal value',{formula:`B${terminalRow-1}*(1+$B$5)/($B$4-$B$5)`,result:result.terminalValue}]);
  sheet.addRow(['DCF enterprise value',{formula:`SUM(C17:C${terminalRow-1})+B${terminalRow}/(1+$B$4)^$B$6`,result:result.enterpriseValue}]);
  sheet.addRow(['DCF equity value',{formula:`B${terminalRow+1}-$B$7`,result:result.equityValue}]);
  sheet.addRow(['DCF per share',{formula:`B${terminalRow+2}/$B$8`,result:result.perShare}]);
  sheet.addRow(['Entry enterprise value',{formula:'B9*B10',result:result.entryEnterpriseValue}]);
  sheet.addRow(['Sponsor equity',{formula:`B${terminalRow+4}*(1-B12)`,result:result.sponsorEquity}]);
  sheet.addRow(['Exit debt',{formula:`MAX(0,B${terminalRow+4}*B12-B13*B6)`,result:result.exitDebt}]);
  sheet.addRow(['Exit equity',{formula:`MAX(0,B9*(1+B3)^B6*B11-B${terminalRow+6})`,result:result.exitEquity}]);
  sheet.addRow(['Sponsor MOIC',{formula:`B${terminalRow+7}/B${terminalRow+5}`,result:result.moic}]);
  sheet.addRow(['Sponsor annualized return',{formula:`B${terminalRow+8}^(1/B6)-1`,result:result.irr}]);
  sheet.addRow(['Multiple-based enterprise value',{formula:'B9*B11',result:result.compsEnterpriseValue}]);
  sheet.addRow(['Multiple-based equity value',{formula:`B${terminalRow+10}-B7`,result:result.compsEquityValue}]);
  sheetStyle(sheet); sheet.getColumn(3).width=24;
  for (const row of [3,4,5,12,terminalRow+9]) sheet.getCell(`B${row}`).numFmt='0.00%';
  const sourceSheet=book.addWorksheet('Sources'); sourceSheet.addRow(['Source','URL','Passage']);
  sources.forEach(source=>sourceSheet.addRow([source.title,source.url,source.passage??''])); sheetStyle(sourceSheet);sourceSheet.getColumn(3).width=96;
  if(peers.length){const peerSheet=book.addWorksheet('Operator comparables');peerSheet.addRow(['Company','Enterprise value','EBITDA','EV / EBITDA']);peers.forEach((peer,i)=>peerSheet.addRow([peer.name,peer.enterpriseValue,peer.ebitda,{formula:`B${i+2}/C${i+2}`,result:multiples[i].multiple}]));sheetStyle(peerSheet);peerSheet.getColumn(2).width=24;peerSheet.getColumn(3).width=24;peerSheet.getColumn(4).width=24;}
  return book;
}
export async function downloadValuation(input:ValuationInputs,sources:InvestorArtifactInput['sources']=[],peers:ComparableCompany[]=[]) {
  const book=await createValuationWorkbook(input,sources,peers); const data=await book.xlsx.writeBuffer();
  saveArtifact(new Blob([new Uint8Array(data)],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}),`${safeFileName(input.ticker)}-scenario.xlsx`);
}
export async function downloadInvestorArtifacts(input:InvestorArtifactInput,format:'xlsx'|'pptx') {
  validateArtifactSize(input);
  if(format==='xlsx') {
    const book=await createInvestorWorkbook(input);const data=await book.xlsx.writeBuffer();
    saveArtifact(new Blob([new Uint8Array(data)],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}),`${safeFileName(input.title)}.xlsx`);return;
  }
  const {default:PptxGenJS}=await import('pptxgenjs');const deck=new PptxGenJS();deck.layout='LAYOUT_WIDE';deck.author='Radon';deck.subject='Sourced investor update';deck.title=input.title;
  // Bound lines per slide and split long observations rather than shrinking to unreadable text.
  const chunks=(lines:string[])=>lines.flatMap(line=>line.match(/[\s\S]{1,160}/g)??['']);
  for(const [title,lines] of [...sections(input),['Sources',input.sources.map(s=>`${s.title}\n${s.url}\n${s.passage??''}`)] as [string,string[]]]) {
    const all=chunks(lines.length?lines:['No sourced observations supplied.']);
    for(let offset=0;offset<all.length;offset+=5) {
      const slide=deck.addSlide();slide.background={color:TEMPLATE.paper};
      slide.addText(title+(offset?' (continued)':''),{x:.6,y:.45,w:12.1,h:.6,fontFace:'Inter',fontSize:28,bold:true,color:TEMPLATE.ink,breakLine:false});
      slide.addText(all.slice(offset,offset+5).join('\n\n'),{x:.6,y:1.45,w:12.1,h:4.8,fontFace:'Inter',fontSize:18,color:TEMPLATE.ink,breakLine:false,valign:'top',margin:0});
      slide.addText(`${input.title.slice(0,100)} | ${input.asOf}`,{x:.6,y:6.8,w:12.1,h:.3,fontFace:'Inter',fontSize:11,color:TEMPLATE.muted});
    }
  }
  await deck.writeFile({fileName:`${safeFileName(input.title)}.pptx`});
}

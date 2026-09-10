"use client";
import { useEffect,useState } from 'react';
import { clearResearchChecklist,readResearchChecklist,type ResearchTicketChecklist } from '@/lib/researchWorkbench/handoff';
export default function ResearchTicketContext({ticker}:{ticker:string}) {
  const [checklist,setChecklist]=useState<ResearchTicketChecklist|null>(null);
  useEffect(()=>{
    const current=readResearchChecklist(ticker); setChecklist(current);
    if(!current)return;
    const remaining=Math.max(0,Date.parse(current.createdAt)+86_400_000-Date.now());
    const timer=setTimeout(()=>{clearResearchChecklist(ticker);setChecklist(null);},remaining);
    return ()=>clearTimeout(timer);
  },[ticker]);
  if(!checklist||checklist.ticker!==ticker)return null;
  return <aside aria-label="Research checklist" style={{padding:16,marginBottom:16,border:'1px solid var(--line-grid)',borderRadius:8}}>
    <h3 style={{margin:0,fontSize:16}}>Research checklist for {ticker}</h3>
    <p style={{fontSize:12,color:'var(--text-muted)'}}>Prepared {new Date(checklist.createdAt).toLocaleString()}. Research context expires after 24 hours. Review current prices, coverage and the order risk summary.</p>
    <ul>{checklist.items.map((item,index)=><li key={index} style={{marginBottom:8,overflowWrap:'anywhere'}}>{item}</li>)}</ul>
    <details><summary>Source passages ({checklist.sources.length})</summary>{checklist.sources.map((s,i)=><div key={i} style={{marginTop:12,overflowWrap:'anywhere'}}><span>{s.url?<a href={s.url} target="_blank" rel="noopener noreferrer">{s.title}</a>:`${s.title} (operator-supplied text)`}</span>{s.passage?<blockquote>{s.passage}</blockquote>:null}</div>)}</details>
    <button className="btn-secondary" style={{marginTop:12,minHeight:44}} onClick={()=>{clearResearchChecklist(ticker);setChecklist(null);}}>Dismiss checklist</button>
  </aside>;
}

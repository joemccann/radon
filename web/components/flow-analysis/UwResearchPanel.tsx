"use client";
import {useState} from "react";
import ErrorToast from "@/components/ErrorToast";
import {useUwResearch} from "@/lib/UwResearchContext";
import {researchRows,researchGroups,SECTION_LABELS} from "@/lib/uwResearch";
import styles from "./UwResearchPanel.module.css";
const FIELDS: Record<string,string>={ticker:"Ticker",symbol:"Symbol",strategy:"Strategy",strategy_type:"Strategy",net_premium:"Net premium",net_price:"Net price",net_delta:"Net delta",max_profit:"Reported max profit",max_loss:"Reported max loss",fit_score:"Fit score",sntm_score:"Daily sentiment",intvl_sntm_score:"Interval sentiment",call_txn:"Opening call buys",put_txn:"Opening put buys",name:"Name",company_name:"Company",sector:"Sector",industry:"Industry",market_cap:"Market cap",pe_ratio:"P/E",iv_rank:"IV rank",volatility:"Volatility",iv:"IV",date:"Observation date",timestamp:"Observation time",quarter:"Quarter",month:"Month",avg_return:"Mean return",win_rate:"Win rate",net_call_premium:"Net call premium",net_put_premium:"Net put premium",call_vol:"Call volume",put_vol:"Put volume",delta_flow:"Delta flow",vega_flow:"Vega flow",gamma:"Gamma",charm:"Charm",vanna:"Vanna",strike:"Strike",expiry:"Expiry",price:"Price",size:"Size",premium:"Premium",amount:"Amount",cash_amount:"Cash amount",ex_date:"Ex date",split_ratio:"Split ratio",dividend:"Dividend"};
function display(value:unknown):string|null {
  if(value===null || value===undefined) return null;
  if(typeof value==="number")return Number.isFinite(value)?value.toLocaleString(undefined,{maximumFractionDigits:4}):null;
  if(typeof value==="string")return value.length<180?value:null;
  if(typeof value==="boolean")return value?"Yes":"No";
  return null;
}
const LEG_STATES:Record<string,string>={ok:"Available",empty:"No matching legs",unavailable:"Unavailable",not_requested:"Not requested"};
function Observation({row}:{row:Record<string,unknown>}) {
  const sourceRows=researchRows(row);
  if(sourceRows[0]!==row) return <>{sourceRows.slice(0,3).map((item,i)=><Observation key={i} row={item}/>)}</>;
  const entries=Object.entries(row).filter(([k,v])=>FIELDS[k]&&display(v)!==null).slice(0,8);
  const legsState=typeof row.legs_state==="string"?(LEG_STATES[row.legs_state]??"Unconfirmed"):null;
  return <>{legsState&&<p className={styles.note}>Leg details: {legsState}</p>}{entries.length?<dl className={styles.metrics}>{entries.map(([k,v])=><div key={k}><dt>{FIELDS[k]}</dt><dd>{display(v)}</dd></div>)}</dl>:<p className={styles.note}>Observation retained. No comparable summary fields are available.</p>}</>;
}
function SampleCoverage({data}:{data:unknown}) {
  if(!data || typeof data!=="object" || Array.isArray(data))return null;
  const coverage=(data as Record<string,unknown>).coverage;
  if(!coverage || typeof coverage!=="object" || Array.isArray(coverage))return null;
  const c=coverage as Record<string,unknown>;
  return <p className={styles.note}>
    {c.truncated===true?"Partial source sample: collection bound reached":c.complete===true?"Complete requested source sample":"Source sample completeness unconfirmed"}
    {typeof c.pages==="number"?` · Pages collected: ${c.pages}`:""}
    {typeof c.page_limit==="number"?` · Page limit: ${c.page_limit}`:""}
    {typeof c.range_start==="string"?` · Range start: ${c.range_start}`:""}
    {typeof c.range_end==="string"?` · Range end: ${c.range_end}`:""}
  </p>;
}
function StreamObservation({row}:{row:Record<string,unknown>}) {
  return <div>
    <p className={styles.note}>
      {row.complete===true?"Complete source observation":row.complete===false?"Partial source observation":"Completeness unconfirmed"}
      {typeof row.basis==="string"?` · Basis: ${row.basis}`:""}
      {typeof row.units==="string"?` · Units: ${row.units}`:""}
      {typeof row.event_time==="string"?` · Source time: ${row.event_time}`:" · Source time unavailable"}
      {typeof row.ingested_at==="string"?` · Received: ${row.ingested_at}`:""}
    </p>
    <Observation row={row}/>
  </div>;
}
export default function UwResearchPanel({ticker}:{ticker:string}) {
  const {data,connected,refresh}=useUwResearch(ticker);
  const [busy,setBusy]=useState(false);const [error,setError]=useState<string|null>(null);
  if(process.env.NEXT_PUBLIC_RADON_DEMO==="1")return null;
  const sections=data?.research.sections??{};
  const channels=Object.entries(data?.stream.channels??{});
  async function update(){setBusy(true);setError(null);try{await refresh()}catch{setError("Research refresh could not complete. Previous observations remain available.")}finally{setBusy(false)}}
  return <section className={`section ${styles.panel}`} data-testid="uw-research-panel" aria-label="Advanced flow research">
    <header className={styles.header}><div><h2>Advanced flow research</h2><p>Unusual Whales · {ticker} · Research observations</p></div><button type="button" onClick={()=>void update()} disabled={busy||data?.refresh_pending}>{busy||data?.refresh_pending?"Refreshing":"Refresh research"}</button></header>
    {data?.refresh_failed&&!error&&<ErrorToast message="Research refresh could not complete. Previous observations remain available." onRetry={()=>void update()}/>}
    {error&&<ErrorToast message={error} onRetry={()=>void update()}/>}
    <div className={styles.state} role="status"><strong>{data?.stream.status??"No observations"}</strong><span>{connected?"Application feed connected":"Application feed reconnecting"}</span><span>Execution quotes: IB</span><span>OI confirmation: daily</span></div>
    {!data&&<p className={styles.note}>No retained research observations have been collected for this ticker.</p>}
    {data?.stream.connection?.gaps ? <p className={styles.note}>Stream gaps recorded: {data.stream.connection.gaps}. A retained observation does not establish complete coverage.</p>:null}
    {channels.length>0&&<details><summary>Feed coverage and freshness</summary><p className={styles.note}>Channel timestamps describe the shared feed across its tickers. Each retained observation shows its own source time below.</p><ul className={styles.channels}>{channels.map(([name,c])=><li key={name}><strong>{name.replaceAll("_"," ")}</strong><span>{c.status??"Unknown"}{c.gap_count?` · ${c.gap_count} gaps`:""}</span><span>{c.basis??""}{c.units?` · ${c.units}`:""}</span><time>{c.last_event_at??"No event timestamp"}</time></li>)}</ul></details>}
    {Object.entries(data?.stream.latest??{}).map(([channel,rows])=><details key={channel}><summary>{channel.replaceAll("_"," ")} · Retained stream observations</summary>{Object.entries(rows).slice(-3).map(([key,row])=><StreamObservation key={key} row={row}/> )}</details>)}
    {Object.entries(sections).map(([name,s])=><details key={name}><summary>{SECTION_LABELS[name]??"Research context"}<span>{s.state}</span></summary><p className={styles.note}>{s.as_of?`Source observation: ${s.as_of}`:"Source observation time unavailable"}{s.observed_at?` · Received ${s.observed_at}`:""}</p><SampleCoverage data={s.data}/>{researchGroups(s.data).map(group=><div key={group.label}>{group.label&&<h3>{group.label}</h3>}{group.rows.slice(0,3).map((row,i)=><Observation row={row} key={i}/>)}{group.label&&!group.rows.length&&<p className={styles.note}>No matching observations in this dataset.</p>}</div>)}{s.state==="empty"&&<p className={styles.note}>No matching observations in this source sample.</p>}{s.state==="unavailable"&&<p className={styles.note}>This dataset is not available for the configured request or entitlement.</p>}</details>)}
    <p className={styles.note}>Observed strategies are market prints. Sentiment and vendor fit scores do not establish trade direction, win probability or an executable quote.</p>
  </section>;
}

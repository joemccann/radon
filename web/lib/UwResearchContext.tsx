"use client";
import {createContext,useCallback,useContext,useEffect,useRef,useState,type ReactNode} from "react";
import {isResearchSnapshot,type ResearchSnapshot} from "@/lib/uwResearch";
import {getReturnCacheGeneration} from "@/lib/returnCache";
const Context=createContext<{data:ResearchSnapshot|null;connected:boolean;select:(ticker:string)=>void;refresh:(sections?:string[])=>Promise<void>}>({data:null,connected:false,select:()=>{},refresh:async()=>{}});
/** One authenticated application SSE owner in Providers; never a vendor socket. */
export function UwResearchProvider({children}:{children:ReactNode}) {
  const [ticker,setTicker]=useState<string|null>(null);
  const [data,setData]=useState<ResearchSnapshot|null>(null);
  const [connected,setConnected]=useState(false);
  const generation=useRef(getReturnCacheGeneration());
  const select=useCallback((symbol:string)=>{if(/^[A-Z][A-Z0-9.\-]{0,9}$/.test(symbol))setTicker(symbol)},[]);
  useEffect(()=>{
    if(!ticker || process.env.NEXT_PUBLIC_RADON_DEMO==="1")return;
    const current=getReturnCacheGeneration();generation.current=current;
    let live=true;
    setData(previous=>previous?.ticker===ticker?previous:null);
    const accept=(value:unknown)=>{if(live&&getReturnCacheGeneration()===current&&isResearchSnapshot(value,ticker))setData(value)};
    const controller=new AbortController();
    void fetch(`/api/uw/research?ticker=${encodeURIComponent(ticker)}`,{cache:"no-store",signal:controller.signal}).then(r=>r.ok?r.json():null).then(accept).catch(()=>{});
    const source=new EventSource(`/api/uw/research/events?ticker=${encodeURIComponent(ticker)}`);
    const receive=(event:MessageEvent)=>{try{accept(JSON.parse(event.data))}catch{setConnected(false)}};
    source.addEventListener("research",receive);
    source.addEventListener("degraded",()=>setConnected(false));
    source.onopen=()=>{if(getReturnCacheGeneration()===current)setConnected(true);else source.close()};
    source.onerror=()=>setConnected(false);
    return()=>{live=false;controller.abort();source.close();setConnected(false)};
  },[ticker]);
  const refresh=useCallback(async(sections?:string[])=>{
    if(!ticker) return;
    const current=getReturnCacheGeneration();
    const res=await fetch("/api/uw/research",{method:"POST",cache:"no-store",headers:{"Content-Type":"application/json"},body:JSON.stringify({ticker,...(sections?{sections}:{})}),signal:AbortSignal.timeout(130_000)});
    if(!res.ok)throw new Error("Research refresh could not complete. Previous observations remain available.");
    const value:unknown=await res.json();if(getReturnCacheGeneration()===current&&isResearchSnapshot(value,ticker))setData(value);
  },[ticker]);
  return <Context.Provider value={{data,connected,select,refresh}}>{children}</Context.Provider>;
}
export function useUwResearch(ticker:string){
  const value=useContext(Context);
  useEffect(()=>value.select(ticker),[ticker,value.select]);
  return {...value,data:value.data?.ticker===ticker?value.data:null};
}

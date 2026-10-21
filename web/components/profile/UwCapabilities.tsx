"use client";
import {useState} from "react";
import ErrorToast from "@/components/ErrorToast";
type State={stream_status:string;governor:{requests_per_minute?:number;effective_limit?:number|null;limits_verified?:boolean;quota_period?:string};channels:Record<string,{acknowledged?:boolean;status?:string}>};
export default function UwCapabilities(){
 const [data,setData]=useState<State|null>(null);const [error,setError]=useState(false);const [busy,setBusy]=useState(false);
 async function read(){setBusy(true);setError(false);try{const res=await fetch("/api/uw/capabilities",{cache:"no-store",signal:AbortSignal.timeout(12_000)});if(!res.ok)throw new Error();const value=await res.json() as State;if(!value.governor)throw new Error();setData(value)}catch{setError(true)}finally{setBusy(false)}}
 return <div className="preferences-row__description"><button type="button" className="admin-btn admin-btn-ghost" disabled={busy} onClick={()=>void read()}>{busy?"Reading capabilities":"Read research capabilities"}</button>{error&&<ErrorToast message="Research capabilities could not be loaded. Try again." onRetry={()=>void read()}/>} {data&&<><p>Stream: {data.stream_status}. Limits: {data.governor.limits_verified?"Account verified":"Conservative defaults; account limits unverified"}.</p><p>{data.governor.requests_per_minute??"Unknown"} requests per minute · {data.governor.effective_limit===null?"Unlimited allowance":data.governor.effective_limit??"Unknown allowance"} · Period: {data.governor.quota_period??"unknown"}.</p><p>{Object.values(data.channels??{}).filter(c=>c.acknowledged).length} acknowledged channels. REST key validity does not establish stream coverage.</p></>}</div>;
}

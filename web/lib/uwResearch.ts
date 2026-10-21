export type ResearchSection = { state: string; data: unknown; source?: string; as_of?: string | null; observed_at?: string; expires_at?: string; error_code?: string };
export type ResearchSnapshot = {
  schema_version: number; ticker: string; source: string; refresh_pending?: boolean; refresh_failed?: boolean;
  execution_source: string; oi_cadence: string;
  stream: {status: string; updated_at?: string; connection?: {connected?: boolean; gaps?: number; reconnects?: number}; channels?: Record<string,{status?:string;last_event_at?:string;basis?:string;units?:string;gap_count?:number}>; latest?:Record<string,Record<string,Record<string,unknown>>>};
  research: {status:string; generated_at?:string; sections:Record<string,ResearchSection>; limitations?:string[]};
  governor?: {requests_per_minute?:number;effective_limit?:number|null;limits_verified?:boolean;quota_period?:string};
};
export const SECTION_LABELS: Record<string,string> = {multi_leg:"Observed option strategies",pulse:"Nasdaq Options Pulse",pulse_market:"Market Options Pulse",pulse_sectors:"Sector sentiment",pulse_scanner:"Sentiment candidates",volatility:"Volatility context",volatility_anomaly:"Volatility anomalies",volatility_character:"Volatility character",stance:"Contract fit",company:"Company profile",seasonality:"Seasonality comparison",estimates:"Forward estimates",corporate_actions:"Corporate actions",transcript:"Earnings transcript",analytics:"Window statistics",macro:"Macro context",futures:"CME futures context"};
export function isResearchSnapshot(value: unknown, ticker: string): value is ResearchSnapshot {
  if (!value || typeof value !== "object") return false;
  const v = value as Partial<ResearchSnapshot>;
  return v.ticker === ticker && v.schema_version === 1 && !!v.stream && !!v.research && typeof v.research.sections === "object" && v.research.sections !== null;
}
export function researchRows(data:unknown):Record<string,unknown>[] {
  if(Array.isArray(data)) return data.filter(x=>x&&typeof x==="object"&&!Array.isArray(x)) as Record<string,unknown>[];
  if(!data || typeof data!=="object") return [];
  const obj=data as Record<string,unknown>;
  if(obj.payload && typeof obj.payload==="object") return researchRows(obj.payload);
  if(Array.isArray(obj.trades)) return researchRows(obj.trades);
  if(obj.snapshot && typeof obj.snapshot==="object") return researchRows(obj.snapshot);
  if(obj.latest && typeof obj.latest==="object") return researchRows(obj.latest);
  for(const key of ["monthly","year_month","stats","blocks","fixed","sliding"]){
    if(obj[key]&&typeof obj[key]==="object")return researchRows(obj[key]);
  }
  if(Array.isArray(obj.rows)) return researchRows(obj.rows);
  if(Array.isArray(obj.data)) return researchRows(obj.data);
  if(obj.data && typeof obj.data==="object") return researchRows(obj.data);
  return [obj];
}

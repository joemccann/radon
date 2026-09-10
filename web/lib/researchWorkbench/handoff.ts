export interface ResearchTicketChecklist {
  version: 1; ticker: string; createdAt: string; items: string[];
  sources: {title:string;url:string;passage?:string}[];
}
const key=(ticker:string)=>`radon:research-ticket:${ticker}`;
function validTicker(ticker:string) { return /^[A-Z][A-Z0-9.-]{0,14}$/.test(ticker); }
export function parseTicketChecklist(raw:unknown,ticker:string,now=Date.now()):ResearchTicketChecklist|null {
  if(!raw || typeof raw!=='object') return null;
  const v=raw as Partial<ResearchTicketChecklist>;
  if(v.version!==1 || v.ticker!==ticker || !validTicker(ticker) || typeof v.createdAt!=='string') return null;
  const age=now-Date.parse(v.createdAt); if(!Number.isFinite(age)||age<0||age>86_400_000) return null;
  if(!Array.isArray(v.items)||!v.items.length||v.items.length>30||v.items.some(s=>typeof s!=='string'||!s.trim()||s.length>4000)) return null;
  if(!Array.isArray(v.sources)||v.sources.length>100||v.sources.some(s=>{
    if(!s||typeof s.title!=='string'||typeof s.url!=='string'||s.title.length>500||(s.passage!==undefined&&(typeof s.passage!=='string'||s.passage.length>20_000)))return true;
    if(s.url===''||/^\/api\/newsfeed\/research\/files\/[a-f0-9]{64}\.(?:pdf|json)(?:#page=[1-9]\d*)?$/.test(s.url))return false;
    try {const url=new URL(s.url);return url.protocol!=='https:'||Boolean(url.username||url.password);}catch{return true;}
  }))return null;
  return v as ResearchTicketChecklist;
}
export function applyResearchChecklist(ticker:string,items:string[],sources:ResearchTicketChecklist['sources']):string {
  const normalized=ticker.toUpperCase();
  const checklist:ResearchTicketChecklist={version:1,ticker:normalized,createdAt:new Date().toISOString(),items,sources};
  if(!parseTicketChecklist(checklist,normalized))throw new Error('Checklist requires a valid ticker, bounded observations and safe source references.');
  try {sessionStorage.setItem(key(normalized),JSON.stringify(checklist));}catch{throw new Error('Browser session storage is unavailable. Export the brief before opening the ticket.');}
  return `/${encodeURIComponent(normalized)}?tab=order&src=research`;
}
export function readResearchChecklist(ticker:string):ResearchTicketChecklist|null {
  try {return parseTicketChecklist(JSON.parse(sessionStorage.getItem(key(ticker))??'null'),ticker);}catch{return null;}
}
export function clearResearchChecklist(ticker:string) {try{sessionStorage.removeItem(key(ticker));}catch{/* Session may already be unavailable. */}}

import {test,expect} from "@playwright/test";
const now=new Date().toISOString();
const snapshot={schema_version:1,ticker:"AAPL",source:"unusual_whales",execution_source:"ib",oi_cadence:"daily",stream:{status:"degraded",updated_at:now,connection:{connected:true,gaps:2},channels:{"gex:AAPL":{acknowledged:true,status:"live",basis:"Open interest",units:"Dollars per 1% move",last_event_at:now}},latest:{}},research:{status:"partial",generated_at:now,sections:{multi_leg:{state:"stale",as_of:now,observed_at:now,data:{trades:[{strategy:"call_vertical",net_premium:"-2400.50",max_loss:"1500"}],coverage:{truncated:false}}},pulse:{state:"ok",as_of:now,data:{data:{sntm_score:65,intvl_sntm_score:48}}}}}};
for(const width of [1440,393])test(`UW research retained observations and refresh at ${width}px`,async({page},info)=>{
 await page.setViewportSize({width,height:1100});
 await page.addInitScript(payload=>{
  class Source {static CONNECTING=0;static OPEN=1;static CLOSED=2;readyState=0;onopen:(()=>void)|null=null;onerror:(()=>void)|null=null;listeners:Record<string,((e:{data:string})=>void)[]>={};constructor(){setTimeout(()=>{this.readyState=1;this.onopen?.();for(const cb of this.listeners.research??[])cb({data:JSON.stringify(payload)})},50)}addEventListener(name:string,fn:(e:{data:string})=>void){(this.listeners[name]??=[]).push(fn)}close(){this.readyState=2}}
  Object.defineProperty(window,"EventSource",{value:Source,writable:true});
 },snapshot);
 await page.route("**/api/**",r=>r.fulfill({status:200,json:{}}));
 await page.route("**/api/portfolio",r=>r.fulfill({status:200,json:{bankroll:1500000,peak_value:1500000,last_sync:now,total_deployed_pct:0,total_deployed_dollars:0,remaining_capacity_pct:100,position_count:0,defined_risk_count:0,undefined_risk_count:0,avg_kelly_optimal:null,positions:[],exposure:{},violations:[]}}));
 await page.route("**/api/orders",r=>r.fulfill({status:200,json:{last_sync:now,open_orders:[],executed_orders:[],open_count:0,executed_count:0}}));
 await page.route("**/api/informed-flow/**",r=>r.fulfill({status:200,json:{ticker:"AAPL",congress_trades:[],insider_trades:[],institutional_summary:null}}));
 await page.route("**/api/flow-analysis/AAPL**",r=>r.fulfill({status:200,json:{ticker:"AAPL",fetched_at:now,lookback_days:20,verdict:{direction:"NEUTRAL",confidence:0},dark_pool:{aggregate:{},daily:[]},options_flow:{},cache_meta:{age_seconds:0,is_stale:false}}}));
 await page.route("**/api/uw/research?*",r=>r.fulfill({status:200,json:snapshot}));
 let refreshes=0;
 await page.route("**/api/uw/research",r=>{refreshes++;return r.fulfill({status:200,json:{...snapshot,refresh_pending:true}})});
 await page.goto("/flow-analysis/AAPL",{waitUntil:"domcontentloaded"});
 const panel=page.getByTestId("uw-research-panel");
 await expect(panel).toBeVisible();await expect(panel).toContainText("Stream gaps recorded: 2");await expect(panel).toContainText("Execution quotes: IB");await expect(panel).toContainText("OI confirmation: daily");
 await panel.getByText("Observed option strategies",{exact:false}).click();
 await expect(panel.getByText("-2400.50",{exact:true})).toBeVisible();await expect(panel).toContainText("stale");
 await panel.getByRole("button",{name:"Refresh research"}).click();await expect.poll(()=>refreshes).toBe(1);await expect(panel.getByRole("button",{name:"Refreshing"})).toBeDisabled();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await panel.screenshot({path:info.outputPath(`uw-research-${width}.png`)});
});

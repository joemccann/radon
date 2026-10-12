/** Fixed D3 SVG grammar. All document strings enter through XML escaping. */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const modules = [process.env.RADON_CHART_NODE_MODULES, path.join(root, 'web/node_modules'), '/opt/radon-charts/node_modules'].filter(Boolean);
const d3Path = modules.map(dir => path.join(dir, 'd3/src/index.js')).find(file => fs.existsSync(file));
if (!d3Path) throw new Error('D3 is unavailable; install the pinned chart runtime');
const { scaleLinear, scaleBand, scalePoint, scaleUtc, line } = await import(pathToFileURL(d3Path));
const request = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const p = request.plan;
const W = 1200;
const rows = p.bars ?? p.marks ?? p.points;
const rawPublisher = String(request.publisher ?? '').trim();
const publisher = ['', 'unknown', 'unknown publisher', 'unverified', 'n/a', 'none', 'null', 'unavailable'].includes(rawPublisher.toLowerCase()) ? 'Source PDF' : rawPublisher.slice(0,180);
const provenance = `Source: ${[publisher,request.documentDate,request.pages?.length?`pp. ${request.pages.join(', ')}`:''].filter(Boolean).join('  |  ')}`;
const footerLines = [];
// Reserve a separate right column for branding, including for long names.
for (const word of provenance.split(/\s+/).flatMap(word => word.match(/.{1,52}/g) ?? [])) {
  if (!footerLines.length || (footerLines.at(-1)+' '+word).length > 52) footerLines.push(word);
  else footerLines[footerLines.length-1] += ' '+word;
}
const footerExtra = Math.max(0,footerLines.length-1)*28;
const chartHeight = Math.max(760, 340 + rows.length * 76);
const H = chartHeight + footerExtra;
const C = {bg:'#0a0f14', panel:'#0f1519', grid:'#1e293b', text:'#e2e8f0', muted:'#94a3b8', accent:'#05AD98', prior:'#94a3b8'};
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[char]));
const parts = [];
const tag = (name, attrs, body='') => `<${name} ${Object.entries(attrs).map(([key,value]) => `${key}="${esc(value)}"`).join(' ')}>${body}</${name}>`;
const rect = attrs => parts.push(tag('rect',attrs));
const stroke = (x1,y1,x2,y2,attrs={}) => parts.push(tag('line',{x1,y1,x2,y2,stroke:C.grid,'stroke-width':1,...attrs}));
const text = (value,x,y,attrs={}) => parts.push(tag('text',{x,y,fill:C.text,'font-size':20,...attrs},esc(value)));
const wrap = (value,x,y,maxChars=88,attrs={}) => {
  const words = String(value ?? '').split(/\s+/);
  const lines = [];
  for (const word of words) {
    if (!lines.length || (lines.at(-1)+' '+word).length > maxChars) lines.push(word);
    else lines[lines.length-1] += ' '+word;
  }
  lines.forEach((value,index) => text(value,x,y+index*28,attrs));
  return lines.length*28;
};
const number = value => String(Object.is(value, -0) ? 0 : value);
const measured = (value,unit=p.unit) => unit==='%' ? `${number(value)}%` : unit==='bp' ? `${number(value)} bp` : `${number(value)} ${unit}`;
const fontDir = [process.env.RADON_CHART_FONTS, path.join(root,'web/public/fonts'), '/opt/radon-charts/fonts'].filter(Boolean)
  .find(dir => fs.existsSync(path.join(dir,'Inter-Variable.woff2')) && fs.existsSync(path.join(dir,'IBMPlexMono-400.woff2')));
if (!fontDir) throw new Error('Local Inter and IBM Plex Mono fonts are required');
const font = name => fs.readFileSync(path.join(fontDir,name)).toString('base64');
const style = `<style>@font-face{font-family:Inter;src:url(data:font/woff2;base64,${font('Inter-Variable.woff2')}) format('woff2');font-weight:100 900}@font-face{font-family:IBM;src:url(data:font/woff2;base64,${font('IBMPlexMono-400.woff2')}) format('woff2')}text{font-family:Inter,sans-serif}.number{font-family:IBM,monospace;font-variant-numeric:tabular-nums}</style>`;
rect({x:0,y:0,width:W,height:H,fill:C.bg});
rect({x:24,y:24,width:W-48,height:H-48,fill:C.panel,stroke:C.grid,rx:4});
text('RADON  /  SOURCE DATA',56,62,{'font-size':15,fill:C.muted,'letter-spacing':1.2,class:'number'});
let titleBottom = 108+wrap(p.title,56,108,58,{'font-size':30,'font-weight':600});
if (p.subtitle) titleBottom += wrap(p.subtitle,56,titleBottom+12,92,{'font-size':19,fill:C.muted})+12;
const top = Math.max(202,titleBottom+44);
const bottom = chartHeight-208;
const temporal = p.kind==='bar' && (p.temporal || p.comparison || p.orientation==='vertical' || rows.every(row => /\b(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec|20\d\d)\b/i.test(row.label)));
const mono = {class:'number'};
const metricLabel = p.metric ? (p.metric.includes(`(${p.unit})`) ? p.metric : `${p.metric} (${p.unit})`) : p.unit;
function horizontalAxis(scale,axis,y,label) {
  for (const tick of scale.ticks(6)) {
    const x=scale(tick);stroke(x,top,x,y,{stroke:tick===0?'#758192':C.grid});
    text(number(tick),x,y+32,{'text-anchor':'middle',fill:C.muted,'font-size':18,...mono});
  }
  text(label,W-84,y+66,{'text-anchor':'end',fill:C.muted,'font-size':18});
}
function verticalAxis(scale,left,unit) {
  for (const tick of scale.ticks(5)) {
    const y=scale(tick);stroke(left,y,W-100,y,{stroke:tick===0?'#758192':C.grid});
    text(number(tick),left-18,y+6,{'text-anchor':'end',fill:C.muted,'font-size':18,...mono});
  }
  text(unit,left,top-26,{fill:C.muted,'font-size':18});
}
if (p.kind==='bar' && temporal) {
  const left=116,right=W-100;
  const y=scaleLinear(p.axis,[bottom,top]);
  const x=scaleBand(rows.map(row=>row.label),[left+56,right-56]).padding(.48);
  verticalAxis(y,left,p.yLabel ?? metricLabel);
  rows.forEach((row,index) => {
    const px=x(row.label),py=Math.min(y(0),y(row.value));
    rect({x:px,y:py,width:x.bandwidth(),height:Math.abs(y(row.value)-y(0)),fill:index===rows.length-1?C.accent:C.prior,'data-mark':'bar','data-label':row.label,'data-value':row.value});
    text(measured(row.value),px+x.bandwidth()/2,row.value>=0?py-18:y(row.value)+30,{'text-anchor':'middle','font-size':29,'font-weight':600,...mono});
    wrap(row.label,px+x.bandwidth()/2,bottom+38,22,{'text-anchor':'middle','font-size':21});
  });
} else if (p.kind==='bar' || p.kind==='range') {
  const longest=Math.max(...rows.map(row=>row.label.length));
  const left=Math.min(480,Math.max(250,longest*10+80)),right=W-180;
  const x=scaleLinear(p.axis,[left,right]);
  const y=scaleBand(rows.map(row=>row.label),[top,bottom]).padding(.42);
  horizontalAxis(x,p.axis,bottom,p.xLabel ?? metricLabel);
  rows.forEach(row=>{
    const cy=y(row.label)+y.bandwidth()/2;
    wrap(row.label,left-24,cy+6,Math.floor((left-90)/10),{'text-anchor':'end','font-size':20});
    if (p.kind==='bar') {
      const px=Math.min(x(0),x(row.value));
      rect({x:px,y:y(row.label),width:Math.abs(x(row.value)-x(0)),height:y.bandwidth(),fill:C.accent,'data-mark':'bar','data-label':row.label,'data-value':row.value});
      text(measured(row.value),row.value>=0?x(row.value)+16:x(row.value)-16,cy+7,{'text-anchor':row.value>=0?'start':'end','font-size':21,...mono});
    } else {
      if(row.low!==row.high) {
        stroke(x(row.low),cy,x(row.high),cy,{stroke:C.accent,'stroke-width':12,'data-mark':'range','data-label':row.label,'data-value':`${row.low}..${row.high}`});
        stroke(x(row.low),cy-12,x(row.low),cy+12,{stroke:C.accent,'stroke-width':2});
        stroke(x(row.high),cy-12,x(row.high),cy+12,{stroke:C.accent,'stroke-width':2});
      } else parts.push(tag('circle',{cx:x(row.low),cy,r:7,fill:C.accent,'data-mark':'point','data-label':row.label,'data-value':row.low}));
      text(row.low===row.high?measured(row.low):`${number(row.low)} to ${measured(row.high)}`,right+24,cy+7,{'font-size':20,...mono});
      if(row.detail) text(row.detail,left-24,cy+30,{'text-anchor':'end','font-size':16,fill:C.muted});
    }
  });
  if(p.reference) { const px=x(p.reference.value);stroke(px,top-4,px,bottom,{stroke:C.muted,'stroke-dasharray':'5 5'});text(p.reference.label,px,top-18,{'text-anchor':'middle','font-size':16,fill:C.muted}); }
} else if (p.kind==='line') {
  const left=116,right=W-140;
  const dates=rows.map(row=>new Date(Number.isFinite(row.timestamp) ? row.timestamp : row.label));
  const dated=dates.every(date=>Number.isFinite(+date));
  const x=dated?scaleUtc([dates[0],dates.at(-1)],[left+36,right-36]):scalePoint(rows.map(row=>row.label),[left+36,right-36]);
  const y=scaleLinear(p.axis,[bottom,top]);
  verticalAxis(y,left,p.yLabel ?? metricLabel);
  const px=(row,index)=>x(dated?dates[index]:row.label);
  parts.push(tag('path',{d:line().x((row,index)=>px(row,index)).y(row=>y(row.value))(rows),fill:'none',stroke:C.accent,'stroke-width':3}));
  rows.forEach((row,index)=>{
    parts.push(tag('circle',{cx:px(row,index),cy:y(row.value),r:6,fill:C.accent,'data-mark':'point','data-label':row.label,'data-value':row.value}));
    text(measured(row.value),px(row,index),y(row.value)-18,{'text-anchor':'middle','font-size':21,...mono});
    wrap(row.label,px(row,index),bottom+38,18,{'text-anchor':'middle','font-size':18,fill:C.muted});
  });
} else if(p.kind==='scatter') {
  const left=116,right=W-220;
  const x=scaleLinear(p.xAxis,[left,right]),y=scaleLinear(p.yAxis,[bottom,top]);
  verticalAxis(y,left,p.yLabel ?? p.yUnit);
  horizontalAxis(x,p.xAxis,bottom,p.xLabel ?? p.xUnit);
  rows.forEach(row=>{
    parts.push(tag('circle',{cx:x(row.x),cy:y(row.y),r:8,fill:C.accent,'data-mark':'point','data-label':row.label,'data-value':`${row.x},${row.y}`}));
    text(row.label,x(row.x)+16,y(row.y)-12,{'font-size':20});
    text(`${measured(row.x,p.xUnit)} / ${measured(row.y,p.yUnit)}`,x(row.x)+16,y(row.y)+15,{'font-size':17,fill:C.muted,...mono});
  });
}
const notes=[p.note,p.sourceNote].filter(Boolean).join(' ');
if(notes) wrap(notes,56,chartHeight-108,100,{'font-size':17,fill:C.muted});
stroke(56,chartHeight-66,W-56,chartHeight-66);
footerLines.forEach((label,index) => text(label,56,chartHeight-40+index*28,{'font-size':16,fill:C.muted}));
text('https://radon.run',W-56,H-40,{'font-size':16,fill:C.muted,'text-anchor':'end'});
const svg=tag('svg',{xmlns:'http://www.w3.org/2000/svg',width:W,height:H,viewBox:`0 0 ${W} ${H}`,role:'img','aria-label':p.title},style+tag('title',{},esc(p.title))+parts.join(''));
process.stdout.write(JSON.stringify({svg,width:W,height:H}));

/**
 * Plans a chart from a finding's title and body.
 * Identify comparable numbers, pick line / bar / range / scatter,
 * then keep the plan only when each point still traces to its label.
 */

export type RangeMark = {
  id: string;
  label: string;
  detail: string;
  low: number;
  high: number;
  estimated: boolean;
};

export type RangePlan = {
  kind: "range";
  title: string;
  unit: string;
  axis: [number, number];
  ticks: number[];
  marks: RangeMark[];
  reference?: { value: number; label: string };
  sourceNote?: string;
};

export type BarPlan = {
  kind: "bar";
  title: string;
  unit: string;
  axis: [number, number];
  ticks: number[];
  bars: { id: string; label: string; value: number }[];
  note?: string;
};

export type LinePlan = {
  kind: "line";
  title: string;
  unit: string;
  axis: [number, number];
  ticks: number[];
  points: { id: string; label: string; value: number }[];
};

export type ScatterPlan = {
  kind: "scatter";
  title: string;
  xUnit: string;
  yUnit: string;
  xAxis: [number, number];
  yAxis: [number, number];
  xTicks: number[];
  yTicks: number[];
  points: { id: string; label: string; x: number; y: number }[];
};

export type ChartPlan = RangePlan | BarPlan | LinePlan | ScatterPlan;

const STOP = new Set([
  "The", "Its", "It", "But", "That", "This", "Earlier", "Credit", "Markets",
  "Rates", "An", "US", "HY", "AI", "GS", "PMI", "Fed", "September", "Virginia",
  "Jan", "Sep", "Global", "Research", "Deutsche", "Bank", "Finance", "Government",
]);

const ISSUER = /\b(finance|government|construction|supranational|technology|energy|corporate)\b/i;

type Sentence = { start: number; end: number; text: string };
type Span = { start: number; end: number };
type Obs = {
  label: string;
  unit: string;
  role: "level" | "change";
  low: number;
  high: number;
  at: number;
  time?: string;
  window?: string;
  estimated?: boolean;
  detail?: string;
};

function normalize(text: string): string {
  return text.replace(/[–—]/g, "-").replace(/[ \t]+/g, " ").trim();
}

function round(value: number): number {
  return Math.round(value * 1000) / 1000;
}

function sentences(text: string): Sentence[] {
  const shielded = text.replace(/(\d)\.(\d)/g, "$1\u0000$2");
  const found: Sentence[] = [];
  for (const match of shielded.matchAll(/[^.!?]+[.!?]+|[^.!?]+$/g)) {
    const raw = match[0];
    const lead = raw.match(/^\s*/)?.[0].length ?? 0;
    const start = (match.index ?? 0) + lead;
    const body = raw.slice(lead).trim().replace(/\u0000/g, ".");
    if (body) found.push({ start, end: start + body.length, text: body });
  }
  return found;
}

function sentenceAt(list: Sentence[], index: number): Sentence | undefined {
  return list.find((sentence) => index >= sentence.start && index < sentence.end);
}

function overlaps(spans: Span[], start: number, end: number): boolean {
  return spans.some((span) => start < span.end && end > span.start);
}

function lastName(text: string): string {
  const re = /\b([A-Z][A-Za-z0-9]+)(?:['’]s)?(?:\s+([A-Z][A-Za-z0-9]*))?/g;
  let found = "";
  for (const match of text.matchAll(re)) {
    if (STOP.has(match[1])) continue;
    const second = match[2];
    found = second && !STOP.has(second) ? `${match[1]} ${second}` : match[1];
  }
  return found;
}

function slug(label: string): string {
  return label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "mark";
}

function uniqueId(label: string, used: Set<string>): string {
  const base = slug(label);
  if (!used.has(base)) {
    used.add(base);
    return base;
  }
  let n = 2;
  while (used.has(`${base}-${n}`)) n += 1;
  const id = `${base}-${n}`;
  used.add(id);
  return id;
}

function stepFor(span: number): number {
  if (span <= 2) return 0.5;
  if (span <= 15) return 2;
  if (span <= 80) return 10;
  const pow = 10 ** Math.floor(Math.log10(span));
  return span / pow <= 2 ? pow / 2 : pow;
}

function frame(values: number[]): { axis: [number, number]; ticks: number[] } {
  const minV = Math.min(...values, 0);
  const maxV = Math.max(...values, 0);
  const span = Math.max(maxV - minV, 0.5);
  const step = stepFor(span);
  const lo = round(Math.floor((minV - (minV < 0 ? span * 0.08 : 0)) / step) * step);
  const padded = maxV === 0 ? 0 : maxV + span * 0.08;
  let hi = round(Math.ceil(padded / step) * step);
  if (hi <= maxV && maxV !== 0) hi = round(hi + step);
  if (hi === lo) hi = round(lo + step);
  const ticks: number[] = [];
  for (let tick = lo; tick <= hi + step * 0.001; tick = round(tick + step)) ticks.push(round(tick));
  return { axis: [lo, hi], ticks };
}

function moneyBn(amount: number, suffix: string): number {
  return /^(mn|m|million)$/i.test(suffix) ? round(amount / 1000) : amount;
}

function moneyUnit(symbol: string): string {
  if (symbol === "€") return "€bn";
  if (symbol === "£") return "£bn";
  return "$bn";
}

function clauseStart(text: string, index: number, sentenceStart: number): number {
  const prior = text.slice(sentenceStart, index);
  const cuts = [prior.lastIndexOf(","), prior.lastIndexOf(";"), prior.toLowerCase().lastIndexOf(" and ")];
  const cut = Math.max(...cuts);
  return cut >= 0 ? sentenceStart + cut + 1 : sentenceStart;
}

function cap(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
}

function moneyLabel(clause: string): string {
  const issuer = clause.match(ISSUER);
  if (issuer) return cap(issuer[1]);
  const words = clause.replace(/[()]/g, " ").trim().split(/\s+/);
  const kept: string[] = [];
  for (const word of words) {
    const bare = word.replace(/[^A-Za-z]/g, "");
    if (!bare) continue;
    if (/^(bought|added|issued|was|were|reached|led|sits|is|grew|at|of|the|in|by|to|from)$/i.test(bare)) {
      if (kept.length) break;
      continue;
    }
    if (/^[A-Z]/.test(bare) || kept.length) kept.push(bare);
    if (kept.length === 3) break;
  }
  return kept.join(" ");
}

function percentLabel(text: string, list: Sentence[], index: number): string {
  const sentence = sentenceAt(list, index);
  if (!sentence) return "";
  const clause = text.slice(clauseStart(text, index, sentence.start), index);
  const tenor = clause.match(/(\d+)\s*-\s*year/i);
  if (tenor) return `${tenor[1]}-year`;
  const position = list.indexOf(sentence);
  const previous = position > 0 ? list[position - 1] : undefined;
  const pronoun = /^(Its|It|That|This)\b/.test(sentence.text);
  const name = pronoun && previous
    ? lastName(previous.text)
    : lastName(clause) || lastName(sentence.text);
  if (!name) return "";
  if (/\bHY\b/.test(sentence.text)) return `${name} HY`;
  if (/\bVirginia\b/.test(sentence.text)) return `${name} VA`;
  return name;
}

function dedupe(items: Obs[]): Obs[] {
  const seen = new Set<string>();
  const kept: Obs[] = [];
  for (const item of items) {
    const key = item.time || item.window
      ? [item.unit, item.role, item.label.toLowerCase(), item.low, item.high, item.time ?? "", item.window ?? ""].join("|")
      : [item.unit, item.role, item.low, item.high].join("|");
    if (seen.has(key)) continue;
    seen.add(key);
    kept.push(item);
  }
  return kept;
}

function dropTotal(items: Obs[]): Obs[] {
  if (items.length < 3) return items;
  for (let i = 0; i < items.length; i += 1) {
    const rest = items.filter((_, index) => index !== i);
    const sum = rest.reduce((total, item) => total + item.low, 0);
    const value = items[i].low;
    if (Math.abs(sum - value) <= Math.max(0.02, Math.abs(value) * 0.02)) return rest;
  }
  return items;
}

function extract(text: string): Obs[] {
  const list = sentences(text);
  const consumed: Span[] = [];
  const found: Obs[] = [];
  const take = (start: number, end: number) => {
    if (overlaps(consumed, start, end)) return false;
    consumed.push({ start, end });
    return true;
  };

  for (const match of text.matchAll(/grew from\s+~?\s*([€$£])\s*(\d+(?:\.\d+)?)\s*(bn|mn|million|billion)\s+in\s+([A-Z][a-z]+\s+\d{4})\s+to\s+~?\s*([€$£])\s*(\d+(?:\.\d+)?)\s*(bn|mn|million|billion)\s+in\s+([A-Z][a-z]+\s+\d{4})/gi)) {
    const start = match.index ?? 0;
    const end = start + match[0].length;
    if (!take(start, end)) continue;
    const before = text.slice(Math.max(0, start - 80), start).toLowerCase();
    const words = before.match(/\b(tokenized|assets|equities|credit|debt|issuance)\b/g) ?? [];
    const label = words.includes("tokenized") && words.includes("assets") ? "Tokenized assets" : "Series";
    if (label === "Series") continue;
    const unit = moneyUnit(match[1]);
    found.push({ label, unit, role: "level", low: moneyBn(Number(match[2]), match[3]), high: moneyBn(Number(match[2]), match[3]), at: start, time: match[4] });
    found.push({ label, unit, role: "level", low: moneyBn(Number(match[6]), match[7]), high: moneyBn(Number(match[6]), match[7]), at: start + match[0].indexOf(match[5]), time: match[8] });
  }

  for (const match of text.matchAll(/low\s*-?\s*to\s*mid\s*-?\s*(\d+(?:\.\d+)?)%/gi)) {
    const start = match.index ?? 0;
    const end = start + match[0].length;
    if (!take(start, end)) continue;
    const label = percentLabel(text, list, start);
    if (!label) continue;
    const low = Number(match[1]);
    found.push({ label, unit: "%", role: "level", low, high: round(low + 0.5), at: start, estimated: true });
  }

  for (const match of text.matchAll(/(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)%/g)) {
    const start = match.index ?? 0;
    const end = start + match[0].length;
    if (!take(start, end)) continue;
    const label = percentLabel(text, list, start);
    if (!label) continue;
    const sentence = sentenceAt(list, start);
    const detail = sentence?.text.match(/\$(\d+(?:\.\d+)?)bn/i);
    found.push({
      label, unit: "%", role: "level", low: Number(match[1]), high: Number(match[2]), at: start,
      detail: detail ? `$${detail[1]}bn` : "",
    });
  }

  for (const match of text.matchAll(/\b(?:up|down|rose|fell|higher|lower)\s+~?\s*(\d+(?:\.\d+)?)%/gi)) {
    const start = match.index ?? 0;
    if (!take(start, start + match[0].length)) continue;
    found.push({ label: "Change", unit: "%", role: "change", low: Number(match[1]), high: Number(match[1]), at: start });
  }

  for (const match of text.matchAll(/(\d+(?:\.\d+)?)%\s+(?:of|above|below)\b/gi)) {
    const start = match.index ?? 0;
    take(start, start + match[0].length);
  }

  for (const match of text.matchAll(/near\s*-?\s*(\d+(?:\.\d+)?)%/gi)) {
    const start = match.index ?? 0;
    take(start, start + match[0].length);
  }

  for (const match of text.matchAll(/(\d+(?:\.\d+)?)%\s*chance\b/gi)) {
    const start = match.index ?? 0;
    take(start, start + match[0].length);
  }

  for (const match of text.matchAll(/~?(\d+(?:\.\d+)?)%/g)) {
    const start = match.index ?? 0;
    const end = start + match[0].length;
    if (!take(start, end)) continue;
    const label = percentLabel(text, list, start);
    if (!label) continue;
    const value = Number(match[1]);
    found.push({ label, unit: "%", role: "level", low: value, high: value, at: start });
  }

  for (const match of text.matchAll(/~?\s*([€$£])\s*(\d+(?:\.\d+)?)\s*(bn|mn|million|billion)\b/gi)) {
    const start = match.index ?? 0;
    const end = start + match[0].length;
    if (!take(start, end)) continue;
    const sentence = sentenceAt(list, start);
    if (sentence && /\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?%|low\s*-?\s*to\s*mid/i.test(sentence.text)) continue;
    const label = moneyLabel(text.slice(clauseStart(text, start, sentence?.start ?? 0), start));
    if (!label) continue;
    const value = moneyBn(Number(match[2]), match[3]);
    found.push({ label, unit: moneyUnit(match[1]), role: "level", low: value, high: value, at: start });
  }

  for (const match of text.matchAll(/~?(\d+(?:\.\d+)?)\s*bps?\b/gi)) {
    const start = match.index ?? 0;
    const end = start + match[0].length;
    if (!take(start, end)) continue;
    const after = text.slice(end, end + 48);
    let window = "";
    if (/two weeks|2 weeks/i.test(after)) window = "2 weeks";
    else if (/past month|one month|1 month|\ba month\b/i.test(after)) window = "1 month";
    if (!window) continue;
    const before = text.slice(Math.max(0, start - 80), start);
    const tenor = before.match(/(\d+)\s*-\s*year/i);
    found.push({ label: window, unit: "bp", role: "change", low: Number(match[1]), high: Number(match[1]), at: start, window, detail: tenor ? `${tenor[1]}-year` : "" });
  }

  for (const match of text.matchAll(/([+-]?\d+(?:\.\d+)?)z\b/gi)) {
    const start = match.index ?? 0;
    const end = start + match[0].length;
    if (!take(start, end)) continue;
    const before = text.slice(Math.max(0, start - 18), start);
    let label = "";
    if (/after\s*[-~(]?\s*$/i.test(before)) label = "After";
    else if (/MoM\s*$/i.test(before)) label = "MoM";
    else if (/\bweek\b/i.test(before)) label = "Week";
    if (!label) continue;
    const sentence = sentenceAt(list, start);
    const subject = sentence?.text.match(/\b(OATs?|Bunds?|WTI|NDX)\b/);
    found.push({ label, unit: "z", role: "change", low: Number(match[1]), high: Number(match[1]), at: start, window: label, detail: subject?.[1] ?? "" });
  }

  for (const match of text.matchAll(/\bduration is\s+(\d+(?:\.\d+)?)\s+years\b/gi)) {
    const start = match.index ?? 0;
    if (!take(start, start + match[0].length)) continue;
    const label = percentLabel(text, list, start) || moneyLabel(text.slice(sentenceAt(list, start)?.start ?? 0, start));
    if (!label) continue;
    found.push({ label, unit: "years", role: "level", low: Number(match[1]), high: Number(match[1]), at: start });
  }

  return dedupe(found);
}

function referenceFor(text: string): { value: number; label: string } | undefined {
  const match = text.match(/near\s*-?\s*(\d+(?:\.\d+)?)%/i);
  return match ? { value: Number(match[1]), label: `near ${match[1]}%` } : undefined;
}

function build(text: string): ChartPlan[] {
  const all = extract(text);
  const used = new Set<Obs>();
  const ids = new Set<string>();
  const plans: ChartPlan[] = [];
  const free = () => all.filter((item) => !used.has(item));
  const consume = (items: Obs[]) => items.forEach((item) => used.add(item));

  const lineKeys = new Map<string, Obs[]>();
  for (const item of free()) {
    if (!item.time) continue;
    const key = `${item.label}|${item.unit}`;
    lineKeys.set(key, [...(lineKeys.get(key) ?? []), item]);
  }
  for (const items of lineKeys.values()) {
    if (items.length < 2) continue;
    const ordered = [...items].sort((a, b) => a.at - b.at);
    const scale = frame(ordered.map((item) => item.low));
    plans.push({
      kind: "line",
      title: ordered[0].label,
      unit: ordered[0].unit,
      ...scale,
      points: ordered.map((item) => ({ id: uniqueId(item.time ?? item.label, ids), label: item.time ?? item.label, value: item.low })),
    });
    consume(ordered);
  }

  const byLabel = new Map<string, Obs[]>();
  for (const item of free()) {
    byLabel.set(item.label, [...(byLabel.get(item.label) ?? []), item]);
  }
  const scatterLabels = [...byLabel.entries()].filter(([, items]) => new Set(items.map((item) => item.unit)).size >= 2);
  if (scatterLabels.length >= 2) {
    const units = [...new Set(scatterLabels[0][1].map((item) => item.unit))];
    const pair = units.includes("years") && units.includes("%") ? ["years", "%"] : units.slice(0, 2);
    const ready = scatterLabels.filter(([, items]) => pair.every((unit) => items.some((item) => item.unit === unit)));
    if (ready.length >= 2 && pair[0] !== pair[1]) {
      const points = ready.map(([label, items]) => {
        const x = items.find((item) => item.unit === pair[0])!;
        const y = items.find((item) => item.unit === pair[1])!;
        return { label, x: x.low, y: y.low, obs: [x, y] };
      });
      const xScale = frame(points.map((point) => point.x));
      const yScale = frame(points.map((point) => point.y));
      plans.push({
        kind: "scatter",
        title: `${pair[1] === "%" ? "Yield" : pair[1]} vs ${pair[0]}`,
        xUnit: pair[0],
        yUnit: pair[1] === "%" ? "%" : pair[1],
        xAxis: xScale.axis,
        yAxis: yScale.axis,
        xTicks: xScale.ticks,
        yTicks: yScale.ticks,
        points: points.map((point) => ({ id: uniqueId(point.label, ids), label: point.label, x: point.x, y: point.y })),
      });
      consume(points.flatMap((point) => point.obs));
    }
  }

  const levels = free().filter((item) => item.unit === "%" && item.role === "level");
  if (levels.some((item) => item.low !== item.high) || levels.length >= 2) {
    const marks = [...levels].sort((a, b) => b.high - a.high || b.low - a.low || a.label.localeCompare(b.label));
    const reference = referenceFor(text);
    const scale = frame([...marks.map((mark) => mark.high), ...(reference ? [reference.value] : [])]);
    const plan: RangePlan = {
      kind: "range",
      title: /dollar yield/i.test(text) ? "Dollar yields" : "Yields",
      unit: "%",
      ...scale,
      marks: marks.map((mark) => ({
        id: uniqueId(mark.label, ids),
        label: mark.label,
        detail: mark.detail ?? "",
        low: mark.low,
        high: mark.high,
        estimated: mark.estimated === true,
      })),
    };
    if (reference) plan.reference = reference;
    if (marks.some((mark) => mark.estimated)) {
      plan.sourceNote = "Desk band places the printed phrase on the axis. Not a printed coupon.";
    }
    plans.push(plan);
    consume(marks);
  }

  const groups = new Map<string, Obs[]>();
  for (const item of free()) {
    if (item.role === "change" && item.unit === "%" && !item.window) continue;
    const key = `${item.unit}|${item.detail ?? ""}|${item.role}`;
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  for (const [key, items] of groups) {
    const peers = key.startsWith("€") || key.startsWith("$") || key.startsWith("£") ? dropTotal(items) : items;
    if (peers.length < 2) continue;
    const ordered = peers.some((item) => item.window)
      ? [...peers].sort((a, b) => a.at - b.at)
      : [...peers].sort((a, b) => b.low - a.low || a.label.localeCompare(b.label));
    const scale = frame(ordered.map((item) => item.low));
    const unit = ordered[0].unit;
    const tenor = ordered.find((item) => item.detail && item.unit === "bp")?.detail;
    const subject = ordered.find((item) => item.detail && item.unit === "z")?.detail;
    const title = unit === "bp"
      ? (tenor ? `${tenor} change` : "Change")
      : unit === "z"
        ? (subject || "Positioning")
        : /issuance/i.test(text) ? "Issuance" : "Amounts";
    const plan: BarPlan = {
      kind: "bar",
      title,
      unit,
      ...scale,
      bars: ordered.map((item) => ({ id: uniqueId(item.label, ids), label: item.label, value: item.low })),
    };
    const windows = new Set(ordered.map((item) => item.window));
    if (windows.has("2 weeks") && windows.has("1 month")) plan.note = "2-week window sits inside the month. Not additive.";
    plans.push(plan);
    consume(ordered);
  }

  const earliest = (plan: ChartPlan) => {
    const labels = plan.kind === "range"
      ? plan.marks.map((mark) => mark.label)
      : plan.kind === "bar"
        ? plan.bars.map((row) => row.label)
        : plan.points.map((point) => point.label);
    const ats = labels.map((label) => all.find((item) => item.label === label || item.time === label || item.window === label)?.at ?? Number.MAX_SAFE_INTEGER);
    return Math.min(...ats);
  };
  return plans.sort((a, b) => earliest(a) - earliest(b));
}

function labelWords(label: string): string[] {
  return label.split(/\s+/).filter(Boolean).map((word) => (word === "VA" ? "virginia" : word.toLowerCase()));
}

function close(left: number, right: number): boolean {
  return Math.abs(left - right) <= 0.0005;
}

type Hit = { index: number; end: number; values: number[] };

function hitsIn(sentence: string, unit: string): Hit[] {
  const hits: Hit[] = [];
  if (unit === "%") {
    for (const match of sentence.matchAll(/(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)%/g)) {
      hits.push({ index: match.index ?? 0, end: (match.index ?? 0) + match[0].length, values: [Number(match[1]), Number(match[2])] });
    }
    for (const match of sentence.matchAll(/(?<![\d.-])(\d+(?:\.\d+)?)%/g)) {
      const index = match.index ?? 0;
      if (hits.some((hit) => index >= hit.index && index < hit.end)) continue;
      hits.push({ index, end: index + match[0].length, values: [Number(match[1])] });
    }
  } else if (unit === "bp") {
    for (const match of sentence.matchAll(/(\d+(?:\.\d+)?)\s*bps?\b/gi)) {
      hits.push({ index: match.index ?? 0, end: (match.index ?? 0) + match[0].length, values: [Number(match[1])] });
    }
  } else if (unit === "z") {
    for (const match of sentence.matchAll(/([+-]?\d+(?:\.\d+)?)z\b/gi)) {
      hits.push({ index: match.index ?? 0, end: (match.index ?? 0) + match[0].length, values: [Number(match[1])] });
    }
  } else if (unit === "years") {
    for (const match of sentence.matchAll(/(\d+(?:\.\d+)?)\s+years\b/gi)) {
      hits.push({ index: match.index ?? 0, end: (match.index ?? 0) + match[0].length, values: [Number(match[1])] });
    }
  } else {
    const symbol = unit.startsWith("€") ? "€" : unit.startsWith("£") ? "£" : "\\$";
    for (const match of sentence.matchAll(new RegExp(`${symbol}\\s*(\\d+(?:\\.\\d+)?)\\s*(bn|mn|million|billion)\\b`, "gi"))) {
      hits.push({ index: match.index ?? 0, end: (match.index ?? 0) + match[0].length, values: [moneyBn(Number(match[1]), match[2])] });
    }
  }
  return hits.sort((a, b) => a.index - b.index);
}

function grounded(text: string, label: string, value: number, unit: string, estimated = false): boolean {
  const list = sentences(text);
  const words = labelWords(label);
  for (let index = 0; index < list.length; index += 1) {
    const sentence = list[index];
    const previous = /^(Its|It|That|This)\b/.test(sentence.text) && index > 0 ? `${list[index - 1].text} ` : "";
    if (estimated) {
      if (/low\s*-?\s*to\s*mid\s*-?\s*\d+(?:\.\d+)?%/i.test(sentence.text)
        && words.every((word) => `${previous}${sentence.text}`.toLowerCase().includes(word))) return true;
      continue;
    }
    const hits = hitsIn(sentence.text, unit).filter((hit) => hit.values.some((item) => close(item, value)));
    for (const hit of hits) {
      const after = sentence.text.slice(hit.index, hit.end + 48);
      if (label === "2 weeks") {
        if (/two weeks|2 weeks/i.test(after)) return true;
        continue;
      }
      if (label === "1 month") {
        if (/past month|one month|1 month|\ba month\b/i.test(after)) return true;
        continue;
      }
      if (label === "After" || label === "MoM" || label === "Week") {
        const before = sentence.text.slice(Math.max(0, hit.index - 18), hit.index);
        if (label === "After" && /after\s*[-~(]?\s*$/i.test(before)) return true;
        if (label === "MoM" && /MoM\s*$/.test(before)) return true;
        if (label === "Week" && /\bweek\b/i.test(before)) return true;
        continue;
      }
      if (/^[A-Z][a-z]{2}\s+\d{4}$/.test(label)) {
        if (after.includes(label)) return true;
        continue;
      }
      const all = hitsIn(sentence.text, unit);
      const earlier = all.filter((item) => item.index < hit.index).at(-1);
      const local = sentence.text.slice(earlier ? earlier.end : 0, hit.index);
      const cuts = [local.lastIndexOf(","), local.lastIndexOf(";"), local.toLowerCase().lastIndexOf(" and ")];
      const cut = Math.max(...cuts);
      const gap = all.length === 1 ? `${previous}${sentence.text}` : `${previous}${cut >= 0 ? local.slice(cut + 1) : local}`;
      if (words.every((word) => gap.toLowerCase().includes(word))) return true;
    }
  }
  return false;
}

/** A plan stands only when every plotted number sits with its label in the text. */
export function verifyChart(plan: ChartPlan, raw: string): boolean {
  const text = normalize(raw);
  if (plan.kind === "line") {
    return plan.points.length >= 2 && plan.points.every((point) => grounded(text, point.label, point.value, plan.unit));
  }
  if (plan.kind === "bar") {
    return plan.bars.length >= 2 && plan.bars.every((row) => grounded(text, row.label, row.value, plan.unit));
  }
  if (plan.kind === "range") {
    const interval = plan.marks.some((mark) => mark.low !== mark.high);
    if (!interval && plan.marks.length < 2) return false;
    return plan.marks.every((mark) => {
      if (mark.estimated) return grounded(text, mark.label, mark.low, plan.unit, true);
      return grounded(text, mark.label, mark.low, "%") && (mark.low === mark.high || grounded(text, mark.label, mark.high, "%"));
    });
  }
  if (plan.kind === "scatter") {
    return plan.points.length >= 2 && plan.points.every((point) =>
      grounded(text, point.label, point.x, plan.xUnit) && grounded(text, point.label, point.y, plan.yUnit));
  }
  return false;
}

export function planCharts(raw: string): ChartPlan[] {
  const text = normalize(raw);
  if (!text) return [];
  return build(text).filter((plan) => verifyChart(plan, text));
}

export function isChartPlan(value: unknown): value is ChartPlan {
  if (!value || typeof value !== "object") return false;
  const plan = value as ChartPlan;
  if (plan.kind === "range") {
    return typeof plan.title === "string" && typeof plan.unit === "string"
      && Array.isArray(plan.axis) && plan.axis.length === 2
      && Array.isArray(plan.marks) && plan.marks.length > 0
      && plan.marks.every((mark) => mark && typeof mark.id === "string" && typeof mark.label === "string"
        && typeof mark.detail === "string" && typeof mark.low === "number" && typeof mark.high === "number"
        && typeof mark.estimated === "boolean");
  }
  if (plan.kind === "bar") {
    return typeof plan.title === "string" && typeof plan.unit === "string"
      && Array.isArray(plan.bars) && plan.bars.length > 0
      && plan.bars.every((row) => row && typeof row.id === "string" && typeof row.label === "string" && typeof row.value === "number");
  }
  if (plan.kind === "line") {
    return typeof plan.title === "string" && typeof plan.unit === "string"
      && Array.isArray(plan.points) && plan.points.length > 1
      && plan.points.every((point) => point && typeof point.id === "string" && typeof point.label === "string" && typeof point.value === "number");
  }
  if (plan.kind === "scatter") {
    return typeof plan.title === "string" && typeof plan.xUnit === "string" && typeof plan.yUnit === "string"
      && Array.isArray(plan.points) && plan.points.length > 1
      && plan.points.every((point) => point && typeof point.label === "string" && typeof point.x === "number" && typeof point.y === "number");
  }
  return false;
}

export function parseChartPlans(value: unknown): ChartPlan[] | undefined {
  if (!Array.isArray(value) || value.length === 0 || !value.every(isChartPlan)) return undefined;
  return value;
}

type ChartPost = {
  title: string;
  content?: string;
  images?: string[];
  source?: {
    kind?: string;
    figures?: unknown[];
    charts?: ChartPlan[];
  };
};

/** Generated charts are for text-only research posts. A source figure wins. */
export function chartsForPost(post: ChartPost): ChartPlan[] {
  if (post.source?.kind !== "dropbox") return [];
  if ((post.source.figures?.length ?? 0) > 0) return [];
  if ((post.images?.length ?? 0) > 0) return [];
  const stored = post.source.charts;
  if (stored && stored.length > 0 && stored.every((plan) => isChartPlan(plan) && verifyChart(plan, `${post.title}\n${post.content ?? ""}`))) return stored;
  return planCharts(`${post.title}\n${post.content ?? ""}`);
}

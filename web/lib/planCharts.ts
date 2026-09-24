/**
 * Plans charts from a published finding's title and body.
 * A lone point, a lone basis-point change, a dollar amount, or a multiple
 * is not a series. A printed range is. Probability stays on its own scale.
 */

import type { YieldMark } from "@/lib/aiCreditYields";

export type LevelsPlan = {
  kind: "levels";
  title: string;
  dek?: string;
  axis: [number, number];
  ticks: number[];
  reference?: { value: number; label: string };
  marks: YieldMark[];
  sourceNote?: string;
};

export type MovePlan = {
  kind: "move";
  title: string;
  bars: { id: string; label: string; bp: number }[];
  axisMax: number;
  ticks: number[];
  probability?: { label: string; pct: number };
  readout?: { label: string; bp: number };
  note?: string;
  barAria: string;
};

export type ChartPlan = LevelsPlan | MovePlan;

const STOP = new Set([
  "The", "Its", "It", "But", "That", "Earlier", "Credit", "Markets", "Rates",
  "This", "An", "US", "HY", "AI", "GS", "PMI", "Fed", "OpenAI", "September",
  "Virginia", "Chart", "Source", "Official", "Foreign", "Net", "August", "July",
  "June", "May", "April", "March", "Monday", "Tuesday", "Wednesday", "Thursday",
  "Friday", "Saturday", "Sunday", "In", "Of", "And", "For", "To", "On", "By",
  "At", "From", "With", "After", "Before", "During", "About", "Over", "Under",
  "Near", "Above", "Below", "Data", "Centre", "Center", "Financing", "Investors",
  "Investor", "Treasury", "Treasuries", "Research", "Global", "Investment",
  "Bank", "Note", "Desk", "Week", "Month", "Year", "End",
]);

type Span = { start: number; end: number };
type Sentence = { start: number; end: number; text: string };

function normalize(text: string): string {
  return text.replace(/[–—]/g, "-").replace(/[ \t]+/g, " ").trim();
}

function overlaps(spans: Span[], start: number, end: number): boolean {
  return spans.some((span) => start < span.end && end > span.start);
}

function sentences(text: string): Sentence[] {
  // Decimal points are not sentence boundaries. The shield is one character, so indexes still match `text`.
  const shielded = text.replace(/(\d)\.(\d)/g, "$1\u0000$2");
  const found: Sentence[] = [];
  for (const match of shielded.matchAll(/[^.!?]+[.!?]+|[^.!?]+$/g)) {
    const raw = match[0];
    const lead = raw.match(/^\s*/)?.[0].length ?? 0;
    const start = (match.index ?? 0) + lead;
    const body = raw.slice(lead).trim().replace(/\u0000/g, ".");
    if (!body) continue;
    found.push({ start, end: start + body.length, text: body });
  }
  return found;
}

function sentenceAt(list: Sentence[], index: number): Sentence | undefined {
  return list.find((sentence) => index >= sentence.start && index < sentence.end);
}

function lastName(text: string): string | null {
  const re = /\b([A-Z][A-Za-z0-9]+)(?:['’]s)?(?:\s+([A-Z][A-Za-z0-9]*))?/g;
  let found: string | null = null;
  for (const match of text.matchAll(re)) {
    const first = match[1];
    if (STOP.has(first)) continue;
    const second = match[2];
    found = second && !STOP.has(second) ? `${first} ${second}` : first;
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

function plainNumber(value: number): string {
  return Number.isInteger(value) ? String(value) : String(value);
}

function labelAt(text: string, index: number, list: Sentence[]): { label: string; detail: string } {
  const sentence = sentenceAt(list, index);
  if (!sentence) return { label: "Level", detail: "" };
  const position = list.indexOf(sentence);
  const previous = position > 0 ? list[position - 1] : undefined;
  const pronoun = /^(Its|It|That|This|But)\b/.test(sentence.text);
  const nameSource = pronoun && previous ? `${previous.text} ${sentence.text}` : sentence.text;
  const before = text.slice(sentence.start, index);
  const previousBreak = Math.max(before.lastIndexOf("%"), before.lastIndexOf("bp"));
  const lookback = previousBreak >= 0 ? before.slice(previousBreak + 1) : before;
  const tenor = lookback.match(/(\d+)\s*-\s*year/i);
  if (tenor) return { label: `US ${tenor[1]}-year`, detail: "spot" };

  const name = lastName(nameSource);
  let label = name ?? "Level";
  if (name && /\bHY\b/.test(sentence.text)) label = `${name} HY`;
  else if (name && /\bVirginia\b/.test(sentence.text)) label = `${name} VA`;
  const money = sentence.text.match(/\$(\d+(?:\.\d+)?)bn/i);
  return { label, detail: money ? `$${money[1]}bn` : "" };
}

function levelsCeiling(max: number): { axis: [number, number]; ticks: number[] } {
  const step = max <= 12 ? 2 : max <= 40 ? 5 : 10;
  const ceiling = Math.max(step, Math.ceil((max * 1.2) / step) * step);
  const ticks: number[] = [];
  for (let tick = 0; tick <= ceiling; tick += step) ticks.push(tick);
  return { axis: [0, ceiling], ticks };
}

function barCeiling(max: number): { axisMax: number; ticks: number[] } {
  const rounded = Math.ceil(max / 10) * 10;
  const axisMax = rounded === max ? rounded + 10 : rounded;
  const ticks: number[] = [];
  for (let tick = 0; tick <= axisMax; tick += 10) ticks.push(tick);
  return { axisMax, ticks };
}

function dekFor(text: string): string | undefined {
  const multiple = text.match(/S&P[^.]{0,80}?~?\s*(\d+(?:\.\d+)?)x/i);
  return multiple ? `S&P near ${multiple[1]}x.` : undefined;
}

export function planCharts(raw: string): ChartPlan[] {
  const text = normalize(raw);
  if (!text) return [];
  const list = sentences(text);
  const used = new Set<string>();
  const consumed: Span[] = [];
  const marks: YieldMark[] = [];
  let reference: { value: number; label: string } | undefined;

  for (const match of text.matchAll(/low\s*-?\s*to\s*mid\s*-?\s*(\d+(?:\.\d+)?)%/gi)) {
    const start = match.index ?? 0;
    const end = start + match[0].length;
    consumed.push({ start, end });
    const low = Number(match[1]);
    const named = labelAt(text, start, list);
    marks.push({
      id: uniqueId(named.label, used),
      label: named.label,
      detail: "desk band",
      low,
      high: low + 0.5,
      kind: "desk-band",
    });
  }

  for (const match of text.matchAll(/(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)%/g)) {
    const start = match.index ?? 0;
    const end = start + match[0].length;
    if (overlaps(consumed, start, end)) continue;
    consumed.push({ start, end });
    const named = labelAt(text, start, list);
    marks.push({
      id: uniqueId(named.label, used),
      label: named.label,
      detail: named.detail,
      low: Number(match[1]),
      high: Number(match[2]),
      kind: "printed-range",
    });
  }

  for (const match of text.matchAll(/near\s*-?\s*(\d+(?:\.\d+)?)%/gi)) {
    const start = match.index ?? 0;
    const end = start + match[0].length;
    if (overlaps(consumed, start, end)) continue;
    consumed.push({ start, end });
    if (!reference) {
      const value = Number(match[1]);
      reference = { value, label: `near ${plainNumber(value)}%` };
    }
  }

  for (const match of text.matchAll(/~?(\d+(?:\.\d+)?)%(?!\s*chance)/g)) {
    const start = match.index ?? 0;
    const end = start + match[0].length;
    if (overlaps(consumed, start, end)) continue;
    consumed.push({ start, end });
    const value = Number(match[1]);
    const named = labelAt(text, start, list);
    marks.push({
      id: uniqueId(named.label, used),
      label: named.label,
      detail: named.detail || "spot",
      low: value,
      high: value,
      kind: "point",
    });
  }

  const bars: MovePlan["bars"] = [];
  let readout: MovePlan["readout"];
  for (const match of text.matchAll(/~?(\d+(?:\.\d+)?)\s*bps?\b/gi)) {
    const start = match.index ?? 0;
    const end = start + match[0].length;
    const around = text.slice(Math.max(0, start - 24), end + 48);
    const bp = Number(match[1]);
    if (/tightening|year\s*-?\s*end/i.test(around)) {
      readout ??= { label: "Year-end tightening", bp };
      continue;
    }
    const after = text.slice(end, end + 48);
    let label: string | null = null;
    if (/two weeks|2 weeks/i.test(after)) label = "2 weeks";
    else if (/past month|one month|1 month|\ba month\b/i.test(after)) label = "1 month";
    if (!label) continue;
    bars.push({ id: uniqueId(label, used), label, bp });
  }

  let probability: MovePlan["probability"];
  for (const match of text.matchAll(/(\d+(?:\.\d+)?)%\s*chance(?:\s+of(?:\s+an?)?)?(?:\s+([A-Z][a-z]+)(?:\s+([a-z]+))?)?/g)) {
    const tail = match[3] && !/^(?:and|or|of|by|the|a|to|with)$/.test(match[3]) ? ` ${match[3]}` : "";
    probability = {
      label: match[2] ? `${match[2]}${tail}` : "Probability",
      pct: Number(match[1]),
    };
    break;
  }

  const plans: ChartPlan[] = [];
  const ranges = marks.filter((mark) => mark.kind !== "point");
  const points = marks.filter((mark) => mark.kind === "point");
  if (ranges.length >= 1 || points.length >= 2) {
    const kept = [...marks];
    kept.sort((a, b) => b.high - a.high || b.low - a.low || a.label.localeCompare(b.label));
    const peak = Math.max(...kept.map((mark) => mark.high), reference?.value ?? 0);
    const axis = levelsCeiling(peak);
    const plan: LevelsPlan = {
      kind: "levels",
      title: /yield/i.test(text) ? "Dollar yields" : "Levels",
      ...axis,
      marks: kept,
    };
    const dek = dekFor(text);
    if (dek) plan.dek = dek;
    if (reference) plan.reference = reference;
    if (kept.some((mark) => mark.kind === "desk-band")) {
      plan.sourceNote = "Desk band places the printed phrase on the axis. Not a printed coupon.";
    }
    plans.push(plan);
  }

  const plottedBars = probability && bars.length < 2 ? [] : bars.length >= 2 ? bars : [];
  if (plottedBars.length >= 2 || probability) {
    plottedBars.sort((a, b) => b.bp - a.bp || a.label.localeCompare(b.label));
    const subject = plottedBars.length && /\b10\s*-\s*year\b/i.test(text)
      ? "10-year"
      : plottedBars.length && /\b2\s*-\s*year\b/i.test(text)
        ? "2-year"
        : null;
    const barAria = subject ? `${subject} change in basis points` : "Change in basis points";
    const title = probability && plottedBars.length
      ? (subject ? `${subject} move and the ${probability.label}` : probability.label)
      : plottedBars.length
        ? (subject ? `${subject} change` : "Basis-point change")
        : probability?.label ?? "Rates";
    const ceiling = plottedBars.length ? barCeiling(Math.max(...plottedBars.map((bar) => bar.bp))) : { axisMax: 0, ticks: [0] };
    const plan: MovePlan = { kind: "move", title, bars: plottedBars, ...ceiling, barAria };
    if (probability) plan.probability = probability;
    if ((plottedBars.length >= 2 || probability) && readout) plan.readout = readout;
    const windows = new Set(plottedBars.map((bar) => bar.label));
    if (windows.has("2 weeks") && windows.has("1 month")) {
      plan.note = "2-week window sits inside the month. Not additive.";
    }
    plans.push(plan);
  }

  return plans;
}

export function isChartPlan(value: unknown): value is ChartPlan {
  if (!value || typeof value !== "object") return false;
  const plan = value as ChartPlan;
  if (plan.kind === "levels") {
    return typeof plan.title === "string"
      && Array.isArray(plan.axis) && plan.axis.length === 2 && plan.axis.every((n) => typeof n === "number")
      && Array.isArray(plan.ticks) && plan.ticks.every((n) => typeof n === "number")
      && Array.isArray(plan.marks) && plan.marks.length > 0
      && plan.marks.every((mark) => mark && typeof mark.id === "string" && typeof mark.label === "string"
        && typeof mark.detail === "string" && typeof mark.low === "number" && typeof mark.high === "number"
        && (mark.kind === "point" || mark.kind === "printed-range" || mark.kind === "desk-band"));
  }
  if (plan.kind === "move") {
    return typeof plan.title === "string"
      && Array.isArray(plan.bars) && plan.bars.every((bar) => bar && typeof bar.id === "string"
        && typeof bar.label === "string" && typeof bar.bp === "number")
      && typeof plan.axisMax === "number"
      && Array.isArray(plan.ticks)
      && (plan.probability === undefined || (typeof plan.probability.label === "string" && typeof plan.probability.pct === "number"))
      && (plan.readout === undefined || (typeof plan.readout.label === "string" && typeof plan.readout.bp === "number"));
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
  if (stored && stored.length > 0 && stored.every(isChartPlan)) return stored;
  return planCharts(`${post.title}\n${post.content ?? ""}`);
}

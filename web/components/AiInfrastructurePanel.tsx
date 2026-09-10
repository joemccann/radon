"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { AI_PANES, aiDate, aiObservationDate, aiNumber, historyGroups, sourceHref, type AiHistoryPoint, type AiIndicator, type AiPane, type AiSnapshot, type AiSource } from "@/lib/aiInfrastructure";
import { useAiInfrastructure } from "@/lib/useAiInfrastructure";
import LlmTokenIndexCard from "./LlmTokenIndexCard";
import styles from "./AiInfrastructure.module.css";

function SourceLink({ url, children }: { url: string; children: React.ReactNode }) {
  const href = sourceHref(url);
  return href ? <a href={href} target="_blank" rel="noopener noreferrer">{children} ↗</a> : <span>{children} · link unavailable</span>;
}

export function AiSeriesChart({ points }: { points: AiHistoryPoint[] }) {
  const [inspection, setInspection] = useState<number | null>(null);
  const first = points[0], last = points.at(-1)!;
  const low = Math.min(...points.map(p => p.value)), high = Math.max(...points.map(p => p.value));
  const spread = high - low || Math.max(Math.abs(high) * .05, 1);
  const min = low - spread * .1, max = high + spread * .1;
  const start = Date.parse(first.date), end = Date.parse(last.date);
  const gaps = points.slice(1).map((point, i) => Date.parse(point.date) - Date.parse(points[i].date)).filter(gap => gap > 0).sort((a, b) => a - b);
  const usualGap = gaps.length ? gaps[Math.floor((gaps.length - 1) / 2)] : 86_400_000;
  const segments: AiHistoryPoint[][] = [];
  for (const point of points) {
    const current = segments.at(-1);
    if (!current || Date.parse(point.date) - Date.parse(current.at(-1)!.date) > usualGap * 1.8) segments.push([point]);
    else current.push(point);
  }
  const x = (p: AiHistoryPoint) => 60 + (Date.parse(p.date) - start) / (end - start || 1) * 580;
  const y = (value: number) => 155 - (value - min) / (max - min) * 130;
  const index = Math.min(inspection ?? points.length - 1, points.length - 1);
  const selected = points[index];
  return <figure className={styles.figure}>
    <figcaption><strong>{first.label}</strong><span>{aiObservationDate(selected.date)} · {aiNumber(selected.value)} {first.unit}</span></figcaption>
    {points.length >= 2 ? <div className={styles.chart} role="slider" tabIndex={0} aria-label={`Inspect ${first.label} history`} aria-valuemin={0} aria-valuemax={points.length - 1} aria-valuenow={index} aria-valuetext={`${aiObservationDate(selected.date)}: ${aiNumber(selected.value)} ${selected.unit}`}
      onKeyDown={event => { if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return; event.preventDefault(); setInspection(event.key === "Home" ? 0 : event.key === "End" ? points.length - 1 : Math.max(0, Math.min(points.length - 1, index + (event.key === "ArrowRight" ? 1 : -1)))); }}
      onPointerMove={event => { const rect = event.currentTarget.getBoundingClientRect(); const time = start + Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) * (end - start); setInspection(points.reduce((best, point, i) => Math.abs(Date.parse(point.date) - time) < Math.abs(Date.parse(points[best].date) - time) ? i : best, 0)); }} onPointerLeave={() => setInspection(null)}>
      <svg viewBox="0 0 660 180" role="img" aria-label={`${first.label}, ${first.unit}, ${points.length} observations`}>
        {[min, (min + max) / 2, max].map(tick => <g key={tick}><line x1="60" x2="640" y1={y(tick)} y2={y(tick)} className={styles.grid} /><text x="52" y={y(tick) + 4} textAnchor="end">{aiNumber(tick)}</text></g>)}
        {segments.map((segment, i) => <polyline key={i} points={segment.map(point => `${x(point)},${y(point.value)}`).join(" ")} className={styles.series} />)}
        {points.map((point, i) => <circle key={i} cx={x(point)} cy={y(point.value)} r="2" className={styles.dot} />)}
        <circle cx={x(selected)} cy={y(selected.value)} r="4" className={styles.dot} />
      </svg>
    </div> : <p className={styles.note}>One observation. A trend needs at least two comparable dates.</p>}
    {segments.length > 1 ? <p className={styles.note}>Gaps between reported observations are not interpolated.</p> : null}
    <div className={styles.chartDates}><span>{aiDate(first.date)}</span><span>{first.unit} · {points.length} observations</span><span>{aiDate(last.date)}</span></div>
  </figure>;
}

function SourceDrawer({ indicator, data, close, opener }: { indicator: AiIndicator; data: AiSnapshot; close: () => void; opener: HTMLElement | null }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const dialog = ref.current; dialog?.showModal(); return () => { dialog?.close(); queueMicrotask(() => opener?.focus()); }; }, [opener]);
  const sources = data.sources.filter(source => indicator.source_ids.includes(source.id));
  const [metricIndex, setMetricIndex] = useState(0);
  const metric = indicator.metrics[Math.min(metricIndex, indicator.metrics.length - 1)];
  return <dialog ref={ref} className={styles.drawer} aria-labelledby="ai-source-title" onCancel={event => { event.preventDefault(); close(); }} onClick={event => { if (event.target === event.currentTarget) close(); }}>
    <div className={styles.drawerBody}>
      <header className={styles.heading}><div><span className={styles.eyebrow}>Source evidence · {indicator.id}</span><h2 id="ai-source-title">{indicator.title}</h2></div><button type="button" onClick={close} aria-label="Close source evidence">Close</button></header>
      <p>{indicator.methodology}</p><p className={styles.note}>{indicator.reason}</p>
      <h3>Publisher access and coverage</h3>
      {sources.length ? sources.map(source => <section className={styles.source} key={source.id}><h4><SourceLink url={source.url}>{source.name}</SourceLink></h4><p>{source.status.replaceAll("_", " ")} · {source.reason}</p><dl className={styles.evidence}><div><dt>Checked</dt><dd>{aiDate(source.checked_at)}</dd></div><div><dt>Cadence</dt><dd>{source.cadence}</dd></div><div><dt>Usage rights</dt><dd>{source.license}</dd></div><div><dt>Shared lineage</dt><dd>{source.lineage_group}</dd></div></dl></section>) : <p>Source verification has not been recorded.</p>}
      <h3>Observation vintages</h3>
      {indicator.metrics.length > 1 ? <label className={styles.seriesSelect}>Inspect observation<select value={Math.min(metricIndex, indicator.metrics.length - 1)} onChange={event => setMetricIndex(Number(event.target.value))}>{indicator.metrics.map((item, index) => <option value={index} key={`${item.source_id}-${item.id}-${item.methodology_version}-${item.cohort_version}`}>{item.label} · {item.unit} · {item.cohort_version}</option>)}</select></label> : null}
      {metric ? [metric].map(metric => <section className={styles.source} key={`${metric.source_id}-${metric.id}-${metric.methodology_version}-${metric.cohort_version}`}><h4>{metric.label} · {aiNumber(metric.value)} {metric.unit}</h4><dl className={styles.evidence}>
        <div><dt>Measurement</dt><dd>{metric.measurement}</dd></div><div><dt>Observed period</dt><dd>{aiObservationDate(metric.period_start)} to {aiObservationDate(metric.period_end)}</dd></div><div><dt>Publication time (UTC)</dt><dd>{metric.published_at ?? "Not disclosed; first-seen history only"}</dd></div><div><dt>Fetched time (UTC)</dt><dd>{metric.fetched_at || "Not recorded"}</dd></div><div><dt>Method / cohort version</dt><dd>{metric.methodology_version} / {metric.cohort_version}</dd></div><div><dt>Source lineage</dt><dd>{metric.lineage_group}</dd></div><div><dt>{metric.measurement === "derived" ? "Input-set SHA256" : "Raw snapshot SHA256"}</dt><dd className={styles.hash}>{metric.raw_hash || "Not recorded"}</dd></div><div><dt>Coverage / exclusions / definitions</dt><dd><pre>{Object.keys(metric.metadata ?? {}).length ? JSON.stringify(metric.metadata, null, 2) : "Coverage and exclusions not disclosed"}</pre></dd></div>
      </dl><SourceLink url={metric.source_url}>Original observation</SourceLink></section>) : <p>No verified observations. Missing values are not zero.</p>}
    </div>
  </dialog>;
}

function Indicator({ indicator, sources, inspect }: { indicator: AiIndicator; sources: AiSource[]; inspect: (opener: HTMLButtonElement) => void }) {
  const groups = useMemo(() => historyGroups(indicator.history), [indicator.history]);
  const [seriesIndex, setSeriesIndex] = useState(() => Math.max(0, groups.findIndex(points => points[0].series_id === indicator.metrics[0]?.id || points[0].series_id === `${indicator.metrics[0]?.id}:${indicator.metrics[0]?.methodology_version}:${indicator.metrics[0]?.cohort_version}`)));
  const selectedGroup = groups[Math.min(seriesIndex, groups.length - 1)];
  const metrics = indicator.metrics.slice(0, 6);
  return <article className={styles.indicator} data-testid={`ai-indicator-${indicator.id}`}>
    <header className={styles.heading}><div><span className={styles.eyebrow}>{indicator.id} · {indicator.priority}</span><h3>{indicator.title}</h3></div><span className={styles.status} data-status={indicator.status}>{indicator.status.replaceAll("_", " ")}</span></header>
    <p className={styles.note}>{indicator.reason}</p>
    <div className={styles.sourceLabels} aria-label={`${indicator.title} data sources`}>
      {sources.map(source => <div className={styles.sourceLabel} data-testid={`ai-source-${source.id}`} key={source.id}>
        <span className={styles.eyebrow}>Source</span>
        <SourceLink url={source.url}>{source.name}</SourceLink>
        <span className={styles.sourceState} data-status={source.status}>{source.status.replaceAll("_", " ")}</span>
        <small>{source.observation_count > 0 && source.observed_from && source.observed_through ? `${aiDate(source.observed_from)} to ${aiDate(source.observed_through)} · ${source.observation_count.toLocaleString("en-US")} observations` : "No collected observations"}</small>
      </div>)}
    </div>
    {indicator.metrics.length ? <dl className={styles.metrics}>{metrics.map(metric => <div key={`${metric.source_id}-${metric.id}-${metric.methodology_version}-${metric.cohort_version}`}><dt>{metric.label}</dt><dd>{aiNumber(metric.value)} <small>{metric.unit}</small></dd><span>{metric.measurement} · {aiObservationDate(metric.period_end)}</span></div>)}</dl> : <p className={styles.empty}>No verified observations. Collection or source verification is incomplete.</p>}
    {groups.length > 1 ? <label className={styles.seriesSelect}>Observation series<select value={Math.min(seriesIndex, groups.length - 1)} onChange={event => setSeriesIndex(Number(event.target.value))}>{groups.map((points, index) => <option key={`${points[0].source_id}-${points[0].series_id}-${points[0].unit}`} value={index}>{points[0].label} · {points[0].unit}</option>)}</select></label> : null}
    {selectedGroup ? <AiSeriesChart key={`${selectedGroup[0].source_id}-${selectedGroup[0].series_id}-${selectedGroup[0].unit}`} points={selectedGroup} /> : null}
    <footer className={styles.indicatorFooter}><button type="button" onClick={event => inspect(event.currentTarget)} aria-label={`Sources and method: ${indicator.title}`}>Sources &amp; method ↗</button><span className={styles.tickers}>{indicator.tickers.map(ticker => <Link key={ticker} href={`/${encodeURIComponent(ticker)}?deck=i`} prefetch={false}>{ticker}</Link>)}</span></footer>
  </article>;
}

export function AiInfrastructureView({ data, error, loading, refresh }: { data: AiSnapshot | null; error: string | null; loading: boolean; refresh: () => void }) {
  const [pane, setPane] = useState<AiPane>("demand");
  const sourceOpener = useRef<HTMLElement | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = data?.indicators.find(indicator => indicator.id === selectedId) ?? null;
  const [legacy, setLegacy] = useState(false);
  useEffect(() => { const value = new URLSearchParams(window.location.search).get("pane"); if (AI_PANES.some(p => p.id === value)) setPane(value as AiPane); }, []);
  const active = AI_PANES.find(p => p.id === pane)!;
  const indicators = data?.indicators.filter(indicator => indicator.pane === pane) ?? [];
  const available = data?.indicators.filter(indicator => indicator.status === "available").length ?? 0;
  return <section className={styles.workspace} data-testid="ai-infrastructure-panel" aria-busy={loading}>
    <header className={styles.heading}><div><span className={styles.eyebrow}>Research / market structure</span><h1>AI infrastructure</h1><p className={styles.subtitle}>Activity, capacity and cash returns. Follow the evidence across the buildout.</p></div><button type="button" onClick={refresh} disabled={loading}>{loading ? "Loading…" : "Refresh snapshot"}</button></header>
    {error ? <div className={styles.error} role="alert"><strong>Snapshot could not be refreshed</strong><p>{error}</p>{data ? <p>Previous snapshot remains visible below. Do not treat it as current evidence.</p> : null}</div> : null}
    <div className={styles.evidenceBar}><div><span className={styles.eyebrow}>Experimental research state</span><strong>{error || !data || !available || data.shadow.state === "insufficient_evidence" ? "Insufficient evidence" : data.shadow.state === "watch" ? "Research watch" : "No active shadow watch"}</strong><p>{data?.shadow.reason ?? "Verified source observations are required before evaluating the cycle."}</p></div><dl><div><dt>Available indicators</dt><dd>{available} / {data?.indicators.length ?? 0}</dd></div><div><dt>Snapshot as of</dt><dd>{aiObservationDate(data?.as_of)}</dd></div><div><dt>Eligible weeks</dt><dd>{data?.shadow.eligible_weeks ?? 0}</dd></div></dl></div>
    {data?.sources.length ? <section className={styles.coverage} data-testid="ai-source-coverage" aria-label="Source coverage">
      <h2>Source coverage</h2>
      <p className={styles.note}>Every configured publisher. Missing observations stay empty; they are not zero.</p>
      <div className={styles.coverageGrid}>{data.sources.map(source => <div className={styles.sourceLabel} data-testid={`ai-coverage-${source.id}`} key={source.id}>
        <span className={styles.eyebrow}>Source</span>
        <SourceLink url={source.url}>{source.name}</SourceLink>
        <span className={styles.sourceState} data-status={source.status}>{source.status.replaceAll("_", " ")}</span>
        <small>{source.observation_count > 0 && source.observed_from && source.observed_through ? `${aiDate(source.observed_from)} to ${aiDate(source.observed_through)} · ${source.observation_count.toLocaleString("en-US")} observations` : source.reason || "No collected observations"}</small>
      </div>)}</div>
    </section> : null}
    <div className={styles.tabs} role="tablist" aria-label="AI infrastructure views">{AI_PANES.map((item, i) => <button key={item.id} id={`ai-tab-${item.id}`} role="tab" type="button" tabIndex={item.id === pane ? 0 : -1} aria-selected={item.id === pane} aria-controls={`ai-pane-${item.id}`} onClick={() => setPane(item.id)} onKeyDown={event => { if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return; event.preventDefault(); const next = event.key === "Home" ? 0 : event.key === "End" ? 3 : (i + (event.key === "ArrowRight" ? 1 : 3)) % 4; setPane(AI_PANES[next].id); document.getElementById(`ai-tab-${AI_PANES[next].id}`)?.focus(); }}>{item.title}</button>)}</div>
    <div id={`ai-pane-${pane}`} role="tabpanel" aria-labelledby={`ai-tab-${pane}`}><div className={styles.paneIntro}><h2>{active.question}</h2><p>{active.note}</p></div>{indicators.length ? indicators.map(indicator => <Indicator key={indicator.id} indicator={indicator} sources={(data?.sources ?? []).filter(source => indicator.source_ids.includes(source.id))} inspect={opener => { sourceOpener.current = opener; setSelectedId(indicator.id); }} />) : <p className={styles.empty}>{loading ? "Retrieving source observations…" : "No verified observations in this view. Source coverage is not established."}</p>}</div>
    <aside className={styles.guardrail}><strong>Research context, not a trade signal</strong><p>Signal → structure → Kelly math → decision. Review institutional flow, next-day OI, event pricing and current portfolio risk in ticker research. This context supplies neither an edge gate nor a Kelly probability.</p></aside>
    <details className={styles.legacy} onToggle={event => setLegacy(event.currentTarget.open)}><summary>Legacy inference price series · methodology v1</summary>{legacy ? <LlmTokenIndexCard /> : null}</details>
    {selected && data ? <SourceDrawer opener={sourceOpener.current} indicator={selected} data={data} close={() => setSelectedId(null)} /> : null}
  </section>;
}
export default function AiInfrastructurePanel() { return <AiInfrastructureView {...useAiInfrastructure()} />; }

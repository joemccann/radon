"use client";
import RequestError from "@/components/RequestError";

import { aiMetricChartPoint, aiMetricDescription, aiMetricDisplay, aiMetricLabel } from "@/lib/aiMetricPresentation";
import InfoTooltip from "./InfoTooltip";
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { aiDate, aiObservationDate, historyGroups, sourceHref, type AiHistoryPoint, type AiIndicator, type AiSnapshot, type AiSource } from "@/lib/aiInfrastructure";
import { useAiInfrastructure } from "@/lib/useAiInfrastructure";
import LlmTokenIndexCard from "./LlmTokenIndexCard";
import AiIndustryHistoryChart from "./AiIndustryHistoryChart";
import { AI_INDUSTRY_STAGES, industryStage, measureCopy } from "@/lib/aiIndustryPresentation";
import styles from "./AiInfrastructure.module.css";

function SourceLink({ url, children }: { url: string; children: React.ReactNode }) {
  const href = sourceHref(url);
  return href ? <a href={href} target="_blank" rel="noopener noreferrer">{children} ↗</a> : <span>{children} · link unavailable</span>;
}

export function AiSeriesChart({ points }: { points: AiHistoryPoint[] }) {
  return <AiIndustryHistoryChart points={points} />;
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
      {indicator.metrics.length > 1 ? <label className={styles.seriesSelect}>Inspect observation<select value={Math.min(metricIndex, indicator.metrics.length - 1)} onChange={event => setMetricIndex(Number(event.target.value))}>{indicator.metrics.map((item, index) => <option value={index} key={`${item.source_id}-${item.id}-${item.methodology_version}-${item.cohort_version}`}>{aiMetricLabel(item)} · {aiMetricDisplay(item).unit} · {data.sources.find(source => source.id === item.source_id)?.name ?? item.source_id}</option>)}</select></label> : null}
      {metric ? [metric].map(metric => <section className={styles.source} key={`${metric.source_id}-${metric.id}-${metric.methodology_version}-${metric.cohort_version}`}><h4>{aiMetricLabel(metric)} · {aiMetricDisplay(metric).value} {aiMetricDisplay(metric).unit}</h4><dl className={styles.evidence}>
        <div><dt>Measurement</dt><dd>{metric.measurement}</dd></div><div><dt>Observed period</dt><dd>{aiObservationDate(metric.period_start)} to {aiObservationDate(metric.period_end)}</dd></div><div><dt>Publication time (UTC)</dt><dd>{metric.published_at ?? "Not disclosed; first-seen history only"}</dd></div><div><dt>Fetched time (UTC)</dt><dd>{metric.fetched_at || "Not recorded"}</dd></div><div><dt>Method / cohort version</dt><dd>{metric.methodology_version} / {metric.cohort_version}</dd></div><div><dt>Source lineage</dt><dd>{metric.lineage_group}</dd></div><div><dt>{metric.measurement === "derived" ? "Input-set SHA256" : "Raw snapshot SHA256"}</dt><dd className={styles.hash}>{metric.raw_hash || "Not recorded"}</dd></div><div><dt>Coverage / exclusions / definitions</dt><dd><pre>{Object.keys(metric.metadata ?? {}).length ? JSON.stringify(metric.metadata, null, 2) : "Coverage and exclusions not disclosed"}</pre></dd></div>
      </dl><SourceLink url={metric.source_url}>Original observation</SourceLink></section>) : <p>No verified observations. Missing values are not zero.</p>}
    </div>
  </dialog>;
}

function SourceCard({ source, coverage = false }: { source: AiSource; coverage?: boolean }) {
  return <div className={styles.sourceLabel} data-testid={`ai-${coverage ? "coverage" : "source"}-${source.id}`}>
    <SourceLink url={source.url}>{source.name}</SourceLink>
    <span className={styles.sourceState} data-status={source.status}>{source.status.replaceAll("_", " ")}</span>
    <small>{source.observation_count > 0 ? `${source.observation_count.toLocaleString("en-US")} observations · ${aiDate(source.observed_from)} to ${aiDate(source.observed_through)}` : "No collected observations"}</small>
    <small>{source.cadence} · checked {aiObservationDate(source.checked_at)}</small>
    {coverage ? <small>{source.reason}</small> : null}
  </div>;
}

function MeasureExplanation({ indicator, sources, inspect, select }: { indicator: AiIndicator; sources: AiSource[]; inspect: (opener: HTMLButtonElement) => void; select?: () => void }) {
  const copy = measureCopy(indicator);
  return <details className={styles.measure} data-testid={`ai-indicator-${indicator.id}`}>
    <summary><span>{copy.name}</span><span className={styles.measureMeta}>{indicator.id} · {indicator.status.replaceAll("_", " ")}<span className={styles.expand} aria-hidden="true">+</span></span></summary>
    <div className={styles.explanation}>
      <p className={styles.measureDefinition}>{copy.definition}</p>
      <h4>Why it matters</h4><p>{copy.relevance}</p>
      <h4>What it cannot prove</h4><p>{copy.limit}</p>
      <p className={styles.note}>{indicator.reason}</p>
      <div className={styles.sourceLabels}>{sources.map(source => <SourceCard key={source.id} source={source} />)}</div>
      <div className={styles.explanationActions}>{select ? <button type="button" onClick={select}>Explore measure</button> : <Link href="/research-workbench">Open research workbench</Link>}<button type="button" onClick={event => inspect(event.currentTarget)} aria-label={`Sources and method: ${indicator.title}`}>Sources &amp; methodology</button></div>
    </div>
  </details>;
}

function SelectedMeasure({ indicator, sources, inspect }: { indicator: AiIndicator; sources: AiSource[]; inspect: (opener: HTMLButtonElement) => void }) {
  const groups = useMemo(() => historyGroups(indicator.history).map(group => group.map(aiMetricChartPoint)), [indicator.history]);
  const [seriesIndex, setSeriesIndex] = useState(0);
  const points = groups[Math.min(seriesIndex, groups.length - 1)];
  const source = sources.find(item => item.id === points?.[0].source_id);
  const copy = measureCopy(indicator);
  return <article className={styles.selectedMeasure} aria-label={`${copy.name} evidence`}>
    <header className={styles.heading}><div><span className={styles.eyebrow}>{indicator.id} · {indicator.title}</span><h3>{copy.name}</h3></div><span className={styles.status} data-status={indicator.status}>{indicator.status.replaceAll("_", " ")}</span></header>
    <p className={styles.reading}>{copy.definition}</p>
    <p className={styles.note}>{indicator.reason}</p>
    {groups.length ? <label className={styles.seriesSelect}>Observation series<select value={Math.min(seriesIndex, groups.length - 1)} onChange={event => setSeriesIndex(Number(event.target.value))}>{groups.map((group, index) => <option key={`${group[0].source_id}-${group[0].series_id}-${group[0].unit}`} value={index}>{aiMetricLabel(group[0])} · {aiMetricDisplay(group[0]).unit} · {sources.find(item => item.id === group[0].source_id)?.name ?? group[0].source_id}</option>)}</select></label> : null}
    {points ? <AiIndustryHistoryChart key={`${points[0].source_id}-${points[0].series_id}-${points[0].unit}`} points={points} cadence={source?.cadence} sourceLabel={source?.name} /> : <p className={styles.empty}>No comparable history is available. Missing observations are not zero.</p>}
    <div className={styles.interpretation}><div><h4>Why it matters</h4><p>{copy.relevance}</p></div><div><h4>What it cannot prove</h4><p>{copy.limit}</p></div></div>
    {indicator.metrics.length ? <details className={styles.observations}><summary>Latest reported observations ({indicator.metrics.length})</summary><dl className={styles.metrics}>{indicator.metrics.map(metric => {
      const label = aiMetricLabel(metric);
      const display = aiMetricDisplay(metric);
      return <div className={styles.observationCard} data-testid="ai-observation-card" key={`${metric.source_id}-${metric.id}-${metric.methodology_version}-${metric.cohort_version}`}>
        <dt className={styles.observationHeading}><span>{label}</span><InfoTooltip prose text={aiMetricDescription(metric)} ariaLabel={`About ${label}`} triggerTestId={`ai-observation-info-${metric.id}`} contentTestId={`ai-observation-description-${metric.id}`} /></dt>
        <dd>{display.value} <small>{display.unit}</small></dd>
        <dd className={styles.observationMeta}>{metric.measurement} · {aiObservationDate(metric.period_end)}</dd>
      </div>;
    })}</dl></details> : <p className={styles.empty}>No verified observations. Collection or source verification is incomplete.</p>}
    <footer className={styles.indicatorFooter}><button type="button" onClick={event => inspect(event.currentTarget)} aria-label={`Sources and method: ${indicator.title}`}>Sources &amp; methodology</button><span className={styles.tickers}>{indicator.tickers.map(ticker => <Link key={ticker} href={`/${encodeURIComponent(ticker)}?deck=i`} prefetch={false}>{ticker}</Link>)}</span></footer>
  </article>;
}

export function AiInfrastructureView({ data, error, loading, refresh }: { data: AiSnapshot | null; error: string | null; loading: boolean; refresh: () => void }) {
  const [stage, setStage] = useState("adoption");
  const [measureId, setMeasureId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const sourceOpener = useRef<HTMLElement | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [legacy, setLegacy] = useState(false);
  const selected = data?.indicators.find(indicator => indicator.id === selectedId) ?? null;
  useEffect(() => { const value = new URLSearchParams(window.location.search).get("pane"); const previous: Record<string, string> = { demand: "adoption", compute: "compute", delivery: "buildout", finance: "funding" }; if (value && previous[value]) setStage(previous[value]); }, []);
  const active = AI_INDUSTRY_STAGES.find(item => item.id === stage) ?? { id: "additional", name: "Additional evidence", question: "What else is being measured?", watch: "Review coverage and methodology before interpreting new measures.", main: "" };
  const indicators = data?.indicators.filter(indicator => industryStage(indicator) === stage) ?? [];
  const current = indicators.find(indicator => indicator.id === measureId) ?? indicators.find(indicator => indicator.id === active.main) ?? indicators[0];
  const available = data?.indicators.filter(indicator => indicator.status === "available").length ?? 0;
  const sources = data?.sources ?? [];
  const filteredSources = sources.filter(source => `${source.name} ${source.status} ${source.reason}`.toLowerCase().includes(query.trim().toLowerCase()));
  const inspect = (id: string, opener: HTMLButtonElement) => { sourceOpener.current = opener; setSelectedId(id); };
  const chooseStage = (id: string) => { setStage(id); setMeasureId(null); };
  const stageTabs = AI_INDUSTRY_STAGES.slice(0, 4);
  return <section className={styles.workspace} data-testid="ai-infrastructure-panel" aria-busy={loading}>
    <header className={styles.heading}><div><span className={styles.eyebrow}>Industry research</span><h1>AI Industry</h1><p className={styles.subtitle}>Follow the value chain from adoption to cash returns.</p></div><button type="button" onClick={refresh} disabled={loading}>{loading ? "Loading…" : "Refresh snapshot"}</button></header>
    <div className={styles.snapshotMeta}><span>{data?.indicators.length ?? 0} measures</span><span>{sources.length} sources</span><span>Snapshot {aiObservationDate(data?.as_of)}</span></div>
    {error ? <RequestError error={error} fallback="The AI Industry snapshot could not be refreshed. Try again." retainedData={Boolean(data)} /> : null}
    <div className={styles.stageCards} role="tablist" aria-label="AI Industry value chain">{stageTabs.map((item, i) => <button key={item.id} id={`ai-tab-${item.id}`} role="tab" type="button" aria-selected={stage === item.id} aria-controls="ai-stage-evidence" tabIndex={stage === item.id || ((stage === "capability" || stage === "additional") && i === 0) ? 0 : -1} onClick={() => chooseStage(item.id)} onKeyDown={event => { if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return; event.preventDefault(); const next = event.key === "Home" ? 0 : event.key === "End" ? stageTabs.length - 1 : (i + (event.key === "ArrowRight" ? 1 : stageTabs.length - 1)) % stageTabs.length; chooseStage(stageTabs[next].id); document.getElementById(`ai-tab-${stageTabs[next].id}`)?.focus(); }}><span className={styles.eyebrow}>0{i + 1}</span><strong>{item.short}</strong><span>{item.subtitle}</span></button>)}</div>
    <div className={styles.capability}><span>A separate lens</span><button type="button" aria-pressed={stage === "capability"} onClick={() => chooseStage("capability")}>Model capability</button><span>Task quality is distinct from paid adoption.</span></div>
    <div id="ai-stage-evidence" role={stage === "capability" || stage === "additional" ? "region" : "tabpanel"} aria-label={active.name} aria-labelledby={stage === "capability" || stage === "additional" ? undefined : `ai-tab-${stage}`}>
      <div className={styles.paneIntro}><span className={styles.eyebrow}>{active.name}</span><h2>{active.question}</h2><p>{active.watch}</p></div>
      {indicators.length ? <><label className={styles.seriesSelect}>Measure<select value={current?.id ?? ""} onChange={event => setMeasureId(event.target.value)}>{indicators.map(indicator => <option key={indicator.id} value={indicator.id}>{measureCopy(indicator).name} · {indicator.status.replaceAll("_", " ")}</option>)}</select></label>{current ? <SelectedMeasure key={current.id} indicator={current} sources={sources.filter(source => current.source_ids.includes(source.id))} inspect={opener => inspect(current.id, opener)} /> : null}</> : <p className={styles.empty}>{loading ? "Retrieving source observations…" : "No verified observations in this view. Source coverage is not established."}</p>}
    </div>
    <section className={styles.guide} aria-label="Guide to every measure"><header className={styles.paneIntro}><span className={styles.eyebrow}>Read the evidence</span><h2>What each measure tells you</h2><p>Understand the measurement, its investment relevance and its limits.</p></header>{[...AI_INDUSTRY_STAGES.map(item => ({ id: item.id, name: item.name, question: item.question })), { id: "additional", name: "Additional evidence", question: "What else is being measured?" }].map(group => { const measures = data?.indicators.filter(indicator => industryStage(indicator) === group.id) ?? []; return measures.length ? <section className={styles.guideGroup} key={group.id}><span className={styles.eyebrow}>{group.name}</span><h3>{group.question}</h3><div className={styles.measureGrid}>{measures.map(indicator => <MeasureExplanation key={indicator.id} indicator={indicator} sources={sources.filter(source => indicator.source_ids.includes(source.id))} inspect={opener => inspect(indicator.id, opener)} select={() => { chooseStage(group.id); setMeasureId(indicator.id); document.getElementById("ai-stage-evidence")?.scrollIntoView({ block: "start" }); }} />)}</div></section> : null; })}</section>
    <aside className={styles.marketHandoff}><span className={styles.eyebrow}>Across the value chain · M1</span><h2>What is already priced in?</h2><p className={styles.reading}>Compare this evidence with prices, option volatility, institutional flow and next-day open interest in ticker research. A theme is not measured portfolio exposure.</p><div className={styles.tickers}>{[...new Set(data?.indicators.flatMap(indicator => indicator.tickers) ?? [])].map(ticker => <Link key={ticker} href={`/${encodeURIComponent(ticker)}?deck=i`} prefetch={false}>{ticker}</Link>)}</div>{data?.indicators.filter(indicator => indicator.id === "M1").map(indicator => <MeasureExplanation key={indicator.id} indicator={indicator} sources={sources.filter(source => indicator.source_ids.includes(source.id))} inspect={opener => inspect(indicator.id, opener)} />)}</aside>
    <section className={styles.coverage} data-testid="ai-source-coverage" aria-label="Source coverage"><div className={styles.heading}><div><h2>Source coverage</h2><p className={styles.note}>Every configured publisher. Missing observations are not zero.</p></div><label className={styles.search}>Find a source<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Name, status or coverage" /></label></div><div className={styles.coverageGrid}>{filteredSources.map(source => <SourceCard key={source.id} source={source} coverage />)}</div>{filteredSources.length === 0 ? <p className={styles.empty}>{sources.length ? "No sources match your search." : "Source verification has not been recorded."}</p> : null}</section>
    <details className={styles.researchState}><summary>Experimental research state · {error || !data || !available || data.shadow.state === "insufficient_evidence" ? "Insufficient evidence" : data.shadow.state === "watch" ? "Research watch" : "No active shadow watch"}</summary><p>{data?.shadow.reason ?? "Verified source observations are required before evaluating the cycle."}</p><p>{available} / {data?.indicators.length ?? 0} available measures · {data?.shadow.eligible_weeks ?? 0} eligible weeks</p></details>
    <aside className={styles.guardrail}><strong>Research context, not a trade signal</strong><p>Signal → structure → Kelly math → decision. Review institutional flow, next-day OI, event pricing and current portfolio risk in ticker research. This context supplies neither an edge gate nor a Kelly probability.</p></aside>
    <details className={styles.legacy} onToggle={event => setLegacy(event.currentTarget.open)}><summary>Legacy inference price series · methodology v1</summary>{legacy ? <LlmTokenIndexCard /> : null}</details>
    {selected && data ? <SourceDrawer opener={sourceOpener.current} indicator={selected} data={data} close={() => setSelectedId(null)} /> : null}
  </section>;
}
export default function AiInfrastructurePanel() { return <AiInfrastructureView {...useAiInfrastructure()} />; }

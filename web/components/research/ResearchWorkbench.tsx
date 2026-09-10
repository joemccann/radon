"use client";

import { useMemo, useRef, useState, type FormEvent } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useNewsfeedPosts, type NormalisedPost } from "@/lib/useNewsfeedPosts";
import {
  EMPTY_RESEARCH_WORKSPACE, analyzeResearch, createSourceDocument, extractDocumentFacts,
  parseResearchWorkspace, researchCalendarIcs, researchMarkdown, buildResearchPrompt, importResearchTextFile,
  type ResearchWorkspace, type ResearchFact, type SourceDocument, type SourceKind,
} from "@/lib/researchWorkbench";
import type { PortfolioData } from "@/lib/types";
import SortTh from "@/components/SortTh";
import { useSort } from "@/lib/useSort";
import { applyResearchChecklist } from "@/lib/researchWorkbench/handoff";
import { emitAsk } from "@/lib/agent/askBus";
import ResearchControls from "./ResearchControls";
import styles from "./ResearchWorkbench.module.css";

const ResearchLabs = dynamic(() => import("./ResearchLabs"), { loading: () => <p role="status">Loading valuation tools…</p> });
const TABS = ["Brief", "Fundamentals", "Calendar", "Deals", "Signals", "AI infrastructure", "Labs & exports", "Controls"] as const;
type Tab = typeof TABS[number];
const KINDS: SourceKind[] = ["filing", "earnings", "desk-note", "deal", "transcript", "other"];
export function downloadResearchFile(content: string, filename: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename;
  document.body.appendChild(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function Empty({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className={styles.empty}><h2>{title}</h2><div className={styles.muted}>{children}</div></div>;
}
function Citation({ fact, sources, onInspect }: { fact: ResearchFact; sources: SourceDocument[]; onInspect: (id: string) => void }) {
  const source = sources.find((doc) => doc.id === fact.documentId);
  return <details className={styles.fact}><summary>Source: {source?.title ?? "Missing source"} · {source?.publishedAt}</summary>
    <blockquote>{fact.quote}</blockquote><span className={styles.meta}>Characters {fact.start + 1}–{fact.end} · {source?.kind}</span>
    <div className={styles.actions}><button className={styles.button} onClick={() => onInspect(fact.documentId)}>Inspect document</button>
      {source?.url ? <a href={source.url} target="_blank" rel="noopener noreferrer">Open original source</a> : <span className={styles.meta}>Operator supplied text; original URL not supplied.</span>}</div>
  </details>;
}
function SourceForm({ onAdd }: { onAdd: (document: SourceDocument) => void }) {
  const [kind, setKind] = useState<SourceKind>("filing");
  const [filename, setFilename] = useState<string | null>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const [error, setError] = useState<string | null>(null);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const fields = new FormData(form);
    try {
      const metadata = { id: crypto.randomUUID(), title: String(fields.get("title")), url: String(fields.get("url") ?? "") || null,
        publishedAt: String(fields.get("publishedAt")), ticker: String(fields.get("ticker") ?? "").trim().toUpperCase() || null, kind };
      onAdd(filename ? importResearchTextFile(filename, String(fields.get("text")), metadata) : createSourceDocument({ ...metadata, text: fields.get("text") }));
      form.reset(); setFilename(null); setError(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Source could not be imported."); }
  }
  return <form onSubmit={submit} className={styles.form}>
    <label className={styles.field}>Document title<input name="title" required maxLength={500} autoComplete="off" /></label>
    <div className={styles.pair}><label className={styles.field}>Ticker (optional)<input name="ticker" maxLength={15} autoComplete="off" /></label>
      <label className={styles.field}>Published<input name="publishedAt" type="date" required /></label></div>
    <label className={styles.field}>Source type<select value={kind} onChange={(event) => setKind(event.target.value as SourceKind)}>{KINDS.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
    <label className={styles.field}>Original HTTPS URL (optional)<input name="url" type="url" placeholder="https://" /></label>
    <label className={styles.field}>Plaintext file (optional)<input type="file" accept=".txt,.md,.eml,text/plain" onChange={async (event) => {
      const file = event.target.files?.[0]; if (!file) { setFilename(null); return; }
      try { if (file.size > 1_000_000) throw new Error("Source file exceeds 1 MB."); const text = await file.text(); if (textRef.current) textRef.current.value = text; setFilename(file.name); setError(null); }
      catch (reason) { setError(reason instanceof Error ? reason.message : "File could not be read."); }
    }} /></label>
    <label className={styles.field}>Source text<textarea ref={textRef} name="text" rows={8} required maxLength={250000} placeholder="Paste the exact filing, transcript, desk note, or deal email passage." /></label>
    <span className={styles.meta}>Text stays in this page until you export it. Reloading clears this workspace. Evidence exports contain sources and facts; model assumptions and the analysis date stay on this page.</span>
    {error ? <p role="alert" className={styles.error}>{error}</p> : null}
    <button className={styles.primary} type="submit">Add source</button>
  </form>;
}
function FeedSources({ onAdd }: { onAdd: (document: SourceDocument) => void }) {
  const { posts, loading, error, refresh } = useNewsfeedPosts();
  const [message, setMessage] = useState<string | null>(null);
  const candidates = posts.filter((post) => post.content?.trim()).slice(0, 30);
  function add(post: NormalisedPost) {
    try {
      onAdd(createSourceDocument({ id: `feed-${post.id}`, title: `Feed summary: ${post.title}`, url: post.href,
        publishedAt: post.source?.documentDate ?? post.isoTimestamp.slice(0, 10), kind: "desk-note", ticker: null, text: post.content }));
      setMessage(`Imported ${post.title}. Feed summaries retain their source attribution; verify against the original document.`);
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : "Feed source could not be imported."); }
  }
  return <div>{loading ? <p role="status">Loading research feed…</p> : null}
    {error ? <p className={styles.error}>Feed unavailable: {error} <button className={styles.button} onClick={() => void refresh()}>Retry feed</button></p> : null}
    {!loading && candidates.length === 0 ? <p className={styles.muted}>No textual notes are available in the current feed. Add a source passage above.</p> : null}
    <ul className={styles.sourceList}>{candidates.map((post) => <li key={post.id}><span>{post.title}</span><span className={styles.meta}>{post.source?.publisher ?? "Market Ear"} · {post.isoTimestamp.slice(0, 10)}</span><button className={styles.button} onClick={() => add(post)}>Import note</button></li>)}</ul>
    {message ? <p role="status">{message}</p> : null}</div>;
}
function Fundamentals({ workspace, onInspect, asOf }: { workspace: ResearchWorkspace; onInspect: (id: string) => void; asOf: string }) {
  const available = new Set(workspace.documents.filter((source) => source.publishedAt <= asOf).map((source) => source.id));
  const rows = workspace.facts.filter((fact) => fact.kind === "metric" && available.has(fact.documentId));
  type Key = "ticker" | "label" | "period" | "value" | "unit" | "source";
  const { sorted, sort, toggle } = useSort<ResearchFact, Key>(rows, (row, key) => key === "source" ? workspace.documents.find((source) => source.id === row.documentId)?.title : row[key], "period", "desc");
  if (!rows.length) return <Empty title="Normalize the figures you can trace"><p>Add financial facts with an exact passage, period, signed value, and currency scale. Adjusted EBITDA appears only with a complete reconciliation.</p></Empty>;
  return <><h2>Reported fundamentals</h2><p className={styles.muted}>Periods and currency scales remain distinct. Every value links to its source passage.</p><div className={styles.tableWrap}><table><thead><tr>
    {([['ticker', 'Ticker'], ['label', 'Metric'], ['period', 'Period'], ['value', 'Value'], ['unit', 'Unit']] as const).map(([key, label]) => <SortTh key={key} label={label} sortKey={key} activeKey={sort.key} direction={sort.direction} onToggle={toggle} />)}
    <SortTh label="Source" sortKey="source" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
  </tr></thead><tbody>{sorted.map((fact) => <tr key={fact.id}><td>{fact.ticker ?? "Unassigned"}</td><td>{fact.label}</td><td>{fact.period}</td><td>{fact.value?.toLocaleString("en-US", { maximumFractionDigits: 4 })}</td><td>{fact.unit}</td><td><Citation fact={fact} sources={workspace.documents} onInspect={onInspect} /></td></tr>)}</tbody></table></div></>;
}

export default function ResearchWorkbench({ portfolio }: { portfolio?: PortfolioData | null }) {
  const [workspace, setWorkspace] = useState<ResearchWorkspace>(EMPTY_RESEARCH_WORKSPACE);
  const [tab, setTab] = useState<Tab>("Brief");
  const [visited, setVisited] = useState<Set<Tab>>(() => new Set(["Brief"]));
  const [asOf, setAsOf] = useState(() => new Date().toISOString().slice(0, 10));
  const [selected, setSelected] = useState<string | null>(null);
  const [showFeed, setShowFeed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const sourceRef = useRef<HTMLElement>(null);
  const analysis = useMemo(() => analyzeResearch(workspace, asOf), [workspace, asOf]);
  const current = workspace.documents.find((document) => document.id === selected);
  const cite = (fact: ResearchFact) => <Citation key={fact.id} fact={fact} sources={workspace.documents} onInspect={inspect} />;
  function inspect(id: string) { setSelected(id); setTimeout(() => sourceRef.current?.scrollIntoView({ behavior: "auto", block: "start" }), 0); }
  function add(document: SourceDocument) {
    if (workspace.documents.some((source) => source.id === document.id)) throw new Error("This source is already in the workspace.");
    const next = parseResearchWorkspace({ ...workspace, documents: [...workspace.documents, document], facts: [...workspace.facts, ...extractDocumentFacts(document)] });
    setWorkspace(next); setSelected(document.id); setNotice(`Added ${document.title}. ${next.facts.length - workspace.facts.length} cited facts extracted.`); setError(null);
  }
  async function importFile(file?: File) {
    if (!file) return;
    try { if (file.size > 8_000_000) throw new Error("Workspace file exceeds 8 MB.");
      const incoming = parseResearchWorkspace(JSON.parse(await file.text()));
      const ids = new Set(workspace.documents.map((document) => document.id));
      const documents = incoming.documents.filter((document) => !ids.has(document.id));
      const importedIds = new Set(documents.map((document) => document.id));
      const next = parseResearchWorkspace({ version: 1, documents: [...workspace.documents, ...documents], facts: [...workspace.facts, ...incoming.facts.filter((fact) => importedIds.has(fact.documentId))] });
      setWorkspace(next); setNotice(`Imported ${documents.length} sources. Existing sources were preserved.`); setError(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Workspace could not be imported."); }
    if (fileRef.current) fileRef.current.value = "";
  }
  function draftWithAssistant() {
    try { emitAsk(buildResearchPrompt(workspace, undefined, asOf)); setError(null); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Research prompt could not be prepared."); }
  }
  const download = (kind: "json" | "md" | "ics") => {
    try { const content = kind === "json" ? JSON.stringify(workspace, null, 2) : kind === "md" ? researchMarkdown(workspace, asOf) : researchCalendarIcs(workspace, asOf);
      downloadResearchFile(content, `radon-research-${asOf}.${kind}`, kind === "json" ? "application/json" : kind === "ics" ? "text/calendar" : "text/markdown");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Export failed."); }
  };
  return <div className={styles.workbench} data-testid="research-workbench">
    <header className={styles.header}><div><h2>Evidence to decision</h2><p>Trace the claim. Compare the periods. Review the trade.</p><span className={styles.meta}>{workspace.documents.length} sources · {workspace.facts.length} cited facts · Unsaved evidence</span></div>
      <div className={styles.actions}><button className={styles.button} onClick={() => fileRef.current?.click()}>Import evidence</button><button className={styles.button} disabled={!workspace.documents.length} onClick={() => download("json")}>Save evidence</button></div>
      <input ref={fileRef} type="file" accept="application/json,.json" hidden aria-label="Import workspace JSON" onChange={(event) => void importFile(event.target.files?.[0])} />
    </header>
    {error ? <p className={`${styles.status} ${styles.error}`} role="alert">{error}</p> : null}
    {notice ? <p className={styles.status} role="status">{notice}</p> : null}
    <div className={styles.layout}><aside className={styles.sources} aria-label="Research sources"><h2>Evidence</h2>
      <details open={!workspace.documents.length}><summary>Add source text</summary><SourceForm onAdd={add} /></details>
      <details onToggle={(event) => setShowFeed(event.currentTarget.open)}><summary>Import from Radon newsfeed</summary>{showFeed ? <FeedSources onAdd={add} /> : null}</details>
      <label className={styles.field}>Analysis date<input aria-label="Analysis date" type="date" value={asOf} onChange={(event) => { if (/^\d{4}-\d{2}-\d{2}$/.test(event.target.value)) setAsOf(event.target.value); }} /></label>
      <ul className={styles.sourceList}>{workspace.documents.map((document) => <li key={document.id}><button className={styles.sourceTitle} onClick={() => inspect(document.id)}>{document.title}</button><span className={styles.meta}>{document.ticker ?? "No ticker"} · {document.publishedAt} · {document.kind}</span></li>)}</ul>
    </aside><div className={styles.content}><nav aria-label="Research views" className={styles.tabs}>{TABS.map((item) => <button key={item} className={styles.tab} aria-pressed={tab === item} onClick={() => { setTab(item); setVisited((previous) => new Set([...previous, item])); }}>{item}</button>)}</nav>
      {analysis.warnings.length ? <details className={styles.status}><summary>{analysis.warnings.length} evidence conditions</summary><ul>{analysis.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></details> : null}
      {tab === "Brief" ? <>{analysis.briefs.length ? <><div className={styles.sectionHead}><div><h2>Cited research brief</h2><p className={styles.muted}>Extracted passages, with document dates and character-level citations. Text alignment does not verify publisher authenticity.</p></div><div className={styles.actions}><button className={styles.button} onClick={draftWithAssistant}>Draft with assistant</button><button className={styles.button} onClick={() => download("md")}>Export readable research</button></div></div>{analysis.briefs.map((brief) => <article className={styles.fact} key={brief.id}><span className={styles.meta}>{brief.ticker ?? "Research"} · {brief.publishedAt}</span><h3>{brief.title}</h3><p>{brief.text}</p>{brief.citations.map(cite)}</article>)}<TradeReview workspace={workspace} asOf={asOf} /></> : <Empty title="Start with a source you trust"><p>Add a filing, earnings transcript, desk note, or deal email. Or import an existing Radon newsfeed note.</p><ol><li>Keep the original date and exact passage.</li><li>Annotate figures and dated events in the document inspector.</li><li>Inspect citations before creating a model or reviewing a trade.</li></ol><p>Premium data is available only through your existing licensed sources. Use “Draft with assistant” to send selected research to your configured assistant.</p></Empty>}</> : null}
      {tab === "Fundamentals" ? <><Fundamentals workspace={workspace} onInspect={inspect} asOf={asOf} />{analysis.fundamentals.map((period) => <article className={styles.fact} key={`${period.ticker}-${period.period}-${period.unit}`}><h3>{period.ticker} · {period.period} · {period.unit}</h3><p>Reconciled adjusted EBITDA: {period.adjustedEbitda === null ? "Unavailable" : period.adjustedEbitda.toLocaleString("en-US")}</p>{period.warnings.map((warning) => <p className={styles.muted} key={warning}>{warning}</p>)}{period.reconciliation.map(cite)}</article>)}</> : null}
      {tab === "Calendar" ? <><div className={styles.sectionHead}><div><h2>Earnings & blackout calendar</h2><p className={styles.muted}>Source-stated dates only. A generic earnings date does not establish a company buyback blackout.</p></div><button className={styles.button} disabled={!analysis.calendar.length} onClick={() => download("ics")}>Export calendar holds</button></div>{analysis.calendar.length ? analysis.calendar.map((hold) => <article className={styles.fact} key={hold.id}><span className={styles.meta}>{hold.status} · {hold.basis}</span><h3>{hold.ticker} {hold.title}</h3><p className={styles.factValue}>{hold.startDate}{hold.endDate !== hold.startDate ? ` to ${hold.endDate}` : ""}</p>{cite(hold.citation)}</article>) : <Empty title="No supported calendar holds"><p>Annotate an exact earnings date or a passage that explicitly states both blackout boundaries. Estimated windows remain outside the calendar.</p></Empty>}</> : null}
      {tab === "Deals" ? <><h2>Private-company diligence</h2><p className={styles.muted}>Funding and investor claims retain the source text. Missing valuation and comparable-company evidence stay unresolved.</p>{analysis.deals.length ? analysis.deals.map((deal) => <article className={styles.fact} key={deal.id}><h3>{deal.company}</h3><p>{deal.stage ?? "Stage unavailable"} · {deal.amount === null ? "Funding amount unavailable" : `${deal.amount.toLocaleString("en-US")} ${deal.unit}`}</p><p>Investors: {deal.investors.join(", ") || "Unverified"}</p><h3>Diligence questions</h3><ul>{deal.diligence.map((item) => <li key={item}>{item}</li>)}</ul>{deal.citations.map(cite)}</article>) : <Empty title="No sourced deals yet"><p>Paste a deal email or authorized DocSend text, then annotate the company, round, investors, and amount. URL access and inbox ingestion are not connected.</p></Empty>}</> : null}
      {tab === "Signals" ? <><h2>Transcript signal changes</h2><p className={styles.muted}>Guidance, capex, buybacks, and AI spending. Keyword candidates require review with your configured assistant. Changes require comparable cited periods and units.</p>{analysis.themes.length ? analysis.themes.map((theme) => <article className={styles.fact} key={theme.id}><h3>{theme.ticker} · {theme.theme}</h3><p>{theme.interpretation}</p>{theme.delta !== null ? <p className={styles.factValue}>{theme.delta > 0 ? "+" : ""}{theme.delta.toLocaleString("en-US")} {theme.unit}</p> : null}{cite(theme.current)}{theme.previous ? cite(theme.previous) : null}</article>) : <Empty title="No comparable transcript signals"><p>Import source text and annotate guidance, buyback, capex, or AI-spend figures. A change in language alone does not establish a tradeable edge.</p></Empty>}</> : null}
      {tab === "AI infrastructure" ? <><h2>AI spending in company context</h2><p className={styles.muted}>Company capex and revenue share a period and currency scale. Cross-check market indicators in the existing regime workspace.</p><Link href="/regime/llm">Open AI market indicators</Link>{analysis.aiInfrastructure.length ? analysis.aiInfrastructure.map((row) => <article className={styles.fact} key={`${row.ticker}-${row.period}`}><h3>{row.ticker} · {row.period}</h3><p>Capex / revenue: {row.capexToRevenue === null ? "Unavailable" : `${(row.capexToRevenue * 100).toFixed(1)}%`}</p><p>AI spending: {row.aiSpend ? `${row.aiSpend.value?.toLocaleString("en-US")} ${row.aiSpend.unit}` : "Not separately disclosed in this evidence"}</p>{row.citations.map(cite)}</article>) : <Empty title="No supported spending comparison"><p>Add capex and revenue for a common period. General capex is not relabeled as AI spending.</p></Empty>}</> : null}
      {visited.has("Labs & exports") ? <div hidden={tab !== "Labs & exports"}><ResearchLabs workspace={workspace} analysis={analysis} portfolio={portfolio} asOf={asOf} /></div> : null}
      {visited.has("Controls") ? <div hidden={tab !== "Controls"}><ResearchControls /></div> : null}
      {current ? <section ref={sourceRef} className={styles.sourcePreview} aria-label="Document inspector"><div className={styles.sectionHead}><div><h2>{current.title}</h2><span className={styles.meta}>{current.publishedAt} · {current.kind} · {current.ticker ?? "No ticker"}</span></div><button className={styles.button} onClick={() => setSelected(null)}>Close inspector</button></div><pre>{current.text}</pre><FactAnnotation source={current} onAdd={(fact) => { const next = parseResearchWorkspace({ ...workspace, facts: [...workspace.facts, fact] }); setWorkspace(next); setNotice(`Added cited fact: ${fact.label}.`); }} /></section> : null}
    </div></div>
  </div>;
}

function TradeReview({ workspace, asOf }: { workspace: ResearchWorkspace; asOf: string }) {
  const availableDocuments = workspace.documents.filter((source) => source.publishedAt <= asOf);
  const tickers = [...new Set(availableDocuments.flatMap((document) => document.ticker ? [document.ticker] : []))];
  const [ticker, setTicker] = useState("");
  const [checks, setChecks] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const selectedTicker = tickers.includes(ticker) ? ticker : tickers[0] ?? "";
  const items = ["Review current regime and sector concentration", "Verify source date and blackout exposure", "Run the current evaluation and inspect convexity, edge, and risk gates", ...workspace.facts.filter((fact) => fact.ticker === selectedTicker && availableDocuments.some((document) => document.id === fact.documentId)).slice(0, 5).map((fact) => `Verify ${fact.label}: ${fact.quote.slice(0, 300)}`)];
  const checked = items.every((item) => checks[`${selectedTicker}:${item}`]);
  function apply() {
    try { const sources = availableDocuments.filter((document) => document.ticker === selectedTicker).map((document) => ({ title: document.title, url: document.url ?? "", passage: document.text.slice(0, 500) }));
      const href = applyResearchChecklist(selectedTicker, items, sources); window.location.assign(href);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Checklist could not be applied."); }
  }
  return <section className={styles.review}><h2>Desk note to trade review</h2><p>Carry the source context into the existing ticket. Contract selection, sizing, portfolio coverage, and order confirmation remain part of the ticket review.</p>
    <label className={styles.field}>Review ticker<select value={selectedTicker} disabled={!tickers.length} onChange={(event) => setTicker(event.target.value)}>{tickers.length ? tickers.map((item) => <option key={item}>{item}</option>) : <option value="">Add a source with a ticker</option>}</select></label>
    {items.map((item) => <label className={styles.check} key={item}><input type="checkbox" checked={!!checks[`${selectedTicker}:${item}`]} onChange={(event) => setChecks({ ...checks, [`${selectedTicker}:${item}`]: event.target.checked })} /><span>{item}</span></label>)}
    <button className={styles.primary} disabled={!checked || !selectedTicker} onClick={apply}>Apply checklist to ticket</button>{error ? <p role="alert" className={styles.error}>{error}</p> : null}
  </section>;
}

function FactAnnotation({ source, onAdd }: { source: SourceDocument; onAdd: (fact: ResearchFact) => void }) {
  const [kind, setKind] = useState<ResearchFact["kind"]>("metric");
  const [error, setError] = useState<string | null>(null);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const fields = new FormData(form);
    const value = (key: string) => String(fields.get(key) ?? "").trim();
    const quote = value("quote"); const start = source.text.indexOf(quote);
    try {
      if (!quote || start < 0) throw new Error("Passage must match the document text exactly.");
      if (source.text.indexOf(quote, start + 1) !== -1) throw new Error("Passage occurs more than once. Include surrounding text to identify one occurrence.");
      const metadata: Record<string, string> = {};
      for (const key of ["metric", "startDate", "endDate", "company", "stage", "investors"]) if (value(key)) metadata[key] = value(key);
      const fact: ResearchFact = { id: crypto.randomUUID(), documentId: source.id, kind, ticker: source.ticker, label: value("label"), quote, start, end: start + quote.length, metadata };
      if (value("period")) fact.period = value("period");
      if (value("value")) { fact.value = Number(value("value")); fact.unit = value("unit"); }
      onAdd(fact); form.reset(); setError(null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Fact could not be added."); }
  }
  return <details><summary>Annotate a cited fact</summary><form className={styles.form} onSubmit={submit}>
    <div className={styles.pair}><label className={styles.field}>Fact type<select value={kind} onChange={(event) => setKind(event.target.value as ResearchFact["kind"])}>{(["metric", "blackout", "earnings", "deal", "theme"] as const).map((item) => <option key={item}>{item}</option>)}</select></label><label className={styles.field}>Fact label<input name="label" required maxLength={500} /></label></div>
    <label className={styles.field}>Exact cited passage<textarea name="quote" required rows={4} maxLength={20000} /></label>
    {kind === "metric" || kind === "theme" || kind === "deal" ? <><div className={styles.pair}><label className={styles.field}>Period<input name="period" placeholder="2026-Q2" required={kind === "metric"} /></label><label className={styles.field}>Signed value<input name="value" type="number" step="any" required={kind === "metric"} /></label></div><label className={styles.field}>Unit<select name="unit">{["USD", "USD thousand", "USD million", "USD billion", "percent", "count"].map((unit) => <option key={unit}>{unit}</option>)}</select></label></> : null}
    {kind === "metric" ? <label className={styles.field}>Normalized metric<select name="metric">{["revenue", "net_income", "interest", "tax", "depreciation", "amortization", "exclusion", "adjusted_ebitda", "capex", "ai_spend", "guidance", "buybacks"].map((metric) => <option key={metric}>{metric}</option>)}</select></label> : null}
    {kind === "blackout" || kind === "earnings" ? <div className={styles.pair}><label className={styles.field}>Source-stated start date<input name="startDate" type="date" required /></label><label className={styles.field}>Source-stated end date<input name="endDate" type="date" required={kind === "blackout"} /></label></div> : null}
    {kind === "deal" ? <><label className={styles.field}>Company<input name="company" required /></label><label className={styles.field}>Round / stage<input name="stage" /></label><label className={styles.field}>Investors (semicolon separated)<input name="investors" /></label></> : null}
    {error ? <p className={styles.error} role="alert">{error}</p> : null}<button type="submit" className={styles.primary}>Add cited fact</button>
  </form></details>;
}

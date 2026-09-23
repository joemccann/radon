"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import ErrorToast from "@/components/ErrorToast";
import styles from "./slm-review.module.css";

type CandidateAlias = "Candidate 1" | "Candidate 2" | "Candidate 3";
type Item = {
  id: string;
  month: string;
  title: string;
  text: string;
  imageUrls: string[];
  candidates: Record<CandidateAlias, string[]>;
};
type Packet = { schema: "radon.slm-review.v1"; runId: string; taxonomy: string[]; items: Item[] };
type SavedDecision = {
  id: string;
  humanTags: string[];
  acceptance: Record<string, boolean>;
  reviewer: string;
  reviewedAt: string;
};
type SavedRun = { schema: "radon.slm-review-decisions.v1"; runId: string; reviewer: string; decisions: SavedDecision[] };

const ALIASES: CandidateAlias[] = ["Candidate 1", "Candidate 2", "Candidate 3"];
const scrubText = (value: unknown, limit = 20_000) => typeof value === "string"
  ? value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/g, " ").slice(0, limit)
  : "";

function imageHost(url: string): string {
  try { return new URL(url).hostname; } catch { return "external image host"; }
}

function safeImageUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password || (url.port && url.port !== "443")) return null;
    url.search = "";
    url.hash = "";
    return url.toString();
  } catch {
    return null;
  }
}

function parsePacket(value: unknown): Packet {
  if (!value || typeof value !== "object") throw new Error("Review packet is not an object.");
  const raw = value as Partial<Packet>;
  if (raw.schema !== "radon.slm-review.v1" || typeof raw.runId !== "string" || !Array.isArray(raw.taxonomy) || !Array.isArray(raw.items)) {
    throw new Error("Unsupported review packet. Choose a radon.slm-review.v1 JSON file.");
  }
  if (raw.items.length !== 200) throw new Error(`This review requires 200 items; packet has ${raw.items.length}.`);
  const taxonomy = raw.taxonomy.filter((tag): tag is string => typeof tag === "string");
  if (taxonomy.length === 0 || taxonomy.length > 2_000) throw new Error("Packet taxonomy is empty or too large.");
  const seen = new Set<string>();
  const items = raw.items.map((candidate, index): Item => {
    const row = candidate as Item;
    if (!row || typeof row.id !== "string" || seen.has(row.id)) throw new Error(`Invalid or repeated row ID at item ${index + 1}.`);
    seen.add(row.id);
    const predictions = row.candidates as Record<CandidateAlias, unknown>;
    const candidateKeys = predictions && typeof predictions === "object" ? Object.keys(predictions).sort() : [];
    if (candidateKeys.join("|") !== [...ALIASES].sort().join("|") || ALIASES.some((alias) => !Array.isArray(predictions[alias]) || predictions[alias].length > 3 || predictions[alias].some((tag) => typeof tag !== "string" || tag.length > 80))) {
      throw new Error(`Item ${index + 1} has malformed prediction tags.`);
    }
    return {
      id: row.id,
      month: scrubText(row.month, 16),
      title: scrubText(row.title, 500),
      text: scrubText(row.text, 20_000),
      imageUrls: Array.isArray(row.imageUrls) ? [...new Set(row.imageUrls.map(safeImageUrl).filter((url): url is string => Boolean(url)))].slice(0, 6) : [],
      candidates: predictions as Record<CandidateAlias, string[]>,
    };
  });
  if (items.filter((item) => item.imageUrls.length > 0).length < 40) throw new Error("Packet must include at least 40 image posts.");
  return { schema: raw.schema, runId: raw.runId, taxonomy, items };
}


function storageKey(runId: string, reviewer: string) {
  return `radon-slm-review:${runId}:${reviewer}`;
}

export default function SlmReview({ reviewer }: { reviewer: string }) {
  const [packet, setPacket] = useState<Packet | null>(null);
  const [decisions, setDecisions] = useState<Record<string, SavedDecision>>({});
  const [index, setIndex] = useState(0);
  const [tags, setTags] = useState(["", "", ""]);
  const [stage, setStage] = useState<"label" | "compare">("label");
  const [approvedImageHosts, setApprovedImageHosts] = useState<string[]>([]);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!packet) return;
    const saved: SavedRun = { schema: "radon.slm-review-decisions.v1", runId: packet.runId, reviewer, decisions: Object.values(decisions) };
    try { localStorage.setItem(storageKey(packet.runId, reviewer), JSON.stringify(saved)); }
    catch { setError("Browser storage is full. Export the review file now to preserve decisions."); }
  }, [decisions, packet, reviewer]);

  const item = packet?.items[index];
  const current = item ? decisions[item.id] : undefined;
  const completeCount = packet ? Object.values(decisions).filter((decision) => ALIASES.every((name) => name in decision.acceptance)).length : 0;
  const suggestionListId = useMemo(() => "slm-review-taxonomy", []);

  async function loadPacket(file?: File) {
    if (!file) return;
    setError(""); setNotice("");
    try {
      if (file.size > 15_000_000) throw new Error("Review packet is larger than the 15 MB safety limit.");
      const parsed = parsePacket(JSON.parse(await file.text()));
      const raw = localStorage.getItem(storageKey(parsed.runId, reviewer));
      const saved = raw ? JSON.parse(raw) as SavedRun : null;
      const validIds = new Set(parsed.items.map((item) => item.id));
      const prior = saved?.schema === "radon.slm-review-decisions.v1"
        ? saved.decisions.filter((decision) => validIds.has(decision.id))
        : [];
      setDecisions(Object.fromEntries(prior.map((decision) => [decision.id, decision])));
      setPacket(parsed); setIndex(0); setStage("label"); setTags(["", "", ""]); setApprovedImageHosts([]);
      setNotice(`Loaded ${parsed.items.length} blinded items. Source text stays in this tab; only your decisions are saved.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not read review packet.");
    }
    if (fileRef.current) fileRef.current.value = "";
  }

  function beginCompare() {
    if (!packet || !item) return;
    const normalized = tags.map((tag) => tag.trim());
    const known = new Set(packet.taxonomy.map((tag) => tag.toLowerCase()));
    if (normalized.some((tag) => !known.has(tag.toLowerCase())) || new Set(normalized.map((tag) => tag.toLowerCase())).size !== 3) {
      setError("Enter three different labels from the taxonomy before comparing."); return;
    }
    const existing = current ?? {
      id: item.id,
      humanTags: normalized,
      acceptance: {}, reviewer,
      reviewedAt: new Date().toISOString(),
    };
    setDecisions((previous) => ({ ...previous, [item.id]: { ...existing, humanTags: normalized, acceptance: {} } }));
    setStage("compare"); setError("");
  }

  function saveDecision(alias: string, accepted: boolean) {
    if (!item || !current) return;
    const acceptance = { ...current.acceptance, [alias]: accepted };
    const next = { ...current, acceptance, reviewedAt: new Date().toISOString() };
    setDecisions((previous) => ({ ...previous, [item.id]: next }));
    if (ALIASES.every((name) => name in acceptance)) {
      setNotice("All three candidates recorded. Move to the next item when ready.");
    }
  }

  function exportDecisions() {
    if (!packet) return;
    const saved: SavedRun = { schema: "radon.slm-review-decisions.v1", runId: packet.runId, reviewer, decisions: Object.values(decisions) };
    const blob = new Blob([JSON.stringify(saved, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = `slm-review-${packet.runId}.json`; anchor.click();
    URL.revokeObjectURL(url);
  }

  function step(next: number) {
    if (!packet) return;
    const bounded = Math.max(0, Math.min(packet.items.length - 1, next));
    setIndex(bounded);
    setApprovedImageHosts([]);
    const saved = decisions[packet.items[bounded].id];
    setTags(saved?.humanTags ?? ["", "", ""]);
    setStage(saved && saved.humanTags.length === 3 ? "compare" : "label");
    setNotice(""); setError("");
  }

  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>MODEL EVALUATION · HUMAN REVIEW</p>
          <h1>Label the post. Then judge the candidates.</h1>
        </div>
        <div className={styles.headerActions}>
          {packet && <button className={styles.secondary} onClick={exportDecisions} disabled={completeCount !== packet.items.length}>Export completed review</button>}
          <input ref={fileRef} className={styles.fileInput} type="file" accept="application/json,.json" aria-label="Load review packet" onChange={(event) => loadPacket(event.target.files?.[0])} />
          <button className={styles.primary} onClick={() => fileRef.current?.click()}>Load review packet</button>
        </div>
      </header>
      <div className={styles.privacy}>Operator only. Packet text stays in this tab. Images load from their source only when requested; decisions stay in this browser.</div>
      {error && <ErrorToast message={error} />}
      {notice && <p role="status" className={styles.notice}>{notice}</p>}
      {!packet || !item ? (
        <section className={styles.empty}>
          <h2>Choose the 200-item packet</h2>
          <p>Use the evaluation packet generated from the pinned holdout. It must contain at least 40 image posts and three aligned prediction sets.</p>
          <button className={styles.primary} onClick={() => fileRef.current?.click()}>Choose JSON file</button>
        </section>
      ) : (
        <>
          <div className={styles.progress}>
            <span className={styles.progressLabel}>ITEM <strong>{index + 1}</strong> / {packet.items.length}</span>
            <progress value={completeCount} max={packet.items.length} aria-label="Review progress" />
            <span>{completeCount} / {packet.items.length} reviewed · {item.month || "date unavailable"}</span>
          </div>
          <section className={styles.review}>
            <article className={styles.post}>
              <div className={styles.postHead}>
                <span>POST {item.id}</span>
                {item.imageUrls.length > 0 && <span className={styles.imageFlag}>IMAGE POST</span>}
              </div>
              <h2>{item.title || "Untitled post"}</h2>
              <p className={styles.body}>{item.text}</p>
              {item.imageUrls.length > 0 && <div className={styles.imageReview}>
                <p className={styles.help}>Images stay unloaded until you approve each source host.</p>
                {[...new Set(item.imageUrls.map(imageHost))].map((host) => {
                  const hostImages = item.imageUrls.filter((url) => imageHost(url) === host);
                  const approved = approvedImageHosts.includes(host);
                  return <div className={styles.imageSource} key={host}>
                    {!approved
                      ? <button className={styles.secondary} onClick={() => setApprovedImageHosts((currentHosts) => [...currentHosts, host])}>Load {hostImages.length} {hostImages.length === 1 ? "image" : "images"} from {host}</button>
                      : <div className={styles.images}>{hostImages.map((url, imageIndex) => <img key={`${url}-${imageIndex}`} src={url} alt={`Post image from ${host}, ${imageIndex + 1}`} referrerPolicy="no-referrer" loading="lazy" decoding="async" />)}</div>}
                  </div>;
                })}
              </div>}
            </article>

            <section className={styles.labelPanel} aria-labelledby="human-label-heading">
              <div className={styles.panelTitle}>
                <div><p className={styles.eyebrow}>BLIND LABEL</p><h2 id="human-label-heading">What are the three best tags?</h2></div>
                <span className={styles.counter}>{current?.humanTags.join(" · ") || "NOT LABELED"}</span>
              </div>
              <p className={styles.help}>Set your labels before seeing model output.</p>
              <datalist id={suggestionListId}>{packet.taxonomy.map((tag) => <option key={tag} value={tag} />)}</datalist>
              <div className={styles.tagFields}>
                {tags.map((tag, fieldIndex) => <label key={fieldIndex}>
                  <span>TAG {fieldIndex + 1}</span>
                  <input list={suggestionListId} value={tag} onChange={(event) => setTags((old) => old.map((value, i) => i === fieldIndex ? event.target.value : value))} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); if (fieldIndex < 2) (event.currentTarget.parentElement?.parentElement?.querySelectorAll("input").item(fieldIndex + 1) as HTMLInputElement | null)?.focus(); else beginCompare(); } }} autoComplete="off" />
                </label>)}
              </div>
              {stage === "label" ? <button className={styles.primary} onClick={beginCompare}>Lock labels and compare</button> : (
                <div className={styles.candidates}>
                  <div className={styles.panelTitle}><div><p className={styles.eyebrow}>BLINDED CANDIDATES</p><h2>Would you accept these tags?</h2></div><span className={styles.help}>Choose before revealing model identity.</span></div>
                  <button className={styles.secondary} onClick={() => { setTags(current?.humanTags ?? tags); setStage("label"); }}>Edit human labels</button>
                  {ALIASES.map((alias) => {
                    const selected = current?.acceptance[alias];
                    return <div className={styles.candidate} key={alias}>
                      <span>{alias}</span><strong>{item.candidates[alias].join(" · ") || "Invalid or empty output"}</strong>
                      <div className={styles.vote}>
                        <button aria-pressed={selected === true} onClick={() => saveDecision(alias, true)}>Accept</button>
                        <button aria-pressed={selected === false} onClick={() => saveDecision(alias, false)}>Reject</button>
                      </div>
                    </div>;
                  })}
                  {current && ALIASES.every((name) => name in current.acceptance) && <p className={styles.reveal}>Recorded. Candidate identity remains hidden until review export is joined to the prediction files.</p>}
                </div>
              )}
            </section>
          </section>
          <nav className={styles.navigation} aria-label="Review navigation">
            <button className={styles.secondary} onClick={() => step(index - 1)} disabled={index === 0}>Previous</button>
            <button className={styles.secondary} onClick={() => step(index + 1)} disabled={index === packet.items.length - 1}>Skip / next</button>
          </nav>
          <p className={styles.storageNote}>Progress is saved in this browser. Export decisions before clearing site data or changing browsers.</p>
        </>
      )}
    </div>
  );
}

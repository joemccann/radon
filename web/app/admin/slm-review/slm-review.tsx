"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import ErrorToast from "@/components/ErrorToast";
import { loadReviewPacket, saveReviewPacket } from "./packetStorage";
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
type SavedRun = { schema: "radon.slm-review-decisions.v1"; runId: string; reviewer: string; decisions: SavedDecision[]; index?: number; draftTags?: string[] };

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

function nextReviewTime(previous?: string): string {
  return new Date(Math.max(Date.now(), (Date.parse(previous ?? "") || 0) + 1)).toISOString();
}

function readLocalRun(runId: string, reviewer: string): SavedRun | null {
  try {
    const raw = localStorage.getItem(storageKey(runId, reviewer));
    const saved = raw ? JSON.parse(raw) as SavedRun : null;
    return saved?.schema === "radon.slm-review-decisions.v1" && saved.runId === runId && saved.reviewer === reviewer && Array.isArray(saved.decisions) ? saved : null;
  } catch {
    return null;
  }
}

function mergeDecisions(items: Item[], ...runs: Array<SavedRun | null>): Record<string, SavedDecision> {
  const validIds = new Set(items.map((item) => item.id));
  const merged: Record<string, SavedDecision> = {};
  for (const run of runs) {
    for (const decision of run?.decisions ?? []) {
      if (!decision || !validIds.has(decision.id) || !Array.isArray(decision.humanTags) || !decision.acceptance || typeof decision.acceptance !== "object") continue;
      const prior = merged[decision.id];
      if (!prior || (Date.parse(decision.reviewedAt) || 0) >= (Date.parse(prior.reviewedAt) || 0)) merged[decision.id] = decision;
    }
  }
  return merged;
}

export default function SlmReview({ reviewer }: { reviewer: string }) {
  const [packet, setPacket] = useState<Packet | null>(null);
  const [decisions, setDecisions] = useState<Record<string, SavedDecision>>({});
  const [index, setIndex] = useState(0);
  const [tags, setTags] = useState(["", "", ""]);
  const [stage, setStage] = useState<"label" | "compare">("label");
  const [restoring, setRestoring] = useState(true);
  const [serverReady, setServerReady] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"saved" | "saving" | "offline">("saved");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const syncQueue = useRef(Promise.resolve());

  useEffect(() => {
    let active = true;
    loadReviewPacket(reviewer).then((stored) => {
      if (active && stored) return restorePacket(parsePacket(stored), false);
    }).catch(() => {
      if (active) setNotice("Saved packet unavailable. Load the JSON file to resume your saved decisions.");
    }).finally(() => { if (active) setRestoring(false); });
    return () => { active = false; };
  // The reviewer identity is fixed for this page session.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reviewer]);

  async function restorePacket(parsed: Packet, persistPacket: boolean) {
    let packetSaved = true;
    if (persistPacket) {
      try { await saveReviewPacket(reviewer, parsed); }
      catch { packetSaved = false; }
    }
    const local = readLocalRun(parsed.runId, reviewer);
    let remote: SavedRun | null = null;
    try {
      const response = await fetch(`/api/admin/slm-review?runId=${encodeURIComponent(parsed.runId)}`, { cache: "no-store" });
      if (!response.ok) throw new Error("Review backup unavailable.");
      remote = await response.json() as SavedRun;
      if (remote.schema !== "radon.slm-review-decisions.v1" || remote.runId !== parsed.runId || remote.reviewer !== reviewer || !Array.isArray(remote.decisions)) throw new Error("Invalid saved review response.");
      setServerReady(true);
    } catch {
      setServerReady(false);
      setSaveStatus("offline");
    }
    const restored = mergeDecisions(parsed.items, remote, local);
    const resumeIndex = Math.max(0, Math.min(parsed.items.length - 1, local?.index ?? remote?.index ?? 0));
    const selected = restored[parsed.items[resumeIndex].id];
    setDecisions(restored);
    setPacket(parsed);
    setIndex(resumeIndex);
    const draft = local?.index === resumeIndex && Array.isArray(local.draftTags) ? local.draftTags : [];
    setTags(selected?.humanTags ?? [0, 1, 2].map((i) => typeof draft[i] === "string" ? draft[i] : ""));
    setStage(selected?.humanTags.length === 3 ? "compare" : "label");
    setNotice(packetSaved
      ? `Resumed ${Object.keys(restored).length} labeled items in ${parsed.runId}.`
      : "Could not keep the packet in this browser. Keep the JSON file for your next visit; decisions are still saved.");
  }

  useEffect(() => {
    if (!packet) return;
    const saved: SavedRun = { schema: "radon.slm-review-decisions.v1", runId: packet.runId, reviewer, decisions: Object.values(decisions), index, draftTags: tags };
    try { localStorage.setItem(storageKey(packet.runId, reviewer), JSON.stringify(saved)); }
    catch { setError("Browser storage is full. Export the review file now to preserve decisions."); }
  }, [decisions, packet, reviewer, index, tags]);

  useEffect(() => {
    if (!packet || !serverReady) return;
    const snapshot: SavedRun = { schema: "radon.slm-review-decisions.v1", runId: packet.runId, reviewer, decisions: Object.values(decisions), index };
    setSaveStatus("saving");
    syncQueue.current = syncQueue.current.catch(() => undefined).then(async () => {
      const response = await fetch(`/api/admin/slm-review?runId=${encodeURIComponent(packet.runId)}`, {
        method: "PUT", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(snapshot),
      });
      if (!response.ok) throw new Error("Review backup failed.");
      setSaveStatus("saved");
    }).catch(() => { setSaveStatus("offline"); });
  }, [decisions, packet, reviewer, index, serverReady]);

  const item = packet?.items[index];
  const current = item ? decisions[item.id] : undefined;
  const completeCount = packet ? Object.values(decisions).filter((decision) => ALIASES.every((name) => name in decision.acceptance)).length : 0;
  const suggestionListId = useMemo(() => "slm-review-taxonomy", []);

  function saveLocalNow(nextDecisions: Record<string, SavedDecision>, nextIndex = index, draftTags = tags) {
    if (!packet) return;
    const saved: SavedRun = { schema: "radon.slm-review-decisions.v1", runId: packet.runId, reviewer, decisions: Object.values(nextDecisions), index: nextIndex, draftTags };
    try { localStorage.setItem(storageKey(packet.runId, reviewer), JSON.stringify(saved)); }
    catch { setError("Browser storage is full. Export the review file now to preserve decisions."); }
  }

  async function loadPacket(file?: File) {
    if (!file) return;
    setError(""); setNotice("");
    try {
      if (file.size > 15_000_000) throw new Error("Review packet is larger than the 15 MB safety limit.");
      const parsed = parsePacket(JSON.parse(await file.text()));
      await restorePacket(parsed, true);
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
      reviewedAt: nextReviewTime(),
    };
    const unchanged = existing.humanTags.length === 3 && existing.humanTags.every((tag, i) => tag === normalized[i]);
    const updated = { ...existing, humanTags: normalized, acceptance: unchanged ? existing.acceptance : {}, reviewedAt: nextReviewTime(existing.reviewedAt) };
    const next = { ...decisions, [item.id]: updated };
    saveLocalNow(next, index, normalized);
    setDecisions(next);
    setStage("compare"); setError("");
  }

  function saveDecision(alias: string, accepted: boolean) {
    if (!item || !current) return;
    const acceptance = { ...current.acceptance, [alias]: accepted };
    const updated = { ...current, acceptance, reviewedAt: nextReviewTime(current.reviewedAt) };
    const next = { ...decisions, [item.id]: updated };
    saveLocalNow(next);
    setDecisions(next);
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

  async function retryBackup() {
    if (!packet) return;
    try {
      const response = await fetch(`/api/admin/slm-review?runId=${encodeURIComponent(packet.runId)}`, { cache: "no-store" });
      if (!response.ok) throw new Error("Review backup unavailable.");
      const remote = await response.json() as SavedRun;
      if (remote.schema !== "radon.slm-review-decisions.v1" || remote.runId !== packet.runId || remote.reviewer !== reviewer || !Array.isArray(remote.decisions)) throw new Error("Invalid saved review response.");
      const local: SavedRun = { schema: "radon.slm-review-decisions.v1", runId: packet.runId, reviewer, decisions: Object.values(decisions) };
      setDecisions(mergeDecisions(packet.items, remote, local));
      setServerReady(true);
      setSaveStatus("saving");
      setError("");
    } catch {
      setSaveStatus("offline");
      setError("Review backup is unavailable. Decisions remain in this browser; export a copy or retry.");
    }
  }

  function step(next: number) {
    if (!packet) return;
    const bounded = Math.max(0, Math.min(packet.items.length - 1, next));
    const saved = decisions[packet.items[bounded].id];
    const nextTags = saved?.humanTags ?? ["", "", ""];
    saveLocalNow(decisions, bounded, nextTags);
    setIndex(bounded);
    setTags(nextTags);
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
          {packet && <button className={styles.secondary} onClick={exportDecisions}>Export decisions</button>}
          <input ref={fileRef} className={styles.fileInput} type="file" accept="application/json,.json" aria-label="Load review packet" onChange={(event) => loadPacket(event.target.files?.[0])} />
          <button className={styles.primary} onClick={() => fileRef.current?.click()}>Load review packet</button>
        </div>
      </header>
      <div className={styles.privacy}>Operator only. The packet is kept in this browser for automatic resume. Decisions are backed up to Radon. Images load from their source.</div>
      {error && <ErrorToast message={error} />}
      {notice && <p role="status" className={styles.notice}>{notice}</p>}
      {restoring ? <p role="status" className={styles.notice}>Restoring review…</p> : !packet || !item ? (
        <section className={styles.empty}>
          <h2>Choose the 200-item packet</h2>
          <p>Load the packet for the frozen evaluation cohort. It contains 200 posts, at least 40 with images, and three aligned prediction sets.</p>
          <button className={styles.primary} onClick={() => fileRef.current?.click()}>Choose JSON file</button>
        </section>
      ) : (
        <>
          <div className={styles.progress}>
            <span className={styles.progressLabel}>ITEM <strong>{index + 1}</strong> / {packet.items.length}</span>
            <progress value={completeCount} max={packet.items.length} aria-label="Review progress" />
            <span>{completeCount} / {packet.items.length} reviewed · {item.month || "date unavailable"} · {saveStatus === "saved" ? "Backed up" : saveStatus === "saving" ? "Saving…" : <button className={styles.retry} onClick={retryBackup}>Backup unavailable · Retry</button>}</span>
          </div>
          <section className={styles.review}>
            <article className={styles.post}>
              <div className={styles.postHead}>
                <span>POST {item.id}</span>
                {item.imageUrls.length > 0 && <span className={styles.imageFlag}>IMAGE POST</span>}
              </div>
              <h2>{item.title || "Untitled post"}</h2>
              <p className={styles.body}>{item.text}</p>
              {item.imageUrls.length > 0 && <div className={styles.images}>
                {item.imageUrls.map((url, imageIndex) => <img key={url} src={url} alt={`Post image ${imageIndex + 1} from ${imageHost(url)}`} referrerPolicy="no-referrer" loading="eager" decoding="async" />)}
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
          <p className={styles.storageNote}>Your work resumes automatically in this browser. Export decisions at any time for an additional copy.</p>
        </>
      )}
    </div>
  );
}
